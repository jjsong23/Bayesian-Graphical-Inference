"""Tests for descriptive full-graph statistics."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from full_graph_statistics import (
    build_threshold_graph,
    compute_full_graph_statistics,
    compute_path_union_node_statistics,
    top_node_statistics,
)


class FullGraphStatisticsTests(unittest.TestCase):
    def setUp(self) -> None:
        symbols = ["A", "B", "C", "D"]
        values = np.zeros((4, 4), dtype=float)
        for left, right, probability in (
            (0, 1, 0.9),
            (1, 2, 0.8),
            (2, 0, 0.7),
            (2, 3, 0.95),
        ):
            values[left, right] = values[right, left] = probability
        self.matrix = pd.DataFrame(values, index=symbols, columns=symbols)
        self.metadata = pd.DataFrame(
            {
                "symbol": symbols,
                "name": ["alpha", "beta", "gamma", "delta"],
                "classes": ["kinase"] * 4,
                "node_type": ["protein"] * 4,
            }
        )

    def test_threshold_is_strict_and_pairs_are_unique(self) -> None:
        graph = build_threshold_graph(self.matrix, 0.8)
        self.assertEqual(graph.number_of_edges(), 2)
        self.assertEqual(set(graph.edges()), {("A", "B"), ("C", "D")})

    def test_node_and_global_statistics_are_auditable(self) -> None:
        nodes, summary, robustness = compute_full_graph_statistics(
            self.matrix,
            self.metadata,
            cutoff=0.5,
            metrics=(
                "degree_strength",
                "clustering",
                "betweenness",
                "closeness_harmonic",
                "coreness",
                "connectivity",
                "communities",
                "robustness",
            ),
        )
        by_symbol = nodes.set_index("symbol")
        self.assertEqual(int(by_symbol.at["C", "degree"]), 3)
        self.assertAlmostEqual(float(by_symbol.at["A", "posterior_strength"]), 1.6)
        self.assertAlmostEqual(float(by_symbol.at["A", "local_clustering_coefficient"]), 1.0)
        self.assertTrue(bool(by_symbol.at["C", "articulation_point"]))
        self.assertGreater(float(by_symbol.at["C", "betweenness_centrality"]), 0.0)
        self.assertEqual(summary["node_count"], 4)
        self.assertEqual(summary["edge_count"], 4)
        self.assertEqual(summary["connected_component_count"], 1)
        self.assertEqual(summary["largest_component_diameter_unweighted"], 2)
        self.assertEqual(len(robustness), 4)
        preview = top_node_statistics(nodes, limit=2)
        self.assertIn("degree", preview["top_nodes_by_metric"])
        self.assertEqual(preview["top_nodes_by_metric"]["degree"][0]["symbol"], "C")

    def test_path_union_uses_only_edges_that_occur_in_returned_paths(self) -> None:
        paths = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "path_symbols": "A -> B -> D",
                    "primary_path_score": 0.9,
                },
                {
                    "rank": 2,
                    "path_symbols": "A -> C -> D",
                    "primary_path_score": 0.8,
                },
            ]
        )
        edges = pd.DataFrame(
            [
                {"source_symbol": "A", "target_symbol": "B", "edge_probability": 0.9},
                {"source_symbol": "B", "target_symbol": "D", "edge_probability": 0.9},
                {"source_symbol": "A", "target_symbol": "C", "edge_probability": 0.8},
                {"source_symbol": "C", "target_symbol": "D", "edge_probability": 0.8},
            ]
        )
        nodes, summary = compute_path_union_node_statistics(
            paths,
            edges,
            self.metadata,
        )
        by_symbol = nodes.set_index("symbol")
        self.assertEqual(summary["returned_path_count"], 2)
        self.assertEqual(summary["unique_undirected_path_edge_count"], 4)
        self.assertEqual(summary["directed_transition_count"], 4)
        self.assertEqual(summary["bidirectional_pair_count"], 0)
        self.assertEqual(int(by_symbol.at["A", "path_participation_count"]), 2)
        self.assertEqual(int(by_symbol.at["B", "internal_path_count"]), 1)
        self.assertEqual(int(by_symbol.at["A", "path_union_out_degree"]), 2)
        self.assertEqual(int(by_symbol.at["D", "path_union_in_degree"]), 2)
        self.assertEqual(int(by_symbol.at["A", "degree"]), 2)
        self.assertAlmostEqual(
            float(by_symbol.at["A", "path_participation_fraction"]), 1.0
        )


if __name__ == "__main__":
    unittest.main()
