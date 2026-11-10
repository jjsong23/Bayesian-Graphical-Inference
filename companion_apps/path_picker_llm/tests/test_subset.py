from copy import deepcopy
import gzip
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from path_picker.subset import export_query, select_query


def fixture():
    ledger = {"prior_probability": 0.5, "posterior_probability": 0.91,
              "evidence": [{"stream_id": "observed", "applied_bf": 7.125,
                            "raw_or_precalibration_bf": 3.5, "weight": 0.25,
                            "delta_log2_odds": 0.707, "note": "Dependent proximity, not direct binding.",
                            "conditional_site_evidence": [{"site": "S42", "bf": 2.0}],
                            "empirical_likelihood": {"applied": False, "fallback_reason": "disabled"}}]}
    return {
        "schema": "gbi-agent-evidence-graph", "schema_version": 1, "run_id": "native",
        "source_nodes": ["A", "Z"], "target_nodes": ["B", "C"],
        "coverage": {"network_node_count": 5, "network_edge_count": 5, "ledger_count": 10},
        "configuration": {"path": {"start": "Z", "target": "C", "starts": ["A", "Z"],
                                   "targets": ["B", "C"], "top_k": 500, "max_hops": 6}},
        "nodes": [{"id": symbol, "path_count": 99, "evidence_ledger": deepcopy(ledger)}
                  for symbol in ("A", "B", "C", "X", "Z")],
        "edges": [{"id": a + "--" + b, "node_a": a, "node_b": b, "path_count": 99,
                   "edge_probability": 0.9, "directionality": "uniquely_directed",
                   "path_traversals": [a + "->" + b, b + "->" + a],
                   "evidence_ledger": deepcopy(ledger)}
                  for a, b in (("A", "B"), ("A", "X"), ("X", "B"), ("B", "C"), ("Z", "C"))],
        "ranked_paths": [
            {"query_id": "A->B", "rank": "2", "path_symbols": "A -> B", "primary_path_score": "0.91"},
            {"query_id": "A->B", "rank": "7", "path_symbols": "A -> X -> B", "primary_path_score": "0.85"},
            {"query_id": "A->C", "rank": "1", "path_symbols": "A -> B -> C", "primary_path_score": "0.99"},
            {"query_id": "Z->C", "rank": "3", "path_symbols": "Z -> C", "primary_path_score": "0.9"},
        ],
    }


class SubsetTests(unittest.TestCase):
    def test_complete_union_and_nonselected_graph_removed(self):
        original = fixture()
        saved = deepcopy(original)
        selected = select_query(original, "A->B")
        self.assertEqual(original, saved)
        self.assertEqual(len(selected["ranked_paths"]), 2)
        self.assertEqual({node["id"] for node in selected["nodes"]}, {"A", "B", "X"})
        self.assertEqual({edge["id"] for edge in selected["edges"]}, {"A--B", "A--X", "X--B"})
        self.assertEqual(selected["source_nodes"], ["A"])
        self.assertEqual(selected["target_nodes"], ["B"])
        self.assertEqual(selected["ranked_paths"], original["ranked_paths"][:2])
        self.assertEqual({n["id"]: n["path_count"] for n in selected["nodes"]}, {"A": 2, "B": 2, "X": 1})
        self.assertEqual(selected["nodes"][0]["path_ranks"], [2, 7])
        self.assertEqual(selected["edges"][0]["path_traversals"], ["A->B"])
        self.assertEqual(selected["coverage"]["network_node_count"], 3)
        self.assertEqual(selected["configuration"]["path"]["targets"], ["B"])
        self.assertEqual(selected["configuration"]["path"]["top_k"], 2)
        self.assertEqual(selected["selection_provenance"]["original_metadata"]["coverage"], original["coverage"])

    def test_exact_bf_ledger_interpretation_and_directionality_retention(self):
        original = fixture()
        selected = select_query(original, "A->B")
        for kind in ("nodes", "edges"):
            by_id = {item["id"]: item for item in original[kind]}
            for item in selected[kind]:
                self.assertEqual(item["evidence_ledger"], by_id[item["id"]]["evidence_ledger"])
        for edge in selected["edges"]:
            self.assertEqual(edge["directionality"], "uniquely_directed")
            self.assertEqual(edge["edge_probability"], 0.9)

    def test_unknown_query_duplicate_paths_and_missing_ledgers_fail(self):
        with self.assertRaisesRegex(ValueError, "Unknown or empty query"):
            select_query(fixture(), "missing")
        broken = fixture()
        broken["ranked_paths"].append(deepcopy(broken["ranked_paths"][0]))
        with self.assertRaisesRegex(ValueError, "Duplicate path sequence"):
            select_query(broken, "A->B")
        broken = fixture()
        broken["edges"].pop(0)
        with self.assertRaisesRegex(ValueError, "missing evidence payloads"):
            select_query(broken, "A->B")

    def test_zip_export_and_expected_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive_path = root / "source.zip"
            member = "native/agent_graph.json.gz"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr(member, gzip.compress(json.dumps(fixture()).encode("utf-8")))
            with self.assertRaisesRegex(ValueError, "Expected 2000 paths"):
                export_query(archive_path, root / "bad", session_id="native", query_id="A->B",
                             expected_path_count=2000, archive_member=member)
            self.assertFalse((root / "bad").exists())
            manifest = export_query(archive_path, root / "selected", session_id="native", query_id="A->B",
                                    expected_path_count=2, archive_member=member)
            self.assertEqual(manifest["counts"]["paths"], 2)
            self.assertEqual(manifest["paid_api_calls"], 0)
            with gzip.open(manifest["output_graph"], "rt", encoding="utf-8") as handle:
                result = json.load(handle)
            self.assertEqual(result["run_id"], "native")
            self.assertEqual(result["selection_provenance"]["source_origin"], member)
            self.assertEqual(result["nodes"][0]["evidence_ledger"], fixture()["nodes"][0]["evidence_ledger"])
            self.assertEqual(json.loads((root / "selected" / "selection.json").read_text()), manifest)
            with self.assertRaises(FileExistsError):
                export_query(archive_path, root / "selected", session_id="native", query_id="A->B")


if __name__ == "__main__":
    unittest.main()
