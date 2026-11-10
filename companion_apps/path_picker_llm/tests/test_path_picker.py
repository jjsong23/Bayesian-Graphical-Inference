from __future__ import annotations

import gzip
import json
import unittest
import zipfile
from pathlib import Path

from path_picker.client import responses_url
from path_picker.database import RunDatabase
from path_picker.grounding import quote_is_present, validate_narrative
from path_picker.ingest import canonical_edge, import_agent_graphs
from path_picker.workflow import PathPickerWorkflow


def graph() -> dict:
    return {
        "run_id": "synthetic", "source_nodes": ["Prkaca"], "target_nodes": ["Aqp2"],
        "nodes": [
            {"id": "Prkaca", "label": "Prkaca", "posterior_probability": 0.9, "path_count": 1, "evidence_ledger": {"evidence": []}},
            {"id": "Sik1", "label": "Sik1", "posterior_probability": 0.8, "path_count": 1, "evidence_ledger": {"evidence": []}},
            {"id": "Aqp2", "label": "Aqp2", "posterior_probability": 0.95, "path_count": 1, "evidence_ledger": {"evidence": []}},
        ],
        "edges": [
            {"id": "Prkaca--Sik1", "node_a": "Prkaca", "node_b": "Sik1", "edge_probability": 0.8, "path_count": 1, "evidence_ledger": {"evidence": []}},
            {"id": "Aqp2--Sik1", "node_a": "Sik1", "node_b": "Aqp2", "edge_probability": 0.7, "path_count": 1, "evidence_ledger": {"evidence": []}},
        ],
        "ranked_paths": [{"rank": "1", "query_id": "Prkaca->Aqp2", "start_symbol": "Prkaca", "target_symbol": "Aqp2", "hop_count": "2", "primary_path_score": "0.75", "path_symbols": "Prkaca -> Sik1 -> Aqp2"}],
    }


class PathPickerTests(unittest.TestCase):
    @staticmethod
    def work(name: str) -> Path:
        root = Path(__file__).resolve().parent / "test_data" / name
        for path in root.glob("*"):
            if path.is_file() and path.name != ".gitkeep":
                path.unlink()
        run = root / "run"
        if run.is_dir():
            for path in run.glob("*"):
                if path.is_file():
                    path.unlink()
        else:
            run.mkdir()
        return root

    def test_responses_url(self) -> None:
        self.assertEqual(responses_url("https://example.openai.azure.com/openai/v1"), "https://example.openai.azure.com/openai/v1/responses")
        with self.assertRaises(ValueError):
            responses_url("http://localhost/v1")

    def test_quote_matching_normalizes_punctuation(self) -> None:
        page = "The kinase phosphorylates the substrate in collecting-duct cells."
        self.assertTrue(quote_is_present("The kinase phosphorylates the substrate in collecting–duct cells.", page))
        self.assertFalse(quote_is_present("kinase", page))

    def test_narrative_requires_claim_or_inference_per_sentence(self) -> None:
        text = " ".join([
            "PKA phosphorylates the substrate in this assay [CLM-ABC123].",
            "The predicted bridge is therefore experimentally testable [INFERENCE].",
        ] * 8)
        self.assertEqual(validate_narrative(text, {"CLM-ABC123"}), [])
        self.assertTrue(validate_narrative(text.replace("[CLM-ABC123]", ""), {"CLM-ABC123"}))

    def test_import_preserves_complete_payloads(self) -> None:
        root = self.work("import_case")
        archive_path = root / "input.zip"
        compressed = gzip.compress(json.dumps(graph()).encode())
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("one/agent_graph.json.gz", compressed)
            archive.writestr("two/agent_graph.json.gz", compressed)
        database = RunDatabase(root / "run.sqlite3")
        inventory = import_agent_graphs(archive_path, database)
        self.assertEqual(inventory["session_count"], 2)
        self.assertEqual(inventory["nodes"], 3)
        self.assertEqual(inventory["edges"], 2)
        self.assertEqual(inventory["paths"], 1)
        with database.connect() as db:
            stored = json.loads(db.execute("SELECT payload_json FROM nodes WHERE node_id='Sik1'").fetchone()[0])
        self.assertIn("evidence_ledger", stored)

    def test_api_key_is_never_persisted(self) -> None:
        root = self.work("secret_case")
        graph_path = root / "agent_graph.json.gz"
        with gzip.open(graph_path, "wt", encoding="utf-8") as handle:
            json.dump(graph(), handle)
        secret = "do-not-write-this-key"
        workflow = PathPickerWorkflow(
            run_directory=root / "run", input_path=graph_path,
            configuration={"api_base_url": "https://example.openai.azure.com/openai/v1", "research_model": "x", "verification_model": "x", "reasoning_model": "x", "synthesis_model": "x", "api_key_note": secret},
            api_key=secret,
        )
        workflow.run(inventory_only=True)
        for path in (root / "run").iterdir():
            if path.suffix in {".json", ".sqlite3"}:
                self.assertNotIn(secret.encode(), path.read_bytes())

    def test_canonical_edge_is_order_independent(self) -> None:
        self.assertEqual(canonical_edge("Sik1", "Prkaca"), canonical_edge("Prkaca", "Sik1"))


if __name__ == "__main__":
    unittest.main()

