#!/usr/bin/env python3
"""Focused tests for presence-only IMCD compartment evidence."""

from __future__ import annotations

import unittest

import pandas as pd

from integrate_imcd_compartment_presence import (
    build_pair_catalog,
    presence,
)


class ImcdCompartmentPresenceTests(unittest.TestCase):
    def test_presence_uses_only_positive_numeric_detection(self) -> None:
        self.assertTrue(presence(1))
        self.assertTrue(presence("7"))
        self.assertFalse(presence(0))
        self.assertFalse(presence("Not in CT"))
        self.assertFalse(presence("---"))

    def test_pair_support_is_condition_specific_shared_compartment(self) -> None:
        profiles = pd.DataFrame(
            [
                {
                    "symbol": "A",
                    "cytoplasm_basal": True,
                    "nucleus_basal": False,
                    "cytoplasm_ddavp": False,
                    "nucleus_ddavp": True,
                },
                {
                    "symbol": "B",
                    "cytoplasm_basal": True,
                    "nucleus_basal": False,
                    "cytoplasm_ddavp": True,
                    "nucleus_ddavp": False,
                },
                {
                    "symbol": "C",
                    "cytoplasm_basal": False,
                    "nucleus_basal": True,
                    "cytoplasm_ddavp": False,
                    "nucleus_ddavp": True,
                },
            ]
        )
        pairs = build_pair_catalog(profiles, ["A", "B", "C"], 0.75)
        indexed = pairs.set_index(["node_a", "node_b"])

        self.assertTrue(bool(indexed.loc[("A", "B"), "basal_colocalized"]))
        self.assertFalse(bool(indexed.loc[("A", "B"), "ddavp_colocalized"]))
        self.assertFalse(bool(indexed.loc[("A", "C"), "basal_colocalized"]))
        self.assertTrue(bool(indexed.loc[("A", "C"), "ddavp_colocalized"]))
        self.assertAlmostEqual(float(indexed.loc[("A", "B"), "bayes_factor"]), 1.5)


if __name__ == "__main__":
    unittest.main()
