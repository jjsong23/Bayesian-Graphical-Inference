"""Regression tests for cooperative workbench job cancellation."""

from __future__ import annotations

import json
import shutil
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import server


class ServerCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        with server.JOBS_LOCK:
            server.JOBS.clear()
        self.assertFalse(server.ANALYSIS_LOCK.locked())

    def tearDown(self) -> None:
        with server.JOBS_LOCK:
            server.JOBS.clear()
        if server.ANALYSIS_LOCK.locked():
            server.ANALYSIS_LOCK.release()

    def test_running_job_cancels_at_workflow_checkpoint(self) -> None:
        started = threading.Event()

        def cancellable_workflow(*args, cancel_requested=None, **kwargs):
            started.set()
            while not cancel_requested():
                time.sleep(0.005)
            raise server.WorkflowCancelled("cancelled in test")

        job = server.Job(job_id="running-cancel", configuration={})
        with server.JOBS_LOCK:
            server.JOBS[job.job_id] = job
        with patch.object(server, "run_workflow", side_effect=cancellable_workflow):
            worker = threading.Thread(target=server.execute_job, args=(job.job_id,))
            worker.start()
            self.assertTrue(started.wait(timeout=2))
            job.cancel_event.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(job.status, "cancelled")
        self.assertEqual(job.message, "Cancelled")
        self.assertIsNone(job.error)

    def test_waiting_job_can_cancel_before_workflow_starts(self) -> None:
        job = server.Job(job_id="queued-cancel", configuration={})
        with server.JOBS_LOCK:
            server.JOBS[job.job_id] = job
        server.ANALYSIS_LOCK.acquire()
        with patch.object(server, "run_workflow") as mocked_workflow:
            worker = threading.Thread(target=server.execute_job, args=(job.job_id,))
            worker.start()
            job.cancel_event.set()
            worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        mocked_workflow.assert_not_called()
        self.assertEqual(job.status, "cancelled")

    def test_latest_completed_run_can_be_recovered_after_restart(self) -> None:
        root = Path(__file__).parent / f".server_recovery_test_{uuid.uuid4().hex}"
        try:
            run = root / "results" / "gui_runs" / "run-123"
            run.mkdir(parents=True)
            (run / "configuration.json").write_text(
                json.dumps({"path": {"start": "Prkaca", "target": "Aqp2"}}),
                encoding="utf-8",
            )
            (run / "analysis_summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "generated_at": "2026-09-25T12:00:00-04:00",
                        "path_finding": {"paths_found": 50},
                        "outputs": ["configuration.json"],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(server, "PROJECT_ROOT", root):
                recovered = server.recover_latest_completed_job()
        finally:
            shutil.rmtree(root, ignore_errors=True)

        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.job_id, "run-123")
        self.assertEqual(recovered.status, "complete")
        self.assertEqual(recovered.preview["metrics"]["ranked_paths"], 50)
        self.assertEqual(server.current_or_latest_job_payload()["job_id"], "run-123")


if __name__ == "__main__":
    unittest.main()
