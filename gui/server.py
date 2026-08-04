#!/usr/bin/env python3
"""Local HTTP server for the Graphical Bayesian Inference workbench."""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import traceback
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from workflow_engine import (
    PROJECT_ROOT,
    default_configuration,
    load_registry,
    run_workflow,
)
from incremental_edge_cache import current_cache_counts


WEB_ROOT = Path(__file__).resolve().parent / "web"


@dataclass
class Job:
    job_id: str
    status: str = "queued"
    message: str = "Queued"
    progress: float = 0.0
    configuration: dict[str, Any] = field(default_factory=dict)
    preview: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    output_directory: str | None = None
    error: str | None = None


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
ANALYSIS_LOCK = threading.Lock()


def public_job(job: Job) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "status": job.status,
        "message": job.message,
        "progress": job.progress,
        "preview": job.preview,
        "summary": job.summary,
        "files": job.preview.get("files", []) if job.preview else [],
        "error": job.error,
    }


def execute_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.status = "running"
        job.message = "Starting"

    def progress(message: str, fraction: float) -> None:
        with JOBS_LOCK:
            current = JOBS[job_id]
            current.message = message
            current.progress = max(0.0, min(1.0, float(fraction)))

    try:
        with ANALYSIS_LOCK:
            result = run_workflow(
                job.configuration,
                project_root=PROJECT_ROOT,
                run_id=job_id,
                progress=progress,
            )
        with JOBS_LOCK:
            job = JOBS[job_id]
            job.status = "complete"
            job.message = "Complete"
            job.progress = 1.0
            job.preview = result.preview
            job.summary = result.summary
            job.output_directory = str(result.output_directory)
    except Exception as exc:  # noqa: BLE001 - boundary must report scientific failures
        traceback.print_exc()
        with JOBS_LOCK:
            job = JOBS[job_id]
            job.status = "failed"
            job.message = "Analysis failed"
            job.error = str(exc)


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "GBIWorkbench/1.0"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 2_000_000:
            raise ValueError("request body is empty or too large")
        raw = self.rfile.read(length)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            self.send_json({"status": "ok"})
            return
        if path == "/api/config":
            registry = load_registry(PROJECT_ROOT)
            cache = current_cache_counts(PROJECT_ROOT)
            self.send_json(
                {
                    "registry": registry,
                    "defaults": default_configuration(registry),
                    "project": {
                        "name": "Graphical Bayesian Inference",
                        "node_candidates": 9170,
                        "seed_catalog_nodes": 891,
                        "seed_unique_pairs": 396495,
                        "cached_incremental_pairs": cache["incremental_pairs"],
                        "cached_pair_hypotheses": (
                            cache["seed_pairs"] + cache["incremental_pairs"]
                        ),
                        "cached_incremental_nodes": cache["profiled_nodes"],
                    },
                }
            )
            return
        if path.startswith("/api/jobs/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) == 3:
                job_id = parts[2]
                with JOBS_LOCK:
                    job = JOBS.get(job_id)
                    payload = public_job(job) if job else None
                if payload is None:
                    self.send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
                else:
                    self.send_json(payload)
                return
            if len(parts) == 5 and parts[3] == "files":
                self.send_job_file(parts[2], parts[4])
                return
        if path.startswith("/api/"):
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_static(path)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/jobs":
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            payload = self.read_json()
            configuration = payload.get("configuration", payload)
            if not isinstance(configuration, dict):
                raise ValueError("configuration must be an object")
            job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
            job = Job(job_id=job_id, configuration=configuration)
            with JOBS_LOCK:
                JOBS[job_id] = job
            thread = threading.Thread(target=execute_job, args=(job_id,), daemon=True)
            thread.start()
            self.send_json(public_job(job), HTTPStatus.ACCEPTED)
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def send_job_file(self, job_id: str, requested_name: str) -> None:
        safe_requested = Path(requested_name).name
        if safe_requested != requested_name:
            self.send_json({"error": "invalid file name"}, HTTPStatus.BAD_REQUEST)
            return
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            output_directory = Path(job.output_directory) if job and job.output_directory else None
        if output_directory is None:
            self.send_json({"error": "job output is not available"}, HTTPStatus.NOT_FOUND)
            return
        file_path = (output_directory / safe_requested).resolve()
        if file_path.parent != output_directory.resolve() or not file_path.is_file():
            self.send_json({"error": "file not found"}, HTTPStatus.NOT_FOUND)
            return
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
        self.end_headers()
        with file_path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                self.wfile.write(block)

    def send_static(self, requested_path: str) -> None:
        relative = requested_path.lstrip("/") or "index.html"
        file_path = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in file_path.parents and file_path != WEB_ROOT.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if file_path.is_dir():
            file_path = file_path / "index.html"
        if not file_path.is_file():
            file_path = WEB_ROOT / "index.html"
        body = file_path.read_bytes()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = ThreadingHTTPServer((args.host, args.port), WorkbenchHandler)
    url = f"http://{args.host}:{server.server_port}"
    print(f"Graphical Bayesian Inference workbench: {url}")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
