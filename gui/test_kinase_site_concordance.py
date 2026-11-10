"""Regression tests for response-conditioned kinase/substrate evidence."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from kinase_site_concordance import apply_site_response_gate


class KinaseSiteConcordanceTests(unittest.TestCase):
    def test_exact_site_response_shrinks_motif_factor_toward_neutral(self) -> None:
        predictions = pd.DataFrame(
            [
                {
                    "kinase_node": "Prkaca",
                    "target_protein": "Aqp2",
                    "target_site": "S256",
                    "position": 256,
                },
                {
                    "kinase_node": "Prkaca",
                    "target_protein": "Aqp2",
                    "target_site": "S269",
                    "position": 269,
                },
            ]
        )
        lookup = {
            ("aqp2", 256): {
                "combined_response_reliability": 0.75,
                "datasets": [{"dataset": "kirby_ddavp_timecourse"}],
            }
        }
        with patch("kinase_site_concordance.response_lookup", return_value=lookup):
            result = apply_site_response_gate(
                predictions,
                ".",
                np.asarray([2.0, 1.8]),
            )
        self.assertAlmostEqual(result.loc[0, "response_gated_bayes_factor"], 1.75)
        self.assertAlmostEqual(result.loc[1, "response_gated_bayes_factor"], 1.0)
        self.assertEqual(result.loc[0, "site_response_status"], "responsive_exact_site")
        self.assertEqual(
            result.loc[1, "site_response_status"],
            "site_not_measured_in_response_datasets",
        )

    def test_response_at_a_different_site_does_not_support_the_edge(self) -> None:
        predictions = pd.DataFrame(
            [
                {
                    "kinase_node": "Prkaca",
                    "target_protein": "Aqp2",
                    "target_site": "S269",
                    "position": 269,
                }
            ]
        )
        lookup = {
            ("aqp2", 256): {
                "combined_response_reliability": 1.0,
                "datasets": [{"dataset": "kirby_ddavp_timecourse"}],
            }
        }
        with patch("kinase_site_concordance.response_lookup", return_value=lookup):
            result = apply_site_response_gate(
                predictions,
                ".",
                np.asarray([2.0]),
            )
        self.assertEqual(result.loc[0, "response_gated_bayes_factor"], 1.0)
        self.assertEqual(result.loc[0, "site_response_reliability"], 0.0)


if __name__ == "__main__":
    unittest.main()
