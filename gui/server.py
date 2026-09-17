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
from urllib.parse import parse_qs, unquote, urlparse

from workflow_engine import (
    PROJECT_ROOT,
    WorkflowCancelled,
    default_configuration,
    load_registry,
    run_workflow,
)
from incremental_edge_cache import current_cache_counts
from evidence_inspector import inspect_edge_evidence, inspect_node_evidence
from session_report import build_session_report


WEB_ROOT = Path(__file__).resolve().parent / "web"
MAX_AUTOMATIC_EXPORT_INTERPRETATIONS = 1000


def utc_timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def _evidence_payload_key(payload: dict[str, Any]) -> str:
    """Return the same stable hypothesis key used by the browser inspector."""

    if payload.get("kind") == "node":
        return f"node:{str(payload.get('symbol', '')).casefold()}"
    left, right = sorted(
        (str(payload.get("node_a", "")), str(payload.get("node_b", ""))),
        key=str.casefold,
    )
    return f"edge:{left.casefold()}|{right.casefold()}"


def _materialize_export_interpretations(
    job_payload: dict[str, Any],
    output_directory: Path,
    history: list[dict[str, Any]],
    client_state: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Freeze evidence ledgers for every node/edge visible in the saved graph.

    A live GUI can ask Python to interpret any hypothesis.  A standalone HTML
    cannot.  The export therefore snapshots all ledgers in the currently
    visible top-path network, in addition to hypotheses the scientist already
    opened.  This gives every clickable mark in the archived network the same
    evidence-transparency behavior it had in the live application.
    """

    catalog: dict[str, dict[str, Any]] = {}
    for item in history:
        if item.get("kind") in {"node", "edge"}:
            catalog[_evidence_payload_key(item)] = item

    preview = job_payload.get("preview") or {}
    network = preview.get("path_network") or {}
    available_limit = int(network.get("visualized_path_count") or 0)
    requested_limit = int(
        client_state.get("visible_path_rank_limit") or min(50, available_limit)
    )
    visible_limit = min(max(requested_limit, 0), available_limit)

    def visible(item: dict[str, Any]) -> bool:
        ranks = [int(value) for value in item.get("path_ranks", [])]
        return not ranks or any(rank <= visible_limit for rank in ranks)

    candidates: list[tuple[str, tuple[str, ...]]] = []
    for node in network.get("nodes", []):
        if visible(node) and node.get("posterior_available"):
            candidates.append(("node", (str(node.get("id", "")),)))
    for edge in network.get("edges", []):
        if visible(edge):
            candidates.append(
                (
                    "edge",
                    (str(edge.get("node_a", "")), str(edge.get("node_b", ""))),
                )
            )

    errors: list[str] = []
    automatic_count = 0
    for kind, symbols in candidates:
        stub = (
            {"kind": "node", "symbol": symbols[0]}
            if kind == "node"
            else {"kind": "edge", "node_a": symbols[0], "node_b": symbols[1]}
        )
        key = _evidence_payload_key(stub)
        if key in catalog:
            continue
        if automatic_count >= MAX_AUTOMATIC_EXPORT_INTERPRETATIONS:
            errors.append(
                "The automatic offline interpretation catalog reached its "
                f"safety limit of {MAX_AUTOMATIC_EXPORT_INTERPRETATIONS} hypotheses."
            )
            break
        try:
            if kind == "node":
                result = inspect_node_evidence(
                    output_directory,
                    symbols[0],
                    project_root=PROJECT_ROOT,
                )
            else:
                result = inspect_edge_evidence(
                    output_directory,
                    symbols[0],
                    symbols[1],
                    project_root=PROJECT_ROOT,
                )
            result["export_catalog_source"] = "visible_top_path_network"
            catalog[key] = result
            automatic_count += 1
        except Exception as exc:  # noqa: BLE001 - one ledger must not abort the archive
            errors.append(f"{kind} {' — '.join(symbols)}: {exc}")

    scope = {
        "visible_path_rank_limit": visible_limit,
        "manual_history_count": len(history),
        "automatic_network_ledger_count": automatic_count,
        "catalog_hypothesis_count": len(catalog),
        "coverage": (
            "Every inspectable node and edge in the network view saved at export "
            "time, plus every hypothesis manually inspected in this browser session."
        ),
    }
    return list(catalog.values()), scope, errors


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
    created_at: str = field(default_factory=utc_timestamp)
    started_at: str | None = None
    finished_at: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
ANALYSIS_LOCK = threading.Lock()
TERMINAL_JOB_STATUSES = {"complete", "failed", "cancelled"}


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
        "cancel_requested": job.cancel_event.is_set(),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def execute_job(job_id: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]

    def check_cancel() -> None:
        if job.cancel_event.is_set():
            raise WorkflowCancelled("Analysis cancelled by user")

    def progress(message: str, fraction: float) -> None:
        check_cancel()
        with JOBS_LOCK:
            current = JOBS[job_id]
            current.message = message
            current.progress = max(0.0, min(1.0, float(fraction)))

    lock_acquired = False
    try:
        while not ANALYSIS_LOCK.acquire(timeout=0.2):
            check_cancel()
            with JOBS_LOCK:
                current = JOBS[job_id]
                current.status = "queued"
                current.message = "Waiting for current analysis"
        lock_acquired = True
        check_cancel()
        with JOBS_LOCK:
            current = JOBS[job_id]
            current.status = "running"
            current.message = "Starting"
            current.started_at = utc_timestamp()
        result = run_workflow(
            job.configuration,
            project_root=PROJECT_ROOT,
            run_id=job_id,
            progress=progress,
            cancel_requested=job.cancel_event.is_set,
        )
        check_cancel()
        with JOBS_LOCK:
            job = JOBS[job_id]
            job.status = "complete"
            job.message = "Complete"
            job.progress = 1.0
            job.preview = result.preview
            job.summary = result.summary
            job.output_directory = str(result.output_directory)
            job.finished_at = utc_timestamp()
    except WorkflowCancelled:
        with JOBS_LOCK:
            job = JOBS[job_id]
            job.status = "cancelled"
            job.message = "Cancelled"
            job.error = None
            job.finished_at = utc_timestamp()
    except Exception as exc:  # noqa: BLE001 - boundary must report scientific failures
        traceback.print_exc()
        with JOBS_LOCK:
            job = JOBS[job_id]
            job.status = "failed"
            job.message = "Analysis failed"
            job.error = str(exc)
            job.finished_at = utc_timestamp()
    finally:
        if lock_acquired:
            ANALYSIS_LOCK.release()


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
            with JOBS_LOCK:
                active_job = next(
                    (
                        public_job(job)
                        for job in JOBS.values()
                        if job.status not in TERMINAL_JOB_STATUSES
                    ),
                    None,
                )
            self.send_json(
                {
                    "registry": registry,
                    "defaults": default_configuration(registry),
                    "active_job": active_job,
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
        if path == "/api/jobs/active":
            with JOBS_LOCK:
                active_job = next(
                    (
                        public_job(job)
                        for job in JOBS.values()
                        if job.status not in TERMINAL_JOB_STATUSES
                    ),
                    None,
                )
            self.send_json({"active_job": active_job})
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
            if len(parts) == 5 and parts[3] == "evidence":
                self.send_evidence_inspection(parts[2], parts[4], parsed.query)
                return
        if path.startswith("/api/"):
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_static(path)

    def send_evidence_inspection(
        self,
        job_id: str,
        evidence_kind: str,
        raw_query: str,
    ) -> None:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            status = job.status if job else None
            output_directory = (
                Path(job.output_directory)
                if job and job.output_directory
                else None
            )
        if job is None:
            self.send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
            return
        if status != "complete" or output_directory is None:
            self.send_json(
                {"error": "Evidence can be inspected only after the run is complete"},
                HTTPStatus.CONFLICT,
            )
            return
        query = parse_qs(raw_query, keep_blank_values=True)

        def one(name: str) -> str:
            value = query.get(name, [""])[0].strip()
            if len(value) > 160:
                raise ValueError(f"{name} is too long")
            return value

        try:
            if evidence_kind == "node":
                payload = inspect_node_evidence(
                    output_directory,
                    one("symbol"),
                    project_root=PROJECT_ROOT,
                )
            elif evidence_kind == "edge":
                payload = inspect_edge_evidence(
                    output_directory,
                    one("node_a"),
                    one("node_b"),
                    project_root=PROJECT_ROOT,
                )
            else:
                self.send_json({"error": "unknown evidence kind"}, HTTPStatus.NOT_FOUND)
                return
            self.send_json(payload)
        except (ValueError, FileNotFoundError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        parts = [part for part in path.split("/") if part]
        if (
            len(parts) == 4
            and parts[:2] == ["api", "jobs"]
            and parts[3] == "session-report"
        ):
            try:
                payload = self.read_json()
                self.create_session_report(parts[2], payload)
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if len(parts) == 4 and parts[:2] == ["api", "jobs"] and parts[3] == "cancel":
            job_id = parts[2]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if job is None:
                    payload = None
                elif job.status not in TERMINAL_JOB_STATUSES:
                    job.cancel_event.set()
                    job.status = "cancelling"
                    job.message = "Cancellation requested"
                    payload = public_job(job)
                else:
                    payload = public_job(job)
            if payload is None:
                self.send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
            else:
                self.send_json(payload, HTTPStatus.ACCEPTED)
            return
        if path != "/api/jobs":
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
                active = next(
                    (
                        current
                        for current in JOBS.values()
                        if current.status not in TERMINAL_JOB_STATUSES
                    ),
                    None,
                )
                if active is None:
                    JOBS[job_id] = job
                    conflict = None
                else:
                    conflict = public_job(active)
            if conflict is not None:
                self.send_json(
                    {
                        "error": (
                            "Another analysis is already active. Cancel it before "
                            "starting a new run."
                        ),
                        "active_job": conflict,
                    },
                    HTTPStatus.CONFLICT,
                )
                return
            thread = threading.Thread(target=execute_job, args=(job_id,), daemon=True)
            self.send_json(public_job(job), HTTPStatus.ACCEPTED)
            thread.start()
        except (ValueError, json.JSONDecodeError) as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def create_session_report(self, job_id: str, payload: dict[str, Any]) -> None:
        """Create one portable HTML report without mutating scientific results."""

        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job is None:
                job_payload = None
                configuration = None
                output_directory = None
            else:
                # JSON round-tripping makes a stable snapshot of nested mutable
                # dictionaries before report generation begins on this request
                # thread.
                job_payload = json.loads(json.dumps(public_job(job), default=str))
                configuration = json.loads(json.dumps(job.configuration, default=str))
                output_directory = (
                    Path(job.output_directory) if job.output_directory else None
                )
        if job_payload is None:
            self.send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
            return
        if job_payload["status"] != "complete" or output_directory is None:
            self.send_json(
                {"error": "A complete session can be exported only after the run finishes"},
                HTTPStatus.CONFLICT,
            )
            return

        inspections = payload.get("inspection_history", [])
        client_state = payload.get("client_state", {})
        if not isinstance(inspections, list) or not all(
            isinstance(item, dict) for item in inspections
        ):
            raise ValueError("inspection_history must be a list of objects")
        if len(inspections) > 100:
            raise ValueError("inspection_history may contain at most 100 hypotheses")
        if not isinstance(client_state, dict):
            raise ValueError("client_state must be an object")

        interpretation_catalog, interpretation_scope, interpretation_errors = (
            _materialize_export_interpretations(
                job_payload,
                output_directory,
                inspections,
                client_state,
            )
        )

        session = {
            "schema_version": 2,
            "application": "Graphical Bayesian Inference Version 1",
            "job": job_payload,
            "configuration": configuration,
            "registry": load_registry(PROJECT_ROOT),
            "client_state": client_state,
            "inspection_history": inspections,
            "interpretation_catalog": interpretation_catalog,
            "interpretation_scope": interpretation_scope,
            "interpretation_errors": interpretation_errors,
        }
        try:
            report = build_session_report(session, output_directory)
        except (OSError, RuntimeError, ValueError) as exc:
            self.send_json(
                {"error": f"Unable to create the session report: {exc}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )
            return
        report["url"] = (
            f"/api/jobs/{job_id}/files/{report['file']}"
        )
        self.send_json(report, HTTPStatus.CREATED)

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
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
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
