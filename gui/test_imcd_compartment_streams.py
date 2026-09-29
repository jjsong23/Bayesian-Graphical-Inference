#!/usr/bin/env python3
"""Regression tests for the IMCD presence-only edge streams."""

from __future__ import annotations

import unittest
from pathlib import Path

from workflow_engine import (
    combine_edge_factors,
    default_configuration,
    edge_stream_factor_table,
    load_registry,
)


PROJECT = Path(__file__).resolve().parents[1]


class ImcdCompartmentStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = load_registry(PROJECT)

    def test_streams_are_separate_correlated_default_sources(self) -> None:
        definitions = {
            item["id"]: item for item in self.registry["edge_streams"]
        }
        for stream_id in (
            "imcd_basal_compartment_presence",
            "imcd_ddavp_compartment_presence",
        ):
            definition = definitions[stream_id]
            self.assertTrue(definition["default_enabled"])
            self.assertEqual(
                definition["dependence_group"], "imcd_compartment_fractionation"
            )

    def test_support_likelihood_sets_fixed_bayes_factor(self) -> None:
        definition = next(
            item
            for item in self.registry["edge_streams"]
            if item["id"] == "imcd_basal_compartment_presence"
        )
        config = default_configuration(self.registry)
        state = config["edge_streams"][definition["id"]]
        state["parameters"]["support_likelihood"] = 0.8
        table = edge_stream_factor_table(
            PROJECT, definition, state, ["Aqp2", "Prkaca"]
        )
        self.assertEqual(len(table), 1)
        self.assertAlmostEqual(float(table.iloc[0]["bayes_factor"]), 1.6)

    def test_added_node_uses_same_presence_rule(self) -> None:
        config = default_configuration(self.registry)
        for state in config["edge_streams"].values():
            state["enabled"] = False
        state = config["edge_streams"]["imcd_basal_compartment_presence"]
        state["enabled"] = True
        matrix, _ = combine_edge_factors(
            PROJECT,
            self.registry,
            config,
            ["Prkaca", "1110059G10Rik"],
        )
        self.assertAlmostEqual(float(matrix.loc["Prkaca", "1110059G10Rik"]), 0.6)
        self.assertAlmostEqual(float(matrix.loc["1110059G10Rik", "Prkaca"]), 0.6)


if __name__ == "__main__":
    unittest.main()
