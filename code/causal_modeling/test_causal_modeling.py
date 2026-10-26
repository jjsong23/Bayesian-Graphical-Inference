from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from causal_modeling.candidates import CandidateGraph, _contract_paths
from causal_modeling.model import fit_dynamic_model, predict_from_matrices


class CandidateTests(unittest.TestCase):
    def test_contracts_unmeasured_nodes_without_reversing_path(self) -> None:
        frame = pd.DataFrame(
            [
                {
                    "rank": 1,
                    "path_symbols": "Source -> Hidden -> A -> Hidden2 -> Target",
                    "edge_probabilities": "0.9;0.8;0.7;0.6",
                }
            ]
        )
        edges, inputs, _, segments = _contract_paths(
            frame, ("A", "TARGET"), "SOURCE", 1
        )
        self.assertEqual(inputs, {"A"})
        self.assertEqual(edges, {("A", "TARGET")})
        self.assertIn("SOURCE -> HIDDEN -> A", segments["INPUT->A"])


class DynamicModelTests(unittest.TestCase):
    def test_directed_coupling_improves_noiseless_fit(self) -> None:
        times = np.asarray([1.0, 2.0, 5.0, 15.0])
        states = ("A", "B")
        true_matrix = np.asarray([[-0.7, 0.0], [1.2, -0.25]])
        true_input = np.asarray([1.0, 0.0])
        mean = predict_from_matrices(true_matrix, true_input, times)
        observations = np.repeat(mean[:, None, :], 3, axis=1)
        linked = CandidateGraph(
            graph_id="linked",
            states=states,
            edges=(("A", "B"),),
            input_targets=("A",),
            source_symbol="INPUT",
            target_symbol="B",
            construction="test",
        )
        independent = CandidateGraph(
            graph_id="independent",
            states=states,
            edges=(),
            input_targets=("A",),
            source_symbol="INPUT",
            target_symbol="B",
            construction="test",
        )
        linked_fit = fit_dynamic_model(linked, times, observations, starts=2, max_nfev=500, ridge=1e-6)
        independent_fit = fit_dynamic_model(independent, times, observations, starts=2, max_nfev=500, ridge=1e-6)
        self.assertLess(linked_fit.objective, independent_fit.objective * 0.05)


if __name__ == "__main__":
    unittest.main()
