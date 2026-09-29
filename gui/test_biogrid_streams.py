from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


GUI_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = GUI_DIR.parent
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from workflow_engine import (  # noqa: E402
    BIOGRID_INCIDENT_ANCHORS_RELATIVE,
    BIOGRID_MAPPED_PAIRS_RELATIVE,
    UNIVERSE_RELATIVE,
    _biogrid_edge_factors,
    _biogrid_external_shared_partner_factors,
    _biogrid_reported_pairs,
    biogrid_shared_partner_closure_factors,
    default_configuration,
    load_registry,
)


class BioGRIDWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        required = [
            PROJECT_ROOT / BIOGRID_MAPPED_PAIRS_RELATIVE,
            PROJECT_ROOT / BIOGRID_INCIDENT_ANCHORS_RELATIVE,
        ]
        if not all(path.is_file() for path in required):
            raise unittest.SkipTest("audited BioGRID tables are not installed")

    def test_registry_enables_reported_and_shared_partner_streams(self) -> None:
        registry = load_registry(PROJECT_ROOT)
        config = default_configuration(registry)
        self.assertTrue(config["edge_streams"]["biogrid_physical_interaction"]["enabled"])
        self.assertTrue(config["edge_streams"]["biogrid_shared_partner_closure"]["enabled"])
        self.assertEqual(
            config["edge_streams"]["biogrid_physical_interaction"]["parameters"][
                "reported_pair_bayes_factor"
            ],
            5.0,
        )
        self.assertEqual(
            config["edge_streams"]["biogrid_shared_partner_closure"]["parameters"][
                "closure_likelihood"
            ],
            0.9,
        )

    def test_reported_tiers_receive_the_same_factor(self) -> None:
        pairs = pd.read_csv(
            PROJECT_ROOT / BIOGRID_MAPPED_PAIRS_RELATIVE,
            sep="\t",
            compression="gzip",
        )
        row = pairs.iloc[0]
        factors = _biogrid_edge_factors(
            PROJECT_ROOT,
            [str(row.node_a), str(row.node_b)],
            5.0,
            5.0,
        )
        self.assertEqual(len(factors), 1)
        self.assertEqual(float(factors.iloc[0].bayes_factor), 5.0)

    def test_shared_partner_closure_is_fixed_and_nonrecursive(self) -> None:
        anchors = pd.read_csv(
            PROJECT_ROOT / BIOGRID_INCIDENT_ANCHORS_RELATIVE,
            sep="\t",
            compression="gzip",
        )
        all_anchor_symbols = anchors["graph_node"].astype(str).drop_duplicates().tolist()
        all_reported = {
            tuple(sorted((left, right), key=str.casefold))
            for left, right in _biogrid_reported_pairs(
                PROJECT_ROOT, all_anchor_symbols
            )[["node_a", "node_b"]].itertuples(index=False, name=None)
        }
        symbols = None
        for _, group in anchors.groupby("partner_key"):
            members = group["graph_node"].astype(str).drop_duplicates().head(8).tolist()
            if len(members) < 2:
                continue
            if any(
                tuple(sorted((members[i], members[j]), key=str.casefold))
                not in all_reported
                for i in range(len(members))
                for j in range(i + 1, len(members))
            ):
                symbols = members
                break
        self.assertIsNotNone(symbols)
        assert symbols is not None
        universe = pd.read_csv(PROJECT_ROOT / UNIVERSE_RELATIVE, sep="\t", dtype=str).fillna("")
        metadata = universe.loc[universe["symbol"].isin(symbols)].copy()
        preclosure = np.full((len(symbols), len(symbols)), 0.5)
        np.fill_diagonal(preclosure, 0.0)
        state = {"parameters": {"closure_likelihood": 0.9}}
        audit, summary = biogrid_shared_partner_closure_factors(
            PROJECT_ROOT,
            symbols,
            preclosure,
            state,
            graph_metadata=metadata,
        )
        self.assertGreater(len(audit), 0)
        self.assertTrue((audit["bayes_factor"] == 1.8).all())
        self.assertFalse(summary["recursive_feedback"])
        self.assertEqual(summary["degree_adjustment"], "none")
        self.assertTrue(
            all(
                tuple(sorted((row.node_a, row.node_b), key=str.casefold))
                not in all_reported
                for row in audit.itertuples(index=False)
            )
        )

    def test_shared_partner_closure_extends_to_appended_target(self) -> None:
        anchors = pd.read_csv(
            PROJECT_ROOT / BIOGRID_INCIDENT_ANCHORS_RELATIVE,
            sep="\t",
            compression="gzip",
        )
        all_anchor_symbols = anchors["graph_node"].astype(str).drop_duplicates().tolist()
        all_reported = {
            tuple(sorted((left, right), key=str.casefold))
            for left, right in _biogrid_reported_pairs(
                PROJECT_ROOT, all_anchor_symbols
            )[["node_a", "node_b"]].itertuples(index=False, name=None)
        }
        target = None
        candidates = None
        for _, group in anchors.groupby("partner_key"):
            members = group["graph_node"].astype(str).drop_duplicates().head(8).tolist()
            if len(members) < 2:
                continue
            for candidate_target in members:
                other = [symbol for symbol in members if symbol != candidate_target]
                direct_neighbors = {
                    right if left == candidate_target else left
                    for left, right in all_reported
                    if candidate_target in {left, right}
                }
                novel = [symbol for symbol in other if symbol not in direct_neighbors]
                if novel:
                    target = candidate_target
                    candidates = pd.Index([*novel, *sorted(direct_neighbors)[:1]])
                    break
            if target is not None:
                break
        self.assertIsNotNone(target)
        self.assertIsNotNone(candidates)
        assert target is not None and candidates is not None
        factors = _biogrid_external_shared_partner_factors(
            PROJECT_ROOT,
            target,
            candidates,
            {"parameters": {"closure_likelihood": 0.9}},
        )
        self.assertEqual(len(factors), len(candidates))
        self.assertTrue((factors >= 1.0).all())
        self.assertTrue((factors == 1.8).any())
        direct_neighbors = {
            right if left == target else left
            for left, right in all_reported
            if target in {left, right}
        }
        for index, symbol in enumerate(candidates.astype(str)):
            if symbol in direct_neighbors:
                self.assertEqual(float(factors[index]), 1.0)

    def test_huri_is_retained_inside_projected_biogrid(self) -> None:
        summary = json.loads(
            (
                PROJECT_ROOT
                / "data/edge_characterization/biogrid/5.0.261/"
                "mapped_graph_physical_summary.json"
            ).read_text(encoding="utf-8")
        )
        self.assertGreater(summary["reported_huri_graph_pair_count"], 0)


if __name__ == "__main__":
    unittest.main()
