from __future__ import annotations

import unittest

import numpy as np

from static_causal.exploration import (
    CandidateEdge,
    ExplorationGraph,
    TypedEdge,
    _has_cycle,
    coverage_rows,
    fit_response_model,
    generate_bank,
)


class GraphIdentityTests(unittest.TestCase):
    def test_identity_includes_direction_sign_and_mechanism(self):
        base = dict(candidate_id="E1", node_a="A", node_b="B")
        first = ExplorationGraph("G1", frozenset({"A", "B"}), (TypedEdge(**base, direction="a_to_b", sign="activation", mechanism="regulatory"),), "test", 1)
        reversed_graph = ExplorationGraph("G2", frozenset({"A", "B"}), (TypedEdge(**base, direction="b_to_a", sign="activation", mechanism="regulatory"),), "test", 1)
        inhibited = ExplorationGraph("G3", frozenset({"A", "B"}), (TypedEdge(**base, direction="a_to_b", sign="inhibition", mechanism="regulatory"),), "test", 1)
        physical = ExplorationGraph("G4", frozenset({"A", "B"}), (TypedEdge(**base, direction="a_to_b", sign="activation", mechanism="physical_unspecified"),), "test", 1)
        self.assertEqual(len({first.digest, reversed_graph.digest, inhibited.digest, physical.digest}), 4)

    def test_reciprocal_edge_is_two_arrows_and_retains_cycle(self):
        edge = TypedEdge("E1", "A", "B", "both", "unknown", "regulatory")
        graph = ExplorationGraph("G1", frozenset({"A", "B"}), (edge,), "test", 1)
        self.assertEqual(len(edge.arrows()), 2)
        self.assertTrue(_has_cycle(graph))


class ExplorationTests(unittest.TestCase):
    def setUp(self):
        self.nodes = {"PRKACA": 0.8, "A": 0.1, "B": 0.2, "C": 0.5, "D": 0.9, "E": 0.4}
        pairs = [("PRKACA", "A"), ("A", "B"), ("B", "C"), ("C", "D"), ("D", "E"), ("E", "PRKACA"), ("A", "D"), ("B", "E")]
        self.edges = [CandidateEdge(f"E{i}", a, b, 0.1 + i * 0.1, 0.1 + i * 0.1) for i, (a, b) in enumerate(pairs)]
        self.config = {"target_graph_count": 120, "low_confidence_coverage_target": 5, "coverage_repair_graph_limit": 200, "seeds": [7, 11], "checkpoint_every": 50}

    def test_generation_is_reproducible_and_endpoint_consistent(self):
        first, _ = generate_bank(self.nodes, self.edges, self.config)
        second, _ = generate_bank(self.nodes, self.edges, self.config)
        self.assertEqual([graph.digest for graph in first], [graph.digest for graph in second])
        for graph in first:
            for edge in graph.edges:
                self.assertIn(edge.node_a, graph.nodes)
                self.assertIn(edge.node_b, graph.nodes)

    def test_low_quartile_coverage_is_measured(self):
        bank, _ = generate_bank(self.nodes, self.edges, self.config)
        node_rows, edge_rows = coverage_rows(bank, self.nodes, self.edges, 5)
        self.assertTrue(all(row["target_met"] for row in node_rows if row["confidence_quartile"] == 1))
        self.assertTrue(all(row["target_met"] for row in edge_rows if row["confidence_quartile"] == 1))


class DynamicSurrogateTests(unittest.TestCase):
    def test_low_prior_parent_can_improve_synthetic_fit(self):
        times = np.array([1.0, 2.0, 5.0, 15.0])
        parent_mean = np.array([0.0, 0.4, 0.9, 0.3])
        parent = np.repeat(parent_mean[:, None], 3, axis=1)
        target_mean = np.array([0.0, 0.1, 0.8, 0.5])
        target = np.repeat(target_mean[:, None], 3, axis=1)
        beta_without, _, _ = fit_response_model(times, target, [], [], 0.01)
        beta_with, _, _ = fit_response_model(times, target, [parent], ["activation"], 0.01)
        derivative = np.gradient(target_mean, times)
        baseline = float(np.mean((derivative - (beta_without[0] - beta_without[1] * target_mean)) ** 2))
        supported = float(np.mean((derivative - (beta_with[0] - beta_with[1] * target_mean + beta_with[2] * parent_mean)) ** 2))
        self.assertLessEqual(supported, baseline)

    def test_missing_time_mean_fails_honestly(self):
        times = np.array([1.0, 2.0, 5.0, 15.0])
        target = np.array([[0.0, 0.1, 0.2], [np.nan] * 3, [0.4, 0.5, 0.6], [0.7, 0.8, 0.9]])
        with self.assertRaises(ValueError):
            fit_response_model(times, target, [], [], 1.0)


if __name__ == "__main__":
    unittest.main()
