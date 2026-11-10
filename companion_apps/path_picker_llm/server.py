#!/usr/bin/env python3
"""Local HTTP server for the standalone Path Picker workbench."""

from __future__ import annotations

import argparse
import copy
import json
import math
import mimetypes
import sqlite3
import tempfile
import threading
import traceback
import uuid
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from path_picker.database import RunDatabase
from path_picker.usage import default_pricing, summarize_usage
from path_picker.workflow import Cancelled, PathPickerWorkflow


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
RUNS = ROOT / "runs"
DEFAULT_INPUT = ""
DEFAULT_KNOWLEDGE = ROOT / "runtime" / "shared_knowledge" / "evidence.sqlite3"
DEFAULT_SEED_RUN = RUNS / "20261109_174322_71b2484f" / "path_picker.sqlite3"
DOWNLOAD_FILES = {
    "inventory.json", "shortlist.json", "llm_handoff.json", "report.html",
    "path_picker.sqlite3", "path_coverage.json", "exclusion_ledger.json", "api_usage.jsonl", "configuration.json",
    "rag_audit.json", "rag_reviews.jsonl",
}
DEFAULT_CONFIG = {
    "workflow_mode": "rag_path_first",
    "rag_model": "gpt-6-sol", "rag_reasoning": "medium",
    "rag_kb_path": str(DEFAULT_KNOWLEDGE),
    "rag_seed_run": str(DEFAULT_SEED_RUN) if DEFAULT_SEED_RUN.is_file() else "",
    "rag_max_rounds": 2, "rag_claim_limit": 40, "rag_context_chars": 24000,
    "rag_max_new_claims": 12, "rag_max_investigations": 8,
    "api_base_url": "https://songjj-5910-resource.services.ai.azure.com/openai/v1",
    "screening_model": "gpt-6-luna", "research_model": "gpt-6-luna", "verification_model": "gpt-6-sol",
    "reasoning_model": "gpt-6-sol", "synthesis_model": "gpt-6-sol",
    "screening_reasoning": "medium",
    "research_reasoning": "medium", "verification_reasoning": "high",
    "path_reasoning": "high", "synthesis_reasoning": "high",
    "web_search_context_size": "high", "timeout_seconds": 300,
    "source_timeout_seconds": 30, "node_batch_size": 2, "edge_batch_size": 1,
    "max_deep_path_reviews": 50, "shortlist_minimum": 5, "shortlist_maximum": 12,
    "path_round_size": 10, "max_research_items": 2000,
    "exclusion_max_paths": 50, "exclusion_max_fraction": 0.1,
    "max_output_tokens": 32000, "max_tool_calls": 6, "max_total_api_calls": 10000,
    "pricing": default_pricing(),
}


@dataclass
class Job:
    run_id: str
    directory: Path
    mode: str
    status: str = "queued"
    message: str = "Queued"
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event, repr=False)


JOBS: dict[str, Job] = {}
LOCK = threading.Lock()


def discover_runs() -> None:
    """Rehydrate prior run summaries without recovering API keys."""

    if not RUNS.is_dir():
        return
    for directory in RUNS.iterdir():
        database_path = directory / "path_picker.sqlite3"
        if not directory.is_dir() or not database_path.is_file():
            continue
        database = RunDatabase(database_path)
        status = str(database.get_meta("status", "needs_attention"))
        mode = "inventory" if status == "inventory_complete" else "production"
        message = {
            "inventory_complete": "Inventory ready; enter an API key to resume production",
            "complete": "Shortlist and pathway abstracts ready",
            "cancelled": "Cancelled safely; completed tasks remain resumable",
            "needs_attention": "Stopped; inspect the error and resume",
            "running": "Previous process ended; resume from checkpoints",
        }.get(status, status.replace("_", " "))
        if status == "running":
            status = "needs_attention"
            database.set_meta("status", status)
        JOBS[directory.name] = Job(directory.name, directory, mode, status, message)


def public_job(job: Job) -> dict[str, Any]:
    database = RunDatabase(job.directory / "path_picker.sqlite3") if (job.directory / "path_picker.sqlite3").exists() else None
    progress = database.progress() if database else {}
    inventory = database.get_meta("inventory", {}, decode=True) if database else {}
    adaptive, exclusions = {}, []
    if database:
        from path_picker.adaptive import adaptive_progress, list_exclusions
        adaptive = adaptive_progress(database)
        exclusions = list_exclusions(database)
    files = [name for name in sorted(DOWNLOAD_FILES) if (job.directory / name).exists()]
    configuration = database.get_meta("configuration", {}, decode=True) if database else {}
    rag = {}
    if database and configuration.get("workflow_mode") == "rag_path_first":
        rag = {key: database.get_meta("rag_" + key, {}, decode=True)
               for key in ("stage", "progress", "seed_summary", "knowledge_summary")}
        rag["knowledge"] = knowledge_snapshot(configuration.get("rag_kb_path") or DEFAULT_KNOWLEDGE)
    return {
        "run_id": job.run_id, "mode": job.mode, "status": job.status, "message": job.message,
        "error": job.error, "progress": progress, "inventory": inventory, "files": files,
        "adaptive": adaptive, "exclusions": exclusions, "rag": rag,
        "input_path": database.get_meta("input_path", "") if database else "",
        "configuration": configuration,
        "usage": summarize_usage(job.directory / "api_usage.jsonl", configuration),
        "report_stale": database.get_meta("shortlist_stale", "false") == "true" if database else False,
    }


def knowledge_snapshot(path: str | Path = DEFAULT_KNOWLEDGE) -> dict[str, Any]:
    """Inspect the shared library without creating, seeding, or migrating it."""
    try:
        from path_picker.knowledge import knowledge_summary
        return knowledge_summary(Path(path))
    except (ImportError, OSError, sqlite3.Error, ValueError) as exc:
        return {"path": str(path), "exists": Path(path).is_file(), "error": str(exc)}


def validate_pricing(pricing: Any) -> None:
    """Reject invalid manual rates; null deliberately means an unknown rate."""
    if not isinstance(pricing, dict):
        raise ValueError("pricing must be an object")
    if pricing.get("currency", "USD") != "USD":
        raise ValueError("Pricing must use USD")

    def rate(value: Any, name: str) -> None:
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
            raise ValueError(f"{name} must be a finite nonnegative number or null for unknown")

    models = pricing.get("models", {})
    if not isinstance(models, dict):
        raise ValueError("pricing.models must be an object")
    rate_names = ("input_per_million", "cached_input_per_million", "cache_write_per_million", "output_per_million")
    for model, values in models.items():
        if not isinstance(model, str) or not model.strip() or not isinstance(values, dict):
            raise ValueError("Each pricing model requires an exact name and a rates object")
        for name in rate_names:
            if name in values:
                rate(values[name], f"{model}.{name}")
    search = pricing.get("search", {})
    if not isinstance(search, dict):
        raise ValueError("pricing.search must be an object")
    for name in ("azure_per_thousand", "openai_per_thousand"):
        if name in search:
            rate(search[name], f"search.{name}")
    long_context = pricing.get("long_context", {})
    if not isinstance(long_context, dict):
        raise ValueError("pricing.long_context must be an object")
    for name in ("threshold_input_tokens", "input_multiplier", "output_multiplier"):
        if name in long_context:
            rate(long_context[name], f"long_context.{name}")


def normalize(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    input_path = str(payload.get("input_path") or DEFAULT_INPUT).strip()
    if not input_path:
        raise ValueError("Choose a BGI run, agent graph, or archive first; no default archive is assumed")
    if not Path(input_path).exists():
        raise ValueError(f"Input does not exist: {input_path}")
    config = copy.deepcopy(DEFAULT_CONFIG)
    config.update(payload.get("configuration") or {})
    for field_name in ("screening_model", "research_model", "verification_model", "reasoning_model", "synthesis_model", "rag_model"):
        config[field_name] = str(config[field_name]).strip()
        if not config[field_name]:
            raise ValueError(f"{field_name} is required")
    config["max_deep_path_reviews"] = min(2000, max(5, int(config["max_deep_path_reviews"])))
    config["shortlist_minimum"] = min(12, max(5, int(config["shortlist_minimum"])))
    config["shortlist_maximum"] = min(12, max(config["shortlist_minimum"], int(config["shortlist_maximum"])))
    if config["workflow_mode"] not in {"rag_path_first", "path_first", "evidence_first"}:
        raise ValueError("workflow_mode must be rag_path_first, path_first, or evidence_first")
    if config["rag_reasoning"] not in ("low", "medium", "high"):
        raise ValueError("rag_reasoning must be low, medium, or high")
    for field_name in ("rag_kb_path", "rag_seed_run"):
        config[field_name] = str(config[field_name] or "").strip()
    if config["workflow_mode"] == "rag_path_first":
        if not config["rag_kb_path"]:
            raise ValueError("rag_kb_path is required")
        kb_path = Path(config["rag_kb_path"])
        if not kb_path.is_absolute() or kb_path.is_dir():
            raise ValueError("rag_kb_path must be an absolute SQLite file path")
        if config["rag_seed_run"] and not Path(config["rag_seed_run"]).is_file():
            raise ValueError("rag_seed_run must identify an existing run SQLite file, or be blank")
        if config["rag_seed_run"] and kb_path.resolve() == Path(config["rag_seed_run"]).resolve():
            raise ValueError("The shared knowledge library must not overwrite the seed run")
    for field_name, low, high in (("rag_max_rounds", 1, 2), ("rag_claim_limit", 1, 500),
                                 ("rag_context_chars", 1000, 200000), ("rag_max_new_claims", 1, 100),
                                 ("rag_max_investigations", 1, 100)):
        raw = config[field_name]
        try:
            value = int(raw)
            valid = not isinstance(raw, bool) and value == float(raw) and low <= value <= high
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"{field_name} must be an integer between {low} and {high}")
        config[field_name] = value
    if config["research_reasoning"] not in ("low", "medium", "high"):
        raise ValueError("research_reasoning must be low, medium, or high")
    for field_name in ("node_batch_size", "edge_batch_size"):
        raw = config[field_name]
        try:
            value = int(raw)
            valid = not isinstance(raw, bool) and value == float(raw) and 1 <= value <= 50
        except (TypeError, ValueError, OverflowError):
            valid = False
        if not valid:
            raise ValueError(f"{field_name} must be an integer between 1 and 50")
        config[field_name] = value
    for field_name, low, high in (
        ("path_round_size", 1, 100), ("max_research_items", 1, 1000000),
        ("exclusion_max_paths", 1, 1000000), ("max_output_tokens", 256, 128000),
        ("max_tool_calls", 1, 100), ("max_total_api_calls", 1, 10000000),
    ):
        value = int(config[field_name])
        if not low <= value <= high:
            raise ValueError(f"{field_name} must be between {low} and {high}")
        config[field_name] = value
    config["exclusion_max_fraction"] = float(config["exclusion_max_fraction"])
    if not 0 < config["exclusion_max_fraction"] <= 1:
        raise ValueError("exclusion_max_fraction must be greater than 0 and at most 1")
    validate_pricing(config["pricing"])
    return input_path, config


def run_job(job: Job, input_path: str, config: dict[str, Any], api_key: str, inventory_only: bool) -> None:
    job.status = "running"
    job.message = "Importing complete BGI agent graphs" if inventory_only else "Running evidence-grounded workflow"
    try:
        workflow = PathPickerWorkflow(run_directory=job.directory, input_path=input_path, configuration=config, api_key=api_key, cancel_event=job.cancel)
        result = workflow.run(inventory_only=inventory_only)
        job.status = str(workflow.database.get_meta("status", "inventory_complete" if inventory_only else "complete"))
        job.message = "Inventory ready; verify the scope before spending" if inventory_only else (
            "Shortlist and pathway abstracts ready" if job.status == "complete" else str((result or {}).get("message", "Run stopped with saved checkpoints; inspect coverage"))
        )
    except Cancelled as exc:
        job.status, job.message = "cancelled", str(exc)
        if (job.directory / "path_picker.sqlite3").exists():
            RunDatabase(job.directory / "path_picker.sqlite3").set_meta("status", job.status)
    except Exception as exc:
        job.status, job.error, job.message = "needs_attention", traceback.format_exc(), str(exc)
        if (job.directory / "path_picker.sqlite3").exists():
            RunDatabase(job.directory / "path_picker.sqlite3").set_meta("status", job.status)


def start(payload: dict[str, Any], *, resume_id: str | None = None) -> Job:
    mode = str(payload.get("mode", "inventory"))
    if mode not in {"inventory", "production"}:
        raise ValueError("mode must be inventory or production")
    if resume_id:
        directory = (RUNS / resume_id).resolve()
        if directory.parent != RUNS.resolve() or not directory.is_dir():
            raise ValueError("Run not found")
        saved = RunDatabase(directory / "path_picker.sqlite3")
        saved_input = str(saved.get_meta("input_path", ""))
        entered_input = str(payload.get("input_path") or saved_input)
        if saved_input and Path(entered_input).resolve() != Path(saved_input).resolve():
            raise ValueError("This checkpoint belongs to a different input. Create a new inventory instead")
        saved_configuration = saved.get_meta("configuration", {}, decode=True)
        # An omitted mode in an old checkpoint is legacy, never the new default.
        saved_mode = saved_configuration.get("workflow_mode", "path_first")
        requested_mode = (payload.get("configuration") or {}).get("workflow_mode", saved_mode)
        if requested_mode != saved_mode:
            raise ValueError("A saved run keeps its workflow mode. Create a fresh inventory for RAG or another workflow; the old checkpoints remain unchanged")
        saved_configuration = {**saved_configuration, "workflow_mode": saved_mode}
        payload = {**payload, "input_path": entered_input,
                   "configuration": {**saved_configuration, **(payload.get("configuration") or {})}}
    input_path, config = normalize(payload)
    api_key = str(payload.get("api_key") or "").strip()
    if mode == "production" and not api_key:
        raise ValueError("Production mode requires an API key")
    with LOCK:
        active = next((item for item in JOBS.values() if item.status in {"queued", "running"}), None)
        if active:
            raise RuntimeError(f"Run {active.run_id} is already active")
        if resume_id:
            directory = (RUNS / resume_id).resolve()
            if directory.parent != RUNS.resolve() or not directory.is_dir():
                raise ValueError("Run not found")
            run_id = resume_id
        else:
            run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
            directory = RUNS / run_id
            directory.mkdir(parents=True, exist_ok=False)
        job = Job(run_id, directory, mode)
        JOBS[run_id] = job
    threading.Thread(target=run_job, args=(job, input_path, config, api_key, mode == "inventory"), daemon=True).start()
    return job


def database_snapshot(path: Path) -> bytes:
    """Download a transaction-consistent SQLite backup, even while work continues."""
    with tempfile.TemporaryDirectory(prefix="path_picker_snapshot_") as temporary:
        snapshot = Path(temporary) / "path_picker.sqlite3"
        source = sqlite3.connect(str(path), timeout=60)
        destination = sqlite3.connect(str(snapshot))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        return snapshot.read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "PathPicker/0.2"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, status: int, value: Any) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.send_header("Cache-Control", "no-store")
        self.end_headers(); self.wfile.write(body)

    def body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        if path == "/api/health":
            self.send_json(200, {"application": "path-picker-llm", "version": "0.2.0"}); return
        if path == "/api/defaults":
            self.send_json(200, {"input_path": str(DEFAULT_INPUT), "configuration": DEFAULT_CONFIG}); return
        if path == "/api/knowledge":
            self.send_json(200, knowledge_snapshot(DEFAULT_KNOWLEDGE)); return
        if path == "/api/runs":
            with LOCK:
                values = [public_job(job) for job in sorted(JOBS.values(), key=lambda item: item.run_id, reverse=True)]
            self.send_json(200, values); return
        match = __import__("re").fullmatch(r"/api/runs/([^/]+)", path)
        if match:
            with LOCK: job = JOBS.get(match.group(1))
            if job: self.send_json(200, public_job(job))
            else: self.send_json(404, {"error": "Run not found"})
            return
        file_match = __import__("re").fullmatch(r"/runs/([^/]+)/([^/]+)", path)
        if file_match:
            run_id, name = file_match.groups()
            if name not in DOWNLOAD_FILES:
                self.send_json(404, {"error": "File not found"}); return
            target = (RUNS / run_id / name).resolve()
            if target.parent.parent != RUNS.resolve():
                self.send_json(404, {"error": "File not found"}); return
            if not target.is_file(): self.send_json(404, {"error": "File not found"}); return
            body = database_snapshot(target) if name == "path_picker.sqlite3" else target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(name)[0] or "application/octet-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        target = WEB / ("index.html" if path == "/" else path.lstrip("/"))
        if not target.is_file() or WEB.resolve() not in target.resolve().parents:
            self.send_json(404, {"error": "Not found"}); return
        body = target.read_bytes(); self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/start":
                self.send_json(202, public_job(start(self.body()))); return
            match = __import__("re").fullmatch(r"/api/runs/([^/]+)/resume", path)
            if match:
                payload = self.body(); payload["mode"] = "production"
                self.send_json(202, public_job(start(payload, resume_id=match.group(1)))); return
            match = __import__("re").fullmatch(r"/api/runs/([^/]+)/cancel", path)
            if match:
                with LOCK: job = JOBS.get(match.group(1))
                if not job: self.send_json(404, {"error": "Run not found"}); return
                job.cancel.set()
                if (job.directory / "path_picker.sqlite3").exists(): RunDatabase(job.directory / "path_picker.sqlite3").set_meta("cancel_requested", "true")
                self.send_json(202, public_job(job)); return
            match = __import__("re").fullmatch(r"/api/runs/([^/]+)/exclusions/([^/]+)/revoke", path)
            if match:
                with LOCK:
                    job = JOBS.get(match.group(1))
                    if not job:
                        self.send_json(404, {"error": "Run not found"}); return
                    if job.status in {"queued", "running"}:
                        raise ValueError("Cancel safely and wait for the run to stop before revoking an exclusion")
                    from path_picker.adaptive import revoke_exclusion
                    database = RunDatabase(job.directory / "path_picker.sqlite3")
                    revoke_exclusion(database, match.group(2))
                    job.status = "needs_attention"
                    job.message = "Exclusion revoked. Resume to reassess restored paths and regenerate the report"
                    database.set_meta("status", "needs_attention")
                self.send_json(200, public_job(job)); return
            self.send_json(404, {"error": "Not found"})
        except Exception as exc:
            self.send_json(400, {"error": str(exc)})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--no-browser", action="store_true"); args = parser.parse_args()
    RUNS.mkdir(exist_ok=True); discover_runs()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    if not args.no_browser: threading.Timer(0.5, lambda: webbrowser.open(f"http://{args.host}:{args.port}/")).start()
    print(f"Path Picker LLM workbench: http://{args.host}:{args.port}/", flush=True)
    server.serve_forever(); return 0


if __name__ == "__main__": raise SystemExit(main())

