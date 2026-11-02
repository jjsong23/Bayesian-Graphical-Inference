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
        cls.unresolved = pd.read_csv(
            PROJECT_ROOT
            / "gui/calibration/positive_cases_20261028/unresolved_reference_rows.tsv",
            sep="\t",
        )

    def test_expected_profile_counts_and_no_class_overlap(self) -> None:
        expected = {
            "physical_proximity": (81, 405),
            "physical_binding": (81, 405),
            "directed_phosphorylation": (88, 27),
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
            & self.training["profile"].eq("directed_phosphorylation")
        ]
        self.assertEqual(
            set(kinase["context_policy"]), {"context_general_biochemical"}
        )
        physical = self.training.loc[
            self.training["profile"].isin(
                ["physical_proximity", "physical_binding"]
            )
        ]
        self.assertEqual(
            set(physical["context_policy"]), {"collecting_duct_specific"}
        )
        regulatory = self.training.loc[
            self.training["profile"].eq("regulatory_signaling")
        ]
        self.assertEqual(
            set(regulatory["context_policy"]),
            {"collecting_duct_specific", "outside_collecting_duct_assay"},
        )
        self.assertEqual(EDGE_LIKELIHOOD_PROFILES["string_v12"], "regulatory_signaling")

    def test_every_primary_stream_is_cross_scored_on_every_curated_reference(self) -> None:
        per_stream = self.training.groupby("stream_id").size()
        self.assertEqual(len(per_stream), 12)
        self.assertEqual(len(set(per_stream.astype(int))), 1)
        self.assertEqual(int(per_stream.iloc[0]), 1381)
        expected_profiles = {
            "physical_proximity",
            "physical_binding",
            "directed_phosphorylation",
            "regulatory_signaling",
            "messenger_binding",
        }
        for _, group in self.training.groupby("stream_id"):
            self.assertEqual(set(group["profile"]), expected_profiles)

    def test_default_models_use_curated_references(self) -> None:
        models = curated_edge_likelihood_models(
            PROJECT_ROOT,
            smoothing=0.5,
            minimum_reference_count=20,
        )
        kinase = models["kinase_predictor"]
        self.assertTrue(kinase["applied"])
        self.assertEqual(kinase["h1_reference_count"], 88)
        self.assertEqual(kinase["h0_reference_count"], 27)
        self.assertEqual(kinase["explicit_h0_count"], 27)
        string = models["string_v12"]
        self.assertTrue(string["applied"])
        self.assertEqual(string["h1_reference_count"], 48)
        self.assertEqual(string["explicit_h0_count"], 27)
        self.assertEqual(string["confirmed_h0_count"], 27)
        self.assertEqual(string["outside_cd_h0_count"], 16)
        self.assertEqual(string["background_h0_count"], 213)
        self.assertEqual(string["training_reference_count"], 288)
        optimization = string["h0_mixture_weight_optimization"]
        self.assertTrue(optimization["enabled"])
        self.assertEqual(optimization["objective"], "jensen_shannon_bits")
        self.assertEqual(
            optimization["selected_weight"],
            string["h0_confirmed_mixture_weight"],
        )
        self.assertIn(
            "jensen_shannon_bits",
            optimization["selected_information_indices"],
        )
        self.assertEqual(string["reference_scope"], "class_matched")
        messenger = models["stitch_secondary_messenger"]
        self.assertFalse(messenger["applied"])
        self.assertIn("insufficient", messenger["fallback_reason"])

    def test_h0_mixture_weight_shifts_group_mass(self) -> None:
        background_only = curated_edge_likelihood_models(
            PROJECT_ROOT,
            smoothing=0.5,
            minimum_reference_count=20,
            h0_confirmed_mixture_weight=0.0,
            optimize_h0_mixture_weight=False,
        )
        equal_mixture = curated_edge_likelihood_models(
            PROJECT_ROOT,
            smoothing=0.5,
            minimum_reference_count=20,
            h0_confirmed_mixture_weight=0.5,
            optimize_h0_mixture_weight=False,
        )
        self.assertEqual(
            background_only["string_v12"]["h0_reference_count"], 213
        )
        self.assertAlmostEqual(
            background_only["string_v12"]["background_h0_effective_weight"],
            213.0,
        )
        self.assertAlmostEqual(
            background_only["string_v12"]["confirmed_h0_effective_weight"],
            0.0,
        )
        self.assertEqual(equal_mixture["string_v12"]["h0_reference_count"], 240)
        self.assertAlmostEqual(
            equal_mixture["string_v12"]["confirmed_h0_effective_weight"],
            120.0,
        )
        self.assertAlmostEqual(
            equal_mixture["string_v12"]["background_h0_effective_weight"],
            120.0,
        )
        self.assertNotEqual(
            background_only["omnipath_core"]["p_data_given_h0"],
            equal_mixture["omnipath_core"]["p_data_given_h0"],
        )

    def test_all_curated_mode_uses_one_pooled_pair_set_for_every_stream(self) -> None:
        models = curated_edge_likelihood_models(
            PROJECT_ROOT,
            smoothing=0.5,
            minimum_reference_count=20,
            reference_scope="all_curated_edges",
            optimize_h0_mixture_weight=True,
        )
        self.assertEqual(len(models), 12)
        self.assertEqual({model["h1_reference_count"] for model in models.values()}, {215})
        self.assertEqual(
            {model["training_reference_count"] for model in models.values()},
            {1119},
        )
        self.assertEqual(
            {model["h1_h0_cross_profile_conflict_count"] for model in models.values()},
            {11},
        )
        for model in models.values():
            self.assertEqual(model["reference_scope"], "all_curated_edges")
            self.assertEqual(model["hypothesis_class"], "all_curated_edges")
            self.assertTrue(model["h0_mixture_weight_optimization"]["enabled"])

    def test_negative_signaling_workbook_is_routed_conservatively(self) -> None:
        references = self.training.drop_duplicates(
            ["profile", "node_a", "node_b", "class_label"]
        )
        imported = references.loc[
            references["source_workbook"].eq(
                "collecting_duct_negative_examples.xlsx"
            )
        ]
        self.assertEqual(
            imported.groupby("profile").size().to_dict(),
            {
                "directed_phosphorylation": 5,
                "regulatory_signaling": 27,
            },
        )
        self.assertEqual(set(imported["class_label"]), {"H0_EXPLICIT_NEGATIVE"})
        self.assertEqual(set(imported["negative_kind"]), {"explicit_tested_null"})
        self.assertEqual(
            set(imported.loc[imported["profile"].eq("regulatory_signaling"), "source_sheet"]),
            {"CD assay negatives", "Outside CD assays"},
        )
        outside_regulatory = imported.loc[
            imported["profile"].eq("regulatory_signaling")
            & imported["context_policy"].eq("outside_collecting_duct_assay")
        ]
        self.assertEqual(len(outside_regulatory), 16)
        self.assertEqual(set(outside_regulatory["calibration_weight"]), {1.0})
        self.assertEqual(
            set(
                imported.loc[
                    imported["profile"].eq("directed_phosphorylation"),
                    "source_sheet",
                ]
            ),
            {"Outside CD assays"},
        )
        self.assertFalse(
            set(imported["source_sheet"])
            & {"Dispensable mechanisms", "Limited or disputed", "Mutant controls"}
        )
        conflicts = self.unresolved.loc[
            self.unresolved["source_workbook"].eq(
                "collecting_duct_negative_examples.xlsx"
            )
            & self.unresolved["reason"].str.contains(
                "excluded from pair-level H0", na=False
            )
        ]
        self.assertEqual(
            set(conflicts["source_record_id"]),
            {"OC023", "OC024", "OC026", "OC027"},
        )

    def test_revised_ppi_source_and_negative_pair_routing(self) -> None:
        source_root = (
            PROJECT_ROOT
            / "gui/calibration/positive_cases_20261028/source/Positive Cases"
        )
        focused = pd.read_excel(
            source_root / "Collecting_duct_PPI_evidence.xlsx",
            sheet_name="Focused associations",
            header=5,
            dtype=str,
        ).fillna("")
        self.assertEqual(len(focused), 86)
        self.assertNotIn("ROOT08", set(focused["Study ID"]))

        references = self.training.drop_duplicates(
            ["profile", "node_a", "node_b", "class_label"]
        )
        imported = references.loc[
            references["source_workbook"].eq(
                "Collecting_duct_negative_PPI_examples_expanded.xlsx"
            )
        ]
        self.assertEqual(len(imported), 13)
        self.assertEqual(set(imported["profile"]), {"physical_binding"})
        self.assertEqual(set(imported["class_label"]), {"H0_EXPLICIT_NEGATIVE"})
        self.assertEqual(
            set(imported["source_record_id"]),
            {f"N{index:03d}" for index in range(1, 13)} | {"N014"},
        )
        ppi_conflict = self.unresolved.loc[
            self.unresolved["source_workbook"].eq(
                "Collecting_duct_negative_PPI_examples_expanded.xlsx"
            )
        ]
        self.assertEqual(set(ppi_conflict["source_record_id"]), {"N015"})

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
