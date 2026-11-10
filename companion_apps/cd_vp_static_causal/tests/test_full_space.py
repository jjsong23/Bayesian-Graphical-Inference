from __future__ import annotations

import math
import csv
import gzip
import json
import shutil
import unittest
from pathlib import Path

import numpy as np

from static_causal.full_space import (
    FullGraph,
    FullRegistryIndex,
    construct_full_graph,
    coverage_seed_batches,
    graph_structural_score,
    rank_map_preferences,
)
from static_causal.merge_full_space_shards import merge_shards


def small_index() -> FullRegistryIndex:
    names = ["A", "B", "C", "PRKACA"]
    edge_a = ["PRKACA", "A", "B", "C", "A"]
    edge_b = ["A", "B", "C", "PRKACA", "C"]
    node_p = np.array([0.1, 0.2, 0.3, 0.4])
    edge_p = np.array([0.05, 0.1, 0.15, 0.2, 0.25])
    adjacency = {
        "A": np.array([0, 1, 4], dtype=np.int32),
        "B": np.array([1, 2], dtype=np.int32),
        "C": np.array([2, 3, 4], dtype=np.int32),
        "PRKACA": np.array([0, 3], dtype=np.int32),
    }
    alpha_n, alpha_e = 0.35, 0.65
    return FullRegistryIndex(
        names, node_p.copy(), node_p, {name: i for i, name in enumerate(names)}, edge_a, edge_b,
        edge_p.copy(), edge_p, np.argsort(edge_p), np.cumsum(edge_p) / edge_p.sum(), adjacency,
        {"A": 0, "C": 3},
        alpha_n * np.log1p(-node_p).sum() + alpha_e * np.log1p(-edge_p).sum(),
        np.log(node_p / (1 - node_p)), np.log(edge_p / (1 - edge_p)), alpha_n, alpha_e, 0.03,
    )


class MappingTests(unittest.TestCase):
    def test_rank_mapping_is_monotone_and_hits_sparse_mean(self):
        values = np.array([0.2, 0.9, 0.5, 0.1, 0.7])
        mapped = rank_map_preferences(values, 0.03, temperature=1.0, epsilon=1e-8)
        self.assertAlmostEqual(float(mapped.mean()), 0.03, places=6)
        self.assertEqual(list(np.argsort(values)), list(np.argsort(mapped)))

    def test_sparse_structural_score_matches_dense_formula(self):
        index = small_index()
        graph = FullGraph("G", frozenset({"A", "PRKACA"}), ((0, "a_to_b", "activation", "regulatory"),), "test", 1)
        config = {
            "direction_weights": {"a_to_b": 1.0, "b_to_a": 1.0, "both": 1.0, "unknown": 1.0},
            "sign_weights": {"activation": 1.0, "inhibition": 1.0, "unknown": 1.0},
            "mechanism_weights": {"regulatory": 1.0, "phosphorylation": 1.0, "physical_unspecified": 1.0, "unknown": 1.0},
        }
        observed = graph_structural_score(graph, index, config)["structural_preference"]
        dense_nodes = sum(math.log(p if name in graph.nodes else 1 - p) for name, p in zip(index.node_names, index.node_preference)) * index.alpha_node
        dense_edges = sum(math.log(p if i == 0 else 1 - p) for i, p in enumerate(index.edge_preference)) * index.alpha_edge
        expected = dense_nodes + dense_edges - index.complexity_penalty * 3
        self.assertAlmostEqual(observed, expected, places=10)


class ShardAndGenerationTests(unittest.TestCase):
    def test_shard_scan_partitions_edges_without_loss(self):
        index = small_index()
        covered = []
        for shard in range(2):
            config = {"coverage_scan": {"enabled": True, "shard_count": 2, "shard_index": shard, "seed_edges_per_graph": 2, "passes": 1, "seed": 3}}
            covered.extend(edge for batch in coverage_seed_batches(index, config) for edge in batch)
        self.assertEqual(sorted(covered), list(range(index.edge_count)))

    def test_frontier_generation_preserves_endpoints(self):
        index = small_index()
        graph = construct_full_graph(
            np.random.default_rng(5), index,
            {"anchor_gene": "PRKACA", "min_graph_edges": 3, "max_graph_edges": 4, "max_graph_nodes": 4},
            "uniform_edge_frontier", 5, 1,
        )
        for edge_index, *_ in graph.edge_states:
            self.assertIn(index.edge_a[edge_index], graph.nodes)
            self.assertIn(index.edge_b[edge_index], graph.nodes)

    def test_sparse_shards_merge_to_complete_coverage(self):
        root = Path.cwd() / "runs" / "_test_merge_shards"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        try:
            inputs = []
            fields = ["edge_id", "node_a", "node_b", "source_score", "mapped_preference", "confidence_quartile", "graphs_containing"]
            for shard in range(2):
                directory = root / f"shard_{shard}"
                directory.mkdir()
                inputs.append(directory)
                (directory / "full_space_manifest.json").write_text(json.dumps({"full_registry_edge_count": 2, "coverage_ledger": "shard_edge_coverage.tsv.gz"}))
                (directory / "full_space_resolved_configuration.json").write_text(json.dumps({"full_space": {"coverage_scan": {"shard_index": shard, "shard_count": 2}}}))
                with gzip.open(directory / "shard_edge_coverage.tsv.gz", "wt", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
                    writer.writeheader()
                    writer.writerow({"edge_id": f"FE{shard + 1:07d}", "node_a": "A", "node_b": "B", "source_score": 0.5, "mapped_preference": 0.1, "confidence_quartile": shard + 1, "graphs_containing": 1})
                (directory / "full_node_coverage.tsv").write_text("node\tgraphs_containing\nA\t1\n")
                (directory / "full_graph_rankings.tsv").write_text("graph_id\tstructural_preference\nFG0000001\t-1\n")
            summary = merge_shards(inputs, root / "merged")
            self.assertTrue(summary["complete_single_pass"])
            self.assertEqual(summary["covered_edge_count"], 2)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
