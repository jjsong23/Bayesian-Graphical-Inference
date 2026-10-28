"""Regression tests for the versioned literature-curated edge references."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from workflow_engine import (
    CURATED_EDGE_LIKELIHOOD_TRAINING_RELATIVE,
    EDGE_LIKELIHOOD_PROFILES,
    PROJECT_ROOT,
    apply_empirical_likelihood_model_array,
    combine_edge_factors,
    curated_edge_likelihood_models,
    default_configuration,
    load_registry,
)


class CuratedLikelihoodReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = PROJECT_ROOT / CURATED_EDGE_LIKELIHOOD_TRAINING_RELATIVE
        cls.training = pd.read_csv(cls.path, sep="\t")

    def test_expected_profile_counts_and_no_class_overlap(self) -> None:
        expected = {
            "physical_proximity": (78, 390),
            "physical_binding": (78, 390),
            "directed_phosphorylation": (88, 22),
            "regulatory_signaling": (48, 240),
            "messenger_binding": (3, 3),
        }
        references = self.training.drop_duplicates(
            ["profile", "node_a", "node_b", "class_label"]
        )
        for profile, (h1_count, h0_count) in expected.items():
            selected = references.loc[references["profile"].eq(profile)]
            self.assertEqual(
                int(selected["class_label"].str.startswith("H1").sum()), h1_count
            )
            self.assertEqual(
                int(selected["class_label"].str.startswith("H0").sum()), h0_count
            )
            h1_pairs = set(
                map(
                    tuple,
                    selected.loc[
                        selected["class_label"].str.startswith("H1"),
                        ["node_a", "node_b"],
                    ].to_numpy(),
                )
            )
            h0_pairs = set(
                map(
                    tuple,
                    selected.loc[
                        selected["class_label"].str.startswith("H0"),
                        ["node_a", "node_b"],
                    ].to_numpy(),
                )
            )
            self.assertFalse(h1_pairs & h0_pairs)

    def test_context_policy_and_stream_profiles(self) -> None:
        kinase = self.training.loc[
            self.training["stream_id"].eq("kinase_predictor")
        ]
        self.assertEqual(
            set(kinase["context_policy"]), {"context_general_biochemical"}
        )
        physical = self.training.loc[
            self.training["profile"].isin(
                ["physical_proximity", "physical_binding", "regulatory_signaling"]
            )
        ]
        self.assertEqual(
            set(physical["context_policy"]), {"collecting_duct_specific"}
        )
        self.assertEqual(EDGE_LIKELIHOOD_PROFILES["string_v12"], "regulatory_signaling")

    def test_default_models_use_curated_references(self) -> None:
        models = curated_edge_likelihood_models(
            PROJECT_ROOT,
            smoothing=0.5,
            minimum_reference_count=20,
        )
        kinase = models["kinase_predictor"]
        self.assertTrue(kinase["applied"])
        self.assertEqual(kinase["h1_reference_count"], 88)
        self.assertEqual(kinase["h0_reference_count"], 22)
        self.assertEqual(kinase["explicit_h0_count"], 22)
        string = models["string_v12"]
        self.assertTrue(string["applied"])
        self.assertEqual(string["h1_reference_count"], 48)
        self.assertEqual(string["h0_reference_count"], 240)
        messenger = models["stitch_secondary_messenger"]
        self.assertFalse(messenger["applied"])
        self.assertIn("insufficient", messenger["fallback_reason"])

    def test_saved_model_only_transforms_observed_values(self) -> None:
        model = curated_edge_likelihood_models(
            PROJECT_ROOT,
            smoothing=0.5,
            minimum_reference_count=20,
        )["kinase_predictor"]
        values = np.asarray([0.5, 1.0, 5.0], dtype=float)
        calibrated = apply_empirical_likelihood_model_array(
            values, model, evidence_observed=np.asarray([True, False, True])
        )
        self.assertEqual(calibrated[1], 1.0)
        self.assertTrue(np.isfinite(calibrated).all())
        self.assertTrue((calibrated > 0).all())

    def test_edge_integration_consumes_curated_models_and_skips_closure_refit(self) -> None:
        registry = load_registry(PROJECT_ROOT)
        matrix, summary = combine_edge_factors(
            PROJECT_ROOT,
            registry,
            default_configuration(registry),
            ["Prkaca", "Aqp2", "Dyrk1a"],
        )
        self.assertEqual(matrix.shape, (3, 3))
        streams = {stream["id"]: stream for stream in summary["active_streams"]}
        self.assertEqual(
            streams["kinase_predictor"]["empirical_likelihood"]["reference_source"],
            "positive_cases_20261028",
        )
        self.assertEqual(
            streams["string_v12"]["likelihood_hypothesis_class"],
            "regulatory_signaling",
        )
        self.assertEqual(
            streams["scaffold_triadic_closure"]["empirical_likelihood"][
                "reference_source"
            ],
            "derived_rule",
        )


if __name__ == "__main__":
    unittest.main()
