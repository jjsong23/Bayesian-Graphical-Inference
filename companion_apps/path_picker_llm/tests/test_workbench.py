"""Offline UI/server/report contract checks. Never start paid work."""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import server
from path_picker.database import RunDatabase
from path_picker.report import coverage_panel, write_report


class WorkbenchTests(unittest.TestCase):
    def test_static_assets_and_downloads_disable_browser_caching(self):
        for request_path in ("/", "/app.js", "/styles.css", "/runs/test/configuration.json"):
            with self.subTest(path=request_path):
                handler = server.Handler.__new__(server.Handler)
                handler.path = request_path
                handler.wfile = io.BytesIO()
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                with patch.object(Path, "is_file", return_value=True), patch.object(Path, "read_bytes", return_value=b"example"):
                    handler.do_GET()
                handler.send_response.assert_called_once_with(200)
                handler.send_header.assert_any_call("Cache-Control", "no-store")
                self.assertEqual(handler.wfile.getvalue(), b"example")

    def test_recommended_configuration_is_the_new_run_default(self):
        expected = {
            "workflow_mode": "rag_path_first",
            "rag_model": "gpt-6-sol",
            "rag_reasoning": "medium",
            "rag_max_rounds": 2,
            "screening_model": "gpt-6-luna",
            "research_model": "gpt-6-luna",
            "verification_model": "gpt-6-sol",
            "reasoning_model": "gpt-6-sol",
            "synthesis_model": "gpt-6-sol",
            "max_deep_path_reviews": 50,
            "shortlist_minimum": 5,
            "shortlist_maximum": 12,
        }
        _, config = server.normalize({"input_path": str(Path(__file__).parent)})
        self.assertEqual({key: config[key] for key in expected}, expected)

    def test_explicit_scope_and_guard_defaults(self):
        with self.assertRaisesRegex(ValueError, "Choose a BGI"):
            server.normalize({})
        with tempfile.TemporaryDirectory() as temporary:
            _, config = server.normalize({"input_path": temporary})
            self.assertEqual(config["workflow_mode"], "rag_path_first")
            self.assertEqual(config["screening_model"], "gpt-6-luna")
            self.assertEqual(config["max_total_api_calls"], 10000)
            self.assertEqual(config["max_deep_path_reviews"], 50)
            for field, value in (("max_total_api_calls", 0), ("exclusion_max_fraction", 1.1), ("max_tool_calls", -1)):
                with self.assertRaises(ValueError):
                    server.normalize({"input_path": temporary, "configuration": {field: value}})

    def test_public_job_can_describe_inventory_and_stale_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database = RunDatabase(directory / "path_picker.sqlite3")
            database.set_meta("shortlist_stale", "true")
            database.set_meta("input_path", "example.json.gz")
            result = server.public_job(server.Job("test", directory, "inventory", "inventory_complete"))
            self.assertTrue(result["report_stale"])
            self.assertEqual(result["adaptive"]["pending"], 0)
            self.assertEqual(result["input_path"], "example.json.gz")

    def test_coverage_panel_marks_partial_and_escapes_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "inventory.json").write_text(json.dumps({"paths": 2000, "coverage_scope": "<script>bad</script>"}))
            (directory / "path_coverage.json").write_text(json.dumps({"counts": {"total_paths": 2000, "directly_reviewed": 10, "pending": 1990, "uncertain": 2, "excluded_by_rule": 0}}))
            panel = coverage_panel(directory)
            self.assertIn("partial review", panel)
            self.assertIn("1990", panel)
            self.assertNotIn("<script>", panel)
            report = write_report(directory, {"cards": [], "network_synthesis": "<unsafe>"}, [], [])
            self.assertIn("&lt;unsafe&gt;", report.read_text(encoding="utf-8"))

    def test_download_snapshot_includes_committed_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database = RunDatabase(directory / "source.sqlite3")
            database.set_meta("sample", "committed")
            snapshot = directory / "download.sqlite3"
            snapshot.write_bytes(server.database_snapshot(database.path))
            with closing(sqlite3.connect(snapshot)) as db:
                self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='sample'").fetchone()[0], "committed")


if __name__ == "__main__":
    unittest.main()
