"""Regression tests for the ground-truth curation GUI adapter."""

from __future__ import annotations

import tempfile
import time
import unittest
import json
from pathlib import Path
from unittest.mock import patch

import workbench


class GroundTruthWorkbenchTests(unittest.TestCase):
    def setUp(self) -> None:
        with workbench.JOBS_LOCK:
            workbench.JOBS.clear()

    def tearDown(self) -> None:
        with workbench.JOBS_LOCK:
            workbench.JOBS.clear()

    def test_normalization_drops_browser_keys_and_validates_endpoint(self) -> None:
        config = workbench.normalize_configuration({"api_key": "must-not-survive"})
        self.assertNotIn("api_key", config)
        self.assertEqual(config["api_key_env"], "AZURE_OPENAI_API_KEY")
        self.assertEqual(config["protocol_version"], "0.6")
        self.assertEqual(len(config["evidence_profiles"]), 4)
        self.assertTrue(config["generator_web_search"])
        self.assertTrue(config["generator_web_search_required"])
        self.assertEqual(config["generator_concurrency"], 4)
        self.assertEqual(config["validator_concurrency"], 4)
        with self.assertRaisesRegex(ValueError, "official HTTPS"):
            workbench.normalize_configuration({"api_base_url": "http://example.test/v1"})
        with self.assertRaisesRegex(ValueError, "context_size"):
            workbench.normalize_configuration({"generator_web_search_context_size": "enormous"})
        with self.assertRaisesRegex(ValueError, "generator_concurrency"):
            workbench.normalize_configuration({"generator_concurrency": 33})
        with self.assertRaisesRegex(ValueError, "validator_concurrency"):
            workbench.normalize_configuration({"validator_concurrency": 17})

    def test_dry_run_job_makes_preview_without_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(workbench, "RUN_ROOT", Path(directory)):
                job = workbench.create_job({"mode": "dry_run", "configuration": {}}, None)
                deadline = time.monotonic() + 3
                while job.status not in workbench.TERMINAL_STATUSES and time.monotonic() < deadline:
                    time.sleep(0.01)
                payload = workbench.public_job(job)
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["result"]["minimum_pair_generation_calls"], 90)
        self.assertEqual(payload["result"]["maximum_pair_generation_calls"], 300)
        self.assertTrue(payload["result"]["generator_web_search_required"])
        self.assertIn("dry_run.json", payload["files"])

    def test_paid_run_requires_key_but_not_holdout_review(self) -> None:
        with self.assertRaisesRegex(ValueError, "API key"):
            workbench.create_job({"mode": "production", "configuration": {}}, None)
        config = workbench.normalize_configuration({"heldout_exclusion_reviewed": False, "excluded_pmids": ["12345678"]})
        self.assertNotIn("heldout_exclusion_reviewed", config)
        self.assertNotIn("excluded_pmids", config)

    def test_recovery_ignores_runs_from_an_older_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / "old_run"
            old.mkdir()
            (old / "configuration.json").write_text('{"protocol_version":"0.2"}', encoding="utf-8")
            (old / "dry_run.json").write_text('{"protocol_version":"0.2"}', encoding="utf-8")
            with patch.object(workbench, "RUN_ROOT", root):
                self.assertIsNone(workbench.recover_latest_completed_job())

    def test_recovery_exposes_incomplete_run_as_resumable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "incomplete"
            run.mkdir()
            config = workbench.load_default_configuration()
            (run / "configuration.json").write_text(json.dumps(config), encoding="utf-8")
            (run / "node_candidates.json").write_text("{}", encoding="utf-8")
            with patch.object(workbench, "RUN_ROOT", root):
                job = workbench.recover_latest_job()
        self.assertIsNotNone(job)
        self.assertEqual(job.status, "failed")
        self.assertTrue(workbench.public_job(job)["resumable"])
        self.assertEqual(job.progress, 0.25)

    def test_retry_reuses_failed_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            config = workbench.load_default_configuration()
            (run / "configuration.json").write_text(json.dumps(config), encoding="utf-8")
            failed = workbench.GroundTruthJob(
                "saved-run", "production", config, str(run), status="failed", progress=0.25,
            )
            with workbench.JOBS_LOCK:
                workbench.JOBS[failed.job_id] = failed
            with patch.object(workbench, "execute_job") as execute:
                resumed = workbench.retry_job(failed.job_id, "secret")
                deadline = time.monotonic() + 1
                while not execute.called and time.monotonic() < deadline:
                    time.sleep(0.01)
        self.assertEqual(resumed.job_id, "saved-run")
        self.assertEqual(resumed.output_directory, str(run.resolve()))
        self.assertEqual(resumed.status, "queued")
        execute.assert_called_once_with("saved-run", "secret")


if __name__ == "__main__":
    unittest.main()
