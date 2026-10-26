#!/usr/bin/env python3
"""Regression tests for imported V1 AlphaPulldown evidence."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd


GUI_DIR = Path(__file__).resolve().parent
PROJECT = GUI_DIR.parent
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

import evidence_provenance  # noqa: E402
from workflow_engine import (  # noqa: E402
    combine_edge_factors,
    default_configuration,
    edge_stream_factor_table,
    load_registry,
)


FACTOR = (
    PROJECT
    / "results/backend_bayes_factor_catalogs/edge_factors_891/"
    "alphapulldown_structural_bf.tsv.gz"
)
PROVENANCE = (
    PROJECT
    / "data/edge_characterization/alphapulldown/"
    "v1_colocalized_model1_cycles3_predictions1_compact"
)


class AlphaPulldownStructuralStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not FACTOR.is_file():
            raise unittest.SkipTest("imported AlphaPulldown factor table is not installed")
        cls.registry = load_registry(PROJECT)
        cls.definition = next(
            item
            for item in cls.registry["edge_streams"]
            if item["id"] == "alphapulldown_structural"
        )
        cls.table = pd.read_csv(FACTOR, sep="\t", compression="gzip")

    def tearDown(self) -> None:
        evidence_provenance._table_lookup.cache_clear()
        evidence_provenance._alphapulldown_audit.cache_clear()

    def test_registry_enables_imported_structural_evidence_by_default(self) -> None:
        config = default_configuration(self.registry)
        self.assertTrue(config["edge_streams"]["alphapulldown_structural"]["enabled"])
        self.assertEqual(
            self.definition["dependence_group"], "structural_physical_evidence"
        )
        self.assertNotIn("negative_evidence", self.definition)

    def test_every_observed_pair_is_loaded_without_thresholding(self) -> None:
        self.assertEqual(len(self.table), 6805)
        self.assertTrue((self.table["bayes_factor"] < 1).any())
        row = self.table.sort_values("bayes_factor").iloc[0]
        state = default_configuration(self.registry)["edge_streams"][
            "alphapulldown_structural"
        ]
        loaded = edge_stream_factor_table(
            PROJECT,
            self.definition,
            state,
            [str(row.node_a), str(row.node_b)],
        )
        self.assertEqual(len(loaded), 1)
        self.assertAlmostEqual(
            float(loaded.iloc[0].bayes_factor), float(row.bayes_factor)
        )

    def test_missing_prediction_is_not_converted_to_negative_evidence(self) -> None:
        audit_frames = [
            pd.read_csv(
                PROVENANCE / phase / "structural_prediction_audit.tsv.gz",
                sep="\t",
                compression="gzip",
            )
            for phase in ("phase1_cocomplex", "phase2_other")
        ]
        audit = pd.concat(audit_frames, ignore_index=True)
        missing = audit.loc[audit["collection_status"] != "observed"].iloc[0]
        state = default_configuration(self.registry)["edge_streams"][
            "alphapulldown_structural"
        ]
        loaded = edge_stream_factor_table(
            PROJECT,
            self.definition,
            state,
            [str(missing.node_a), str(missing.node_b)],
        )
        self.assertTrue(loaded.empty)
        trace = evidence_provenance.alphapulldown_trace(
            PROJECT,
            evidence_provenance.canonical_pair(missing.node_a, missing.node_b),
            applied_bayes_factor=None,
            weight=1,
        )
        self.assertNotEqual(trace["trace_status"], "matched")
        self.assertIn("neutral", trace["summary"])

    def test_traceback_reports_iptm_and_frozen_mapping(self) -> None:
        row = self.table.iloc[0]
        trace = evidence_provenance.alphapulldown_trace(
            PROJECT,
            evidence_provenance.canonical_pair(row.node_a, row.node_b),
            applied_bayes_factor=float(row.bayes_factor),
            weight=1,
        )
        self.assertEqual(trace["trace_status"], "matched")
        self.assertAlmostEqual(
            trace["score_derivation"]["database_score"], float(row.iptm)
        )
        self.assertIn("heuristic", trace["score_derivation"]["important_limit"])

    def test_imported_factor_updates_the_edge_posterior_once(self) -> None:
        row = self.table.iloc[0]
        config = default_configuration(self.registry)
        for state in config["edge_streams"].values():
            state["enabled"] = False
        config["edge_streams"]["alphapulldown_structural"]["enabled"] = True
        config["edge_integration"]["prior_probability"] = 0.5
        matrix, _ = combine_edge_factors(
            PROJECT,
            self.registry,
            config,
            [str(row.node_a), str(row.node_b)],
        )
        factor = float(row.bayes_factor)
        self.assertAlmostEqual(
            float(matrix.loc[str(row.node_a), str(row.node_b)]),
            factor / (1.0 + factor),
        )

    def test_distribution_matching_rescales_a_single_stream_before_weighting(self) -> None:
        row = self.table.sort_values("bayes_factor", ascending=False).iloc[0]
        config = default_configuration(self.registry)
        for state in config["edge_streams"].values():
            state["enabled"] = False
        config["edge_streams"]["alphapulldown_structural"]["enabled"] = True
        config["edge_integration"].update(
            {
                "prior_probability": 0.5,
                "empirical_likelihood_enabled": False,
                "normalize_bayes_factor_distributions": True,
                "distribution_normalization_target_bayes_factor": 2.0,
            }
        )
        matrix, summary = combine_edge_factors(
            PROJECT,
            self.registry,
            config,
            [str(row.node_a), str(row.node_b)],
        )
        self.assertAlmostEqual(
            float(matrix.loc[str(row.node_a), str(row.node_b)]), 2.0 / 3.0
        )
        stream = summary["active_streams"][0]
        self.assertEqual(stream["id"], "alphapulldown_structural")
        self.assertTrue(stream["distribution_normalization"]["enabled"])
        self.assertAlmostEqual(
            stream["factor_distribution"]["maximum"], 2.0
        )


if __name__ == "__main__":
    unittest.main()
