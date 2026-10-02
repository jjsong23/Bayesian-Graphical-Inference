#!/usr/bin/env python3
"""Regression tests for the consolidated COMPARTMENTS V1 edge stream."""

from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from workflow_engine import (
    default_configuration,
    edge_stream_eligibility_matrix,
    edge_stream_factor_table,
    load_registry,
    normalize_configuration,
)


PROJECT = Path(__file__).resolve().parents[1]


class CompartmentsStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(PROJECT)
        cls.definitions = {
            item["id"]: item for item in cls.registry["edge_streams"]
        }

    def test_compartments_replaces_hpa_in_defaults(self) -> None:
        config = default_configuration(self.registry)
        self.assertTrue(
            config["edge_streams"]["compartments_localization"]["enabled"]
        )
        self.assertFalse(config["edge_streams"]["hpa_primary"]["enabled"])
        self.assertFalse(
            config["edge_streams"]["hpa_high_confidence"]["enabled"]
        )
        group = "integrated_localization_reference"
        self.assertEqual(
            self.definitions["compartments_localization"]["exclusive_group"],
            group,
        )
        self.assertEqual(self.definitions["hpa_primary"]["exclusive_group"], group)

    def test_legacy_saved_hpa_configuration_migrates_without_conflict(self) -> None:
        legacy = default_configuration(self.registry)
        legacy["edge_streams"].pop("compartments_localization")
        legacy["edge_streams"]["hpa_primary"]["enabled"] = True
        normalized = normalize_configuration(legacy, self.registry)
        self.assertTrue(
            normalized["edge_streams"]["compartments_localization"]["enabled"]
        )
        self.assertFalse(normalized["edge_streams"]["hpa_primary"]["enabled"])

    def test_default_dynamic_factor_matches_saved_catalog(self) -> None:
        definition = self.definitions["compartments_localization"]
        state = default_configuration(self.registry)["edge_streams"][
            "compartments_localization"
        ]
        # Nudge the scale beyond the factor-table fast path so this exercises
        # reconstruction from the saved cross-species node profiles.
        state["tq_multiplier"] = 1.0 + 1e-12
        table = edge_stream_factor_table(
            PROJECT, definition, state, ["Gng12", "Arf4"]
        )
        self.assertEqual(len(table), 1)
        saved = pd.read_csv(
            PROJECT / definition["factor_file"],
            sep="\t",
            compression="gzip",
        )
        saved = saved.loc[
            saved["node_a"].eq("Gng12") & saved["node_b"].eq("Arf4")
        ]
        self.assertEqual(len(saved), 1)
        self.assertAlmostEqual(
            float(table.iloc[0]["bayes_factor"]),
            float(saved.iloc[0]["bayes_factor"]),
            places=7,
        )

    def test_profile_scope_is_pairwise(self) -> None:
        definition = self.definitions["compartments_localization"]
        eligible = edge_stream_eligibility_matrix(
            PROJECT, definition, ["Gng12", "Arf4", "SM_cAMP"]
        )
        self.assertTrue(eligible[0, 1])
        self.assertFalse(eligible[0, 2])


if __name__ == "__main__":
    unittest.main()
