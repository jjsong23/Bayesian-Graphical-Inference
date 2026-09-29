"""Fixture tests for database-native evidence tracebacks."""

from __future__ import annotations

import gzip
import shutil
import unittest
import uuid
from pathlib import Path

import pandas as pd

import evidence_provenance


class EvidenceProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.project = Path(__file__).parent / f".provenance_test_{uuid.uuid4().hex}"
        self.run = self.project / "run"
        self.run.mkdir(parents=True)

    def tearDown(self) -> None:
        evidence_provenance._table_lookup.cache_clear()
        evidence_provenance._seed_identities.cache_clear()
        evidence_provenance._omnipath_rows.cache_clear()
        evidence_provenance._biogrid_mapping_tables.cache_clear()
        evidence_provenance._biogrid_source_records.cache_clear()
        shutil.rmtree(self.project, ignore_errors=True)

    def _write_tsv(self, relative: Path, frame: pd.DataFrame, *, gzip_output: bool = False) -> None:
        path = self.project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_csv(path, sep="\t", index=False, compression="gzip" if gzip_output else None)

    def test_string_trace_exposes_channels_without_double_counting(self) -> None:
        row = {
            "node_a": "A",
            "node_b": "B",
            "string_id_a": "10090.A",
            "string_id_b": "10090.B",
            "experimental_score": 0.8,
            "database_score": 0.7,
            "textmining_score": 0.6,
            "coexpression_score": 0.5,
            "neighborhood_score": 0.4,
            "fusion_score": 0.3,
            "cooccurrence_score": 0.2,
            "string_combined_score": 0.9,
            "string_bayes_factor": 10,
        }
        self._write_tsv(evidence_provenance.STRING_SUPPORTED, pd.DataFrame([row]))
        trace = evidence_provenance.string_trace(
            self.project,
            ("A", "B"),
            applied_bayes_factor=4,
            weight=0.5,
            reference_multiplier=1,
        )
        self.assertEqual(trace["trace_status"], "matched")
        self.assertEqual(trace["score_derivation"]["database_score"], 0.9)
        self.assertEqual(trace["score_derivation"]["weighted_effective_bayes_factor"], 2)
        self.assertEqual(len(trace["factors"]), 8)
        self.assertIn("not independently multiplied", trace["factors"][0]["role"])

    def test_omnipath_trace_preserves_direction_sign_resources_and_references(self) -> None:
        self._write_tsv(
            evidence_provenance.PROTEIN_INDEX,
            pd.DataFrame(
                [
                    {"symbol": "A", "selected_uniprot": "P1"},
                    {"symbol": "B", "selected_uniprot": "P2"},
                ]
            ),
        )
        self._write_tsv(
            evidence_provenance.OMNIPATH_RAW,
            pd.DataFrame(
                [
                    {
                        "source": "P1",
                        "target": "P2",
                        "source_genesymbol": "A",
                        "target_genesymbol": "B",
                        "is_stimulation": "True",
                        "is_inhibition": "False",
                        "consensus_direction": "True",
                        "consensus_stimulation": "True",
                        "consensus_inhibition": "False",
                        "curation_effort": "8",
                        "sources": "SIGNOR;PhosphoSite",
                        "references": "SIGNOR:12345;PhosphoSite:67890",
                    }
                ]
            ),
        )
        trace = evidence_provenance.omnipath_trace(
            self.project,
            ("A", "B"),
            applied_bayes_factor=1.8,
            weight=1,
            tq_multiplier=1,
        )
        self.assertEqual(trace["trace_status"], "matched")
        self.assertEqual(trace["records"][0]["source_symbol"], "A")
        self.assertTrue(trace["records"][0]["is_stimulation"])
        self.assertEqual(trace["score_derivation"]["database_score"], 8)
        self.assertEqual(len([link for link in trace["links"] if "PMID" in link["label"]]), 2)

    def test_biogrid_trace_exposes_assays_publications_and_projection(self) -> None:
        self._write_tsv(
            evidence_provenance.UNIVERSE,
            pd.DataFrame(
                [
                    {"symbol": "A", "node_type": "protein"},
                    {"symbol": "B", "node_type": "protein"},
                ]
            ),
        )
        self._write_tsv(
            evidence_provenance.HUMAN_MAPPING,
            pd.DataFrame(columns=["symbol", "human_entrez_gene_id"]),
        )
        self._write_tsv(
            evidence_provenance.RAT_MAPPING,
            pd.DataFrame(
                columns=[
                    "rat_ensembl_gene_id", "rat_gene_symbol", "mouse_ensembl_gene_id",
                    "mouse_gene_symbol", "orthology_type", "mouse_percent_identity",
                    "rat_percent_identity", "orthology_confidence",
                ]
            ),
        )
        self._write_tsv(
            evidence_provenance.BIOGRID_MAPPED,
            pd.DataFrame(
                [
                    {
                        "node_a": "A", "node_b": "B", "in_direct": True,
                        "in_cocomplex": True, "in_huri": False,
                        "source_species": "mouse", "systems": "Two-hybrid",
                        "pubmed_ids": "111", "maximum_source_n_pubs": 1,
                        "maximum_source_n_methods": 1, "bayes_factor": 1.8,
                        "factor_rule": "equal reported-pair BF",
                    }
                ]
            ),
            gzip_output=True,
        )
        self._write_tsv(
            evidence_provenance.BIOGRID_CATALOG,
            pd.DataFrame(
                [
                    {
                        "build": "5.0.261", "biogrid_id_a": "BG1", "biogrid_id_b": "BG2",
                        "entrez_a": "1", "entrez_b": "2", "symbol_a": "A", "symbol_b": "B",
                        "species": "mouse", "in_direct": True, "in_cocomplex": True,
                        "n_pubs": 1, "n_methods": 1, "systems": "Two-hybrid",
                        "pubmed_ids": "111", "any_low_throughput": True,
                        "in_huri": False, "is_self": False,
                    }
                ]
            ),
            gzip_output=True,
        )
        trace = evidence_provenance.biogrid_trace(
            self.project, ("A", "B"), applied_bayes_factor=1.8, weight=1
        )
        self.assertEqual(trace["trace_status"], "matched")
        self.assertTrue(trace["records"][0]["direct_contact"])
        self.assertEqual(trace["records"][0]["experimental_systems"], ["Two-hybrid"])
        self.assertIn("counted once", trace["summary"])
        self.assertEqual(trace["links"][1]["label"], "PMID 111")


if __name__ == "__main__":
    unittest.main()
