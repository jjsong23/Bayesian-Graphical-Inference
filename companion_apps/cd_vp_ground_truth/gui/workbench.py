"""Asynchronous GUI adapter for ground-truth curation protocol v0.6."""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CODE_ROOT = PROJECT_ROOT / "code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from ground_truth.pipeline import GroundTruthCancelled, GroundTruthPipeline, dry_run  # noqa: E402
from ground_truth.responses import responses_url  # noqa: E402


CONFIG_PATH = PROJECT_ROOT / "configs" / "ground_truth_v0.6.example.json"
PROTOCOL_VERSION = "0.6"
RUN_ROOT = PROJECT_ROOT / "results" / "ground_truth" / "gui_runs"
TERMINAL_STATUSES = {"complete", "failed", "cancelled"}
DOWNLOAD_FILES = {
    "configuration.json",
    "dry_run.json",
    "edges.jsonl",
    "edges.tsv",
    "evidence_stream_references.jsonl",
    "evidence_stream_references.tsv",
    "positive_references.jsonl",
    "negative_references.jsonl",
    "audit.tsv",
    "nodes.jsonl",
    "qa_metrics.json",
    "run_manifest.json",
    "reference_sequences.json",
    "node_candidates.json",
    "node_generation_checkpoint.json",
    "edge_candidates.json",
    "edge_generation_checkpoint.json",
    "pair_generation_checkpoint.json",
    "edge_candidates_with_decoys.json",
}


def timestamp() -> str:
    return datetime.now().astimezone().isoformat()


@dataclass
class GroundTruthJob:
    job_id: str
    mode: str
    configuration: dict[str, Any]
    output_directory: str
    status: str = "queued"
    message: str = "Queued"
    progress: float = 0.0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=timestamp)
    started_at: str | None = None
    finished_at: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)


JOBS: dict[str, GroundTruthJob] = {}
JOBS_LOCK = threading.Lock()


def load_default_configuration() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _integer(config: dict[str, Any], key: str, minimum: int, maximum: int) -> int:
    value = int(config[key])
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _number(config: dict[str, Any], key: str, minimum: float, maximum: float) -> float:
    value = float(config[key])
    if not minimum <= value <= maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def normalize_configuration(payload: dict[str, Any]) -> dict[str, Any]:
    """Merge browser controls into the reviewed template without accepting paths or keys."""

    default = load_default_configuration()
    editable = {
        "api_base_url", "deployments", "seed", "pair_search_calls_per_round",
        "max_pair_search_rounds", "min_pair_search_rounds",
        "pair_search_saturation_rounds", "pair_search_min_new_pairs",
        "max_pair_candidates", "decoy_fraction", "max_co_mention_decoy_queries",
        "minimum_candidates_per_profile_polarity",
        "timeout_seconds", "generator_web_search_context_size",
        "generator_concurrency", "validator_concurrency",
    }
    for key in editable:
        if key in payload:
            default[key] = payload[key]
    default["api_base_url"] = str(default.get("api_base_url", "")).strip().rstrip("/")
    responses_url(default["api_base_url"])
    deployments = default.get("deployments")
    if not isinstance(deployments, dict):
        raise ValueError("deployments must contain generator and validator names")
    default["deployments"] = {
        "generator": str(deployments.get("generator", "")).strip(),
        "validator": str(deployments.get("validator", "")).strip(),
    }
    if not all(default["deployments"].values()):
        raise ValueError("Both Azure deployment names are required")
    default["seed"] = _integer(default, "seed", 0, 2_147_483_647)
    default["pair_search_calls_per_round"] = _integer(default, "pair_search_calls_per_round", 1, 100)
    default["max_pair_search_rounds"] = _integer(default, "max_pair_search_rounds", 1, 50)
    default["min_pair_search_rounds"] = _integer(default, "min_pair_search_rounds", 1, 50)
    if default["min_pair_search_rounds"] > default["max_pair_search_rounds"]:
        raise ValueError("min_pair_search_rounds cannot exceed max_pair_search_rounds")
    default["pair_search_saturation_rounds"] = _integer(default, "pair_search_saturation_rounds", 1, 10)
    default["pair_search_min_new_pairs"] = _integer(default, "pair_search_min_new_pairs", 0, 500)
    default["max_pair_candidates"] = _integer(default, "max_pair_candidates", 1, 10_000)
    default["minimum_candidates_per_profile_polarity"] = _integer(
        default, "minimum_candidates_per_profile_polarity", 0, 1_000
    )
    default["max_co_mention_decoy_queries"] = _integer(default, "max_co_mention_decoy_queries", 0, 500)
    default["timeout_seconds"] = _integer(default, "timeout_seconds", 30, 3600)
    default["generator_concurrency"] = _integer(default, "generator_concurrency", 1, 32)
    default["validator_concurrency"] = _integer(default, "validator_concurrency", 1, 16)
    default["decoy_fraction"] = _number(default, "decoy_fraction", 0.0, 0.4)
    # Fixed operational values never come from the browser.
    default["protocol_version"] = PROTOCOL_VERSION
    default["api_key_env"] = "AZURE_OPENAI_API_KEY"
    default["generator_web_search"] = True
    default["generator_web_search_required"] = True
    context_size = str(default.get("generator_web_search_context_size", "low")).strip().casefold()
    if context_size not in {"low", "medium", "high"}:
        raise ValueError("generator_web_search_context_size must be low, medium, or high")
    default["generator_web_search_context_size"] = context_size
    return default


def public_job(job: GroundTruthJob) -> dict[str, Any]:
    directory = Path(job.output_directory)
    files = sorted(path.name for path in directory.iterdir() if path.is_file() and path.name in DOWNLOAD_FILES) if directory.is_dir() else []
    return {
        "job_id": job.job_id,
        "mode": job.mode,
        "status": job.status,
        "message": job.message,
        "progress": job.progress,
        "result": job.result if job.status == "complete" else None,
        "error": job.error,
        "files": files,
        "resumable": job.mode == "production" and job.status in {"failed", "cancelled"},
        "cancel_requested": job.cancel_event.is_set(),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


def latest_job() -> dict[str, Any] | None:
    with JOBS_LOCK:
        active = next((job for job in JOBS.values() if job.status not in TERMINAL_STATUSES), None)
        if active:
            return public_job(active)
        if not JOBS:
            return None
        return public_job(max(JOBS.values(), key=lambda item: item.finished_at or item.created_at))


def create_job(payload: dict[str, Any], api_key: str | None) -> GroundTruthJob:
    mode = str(payload.get("mode", "dry_run")).strip()
    if mode not in {"dry_run", "production"}:
        raise ValueError("mode must be dry_run or production")
    configuration = normalize_configuration(payload.get("configuration", payload))
    if mode == "production" and not (api_key or os.environ.get("AZURE_OPENAI_API_KEY", "")).strip():
        raise ValueError("Enter the Azure OpenAI API key for this run")
    with JOBS_LOCK:
        active = next((item for item in JOBS.values() if item.status not in TERMINAL_STATUSES), None)
        if active:
            raise RuntimeError("Another ground-truth curation job is already active")
    job_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    directory = RUN_ROOT / job_id
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "configuration.json").write_text(
        json.dumps(configuration, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    job = GroundTruthJob(job_id, mode, configuration, str(directory.resolve()))
    with JOBS_LOCK:
        JOBS[job_id] = job
    thread = threading.Thread(target=execute_job, args=(job_id, api_key), daemon=True)
    thread.start()
    return job


def retry_job(job_id: str, api_key: str | None) -> GroundTruthJob:
    """Resume a failed production run in its existing checkpoint directory."""

    key = (api_key or os.environ.get("AZURE_OPENAI_API_KEY", "")).strip()
    if not key:
        raise ValueError("Enter the Azure OpenAI API key to retry this run")
    with JOBS_LOCK:
        previous = JOBS.get(job_id)
        if previous is None:
            raise ValueError("Run not found")
        active = next((item for item in JOBS.values() if item.status not in TERMINAL_STATUSES), None)
        if active:
            raise RuntimeError("Another ground-truth curation job is already active")
        if previous.mode != "production" or previous.status not in {"failed", "cancelled"}:
            raise ValueError("Only a stopped production run can be retried")
        directory = Path(previous.output_directory)
        config_path = directory / "configuration.json"
        if not config_path.is_file():
            raise ValueError("The saved run has no configuration checkpoint")
        saved_configuration = json.loads(config_path.read_text(encoding="utf-8"))
        if str(saved_configuration.get("protocol_version", "")) != PROTOCOL_VERSION:
            raise ValueError("The saved run belongs to an incompatible protocol version")
        configuration = normalize_configuration(saved_configuration)
        config_path.write_text(
            json.dumps(configuration, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        resumed = GroundTruthJob(
            job_id=job_id,
            mode="production",
            configuration=configuration,
            output_directory=str(directory.resolve()),
            status="queued",
            message="Queued to resume from saved checkpoints",
            progress=previous.progress,
            created_at=previous.created_at,
        )
        JOBS[job_id] = resumed
    thread = threading.Thread(target=execute_job, args=(job_id, key), daemon=True)
    thread.start()
    return resumed


def execute_job(job_id: str, api_key: str | None) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        job.status = "running"
        job.message = "Preparing ground-truth curation"
        job.started_at = timestamp()

    def update(message: str, fraction: float) -> None:
        if job.cancel_event.is_set():
            raise GroundTruthCancelled("Ground-truth curation cancelled by user")
        with JOBS_LOCK:
            current = JOBS[job_id]
            current.message = message
            current.progress = max(current.progress, min(1.0, max(0.0, float(fraction))))

    try:
        directory = Path(job.output_directory)
        if job.mode == "dry_run":
            update("Calculating call-volume preview", 0.5)
            result = dry_run(job.configuration, directory)
        else:
            result = GroundTruthPipeline(
                job.configuration,
                directory,
                api_key=api_key,
                progress=update,
                cancel_requested=job.cancel_event.is_set,
            ).run()
        with JOBS_LOCK:
            current = JOBS[job_id]
            current.status = "complete"
            current.message = "Dry run complete" if job.mode == "dry_run" else "Ground-truth curation complete"
            current.progress = 1.0
            current.result = result
            current.finished_at = timestamp()
    except GroundTruthCancelled:
        with JOBS_LOCK:
            current = JOBS[job_id]
            current.status = "cancelled"
            current.message = "Cancelled"
            current.finished_at = timestamp()
    except Exception as exc:  # noqa: BLE001 - asynchronous boundary
        traceback.print_exc()
        cancelled = job.cancel_event.is_set() or "cancelled by user" in str(exc).casefold()
        with JOBS_LOCK:
            current = JOBS[job_id]
            current.status = "cancelled" if cancelled else "failed"
            current.message = "Cancelled" if cancelled else "Ground-truth curation failed"
            current.error = None if cancelled else str(exc)
            current.finished_at = timestamp()
    finally:
        api_key = None


def cancel_job(job_id: str) -> dict[str, Any] | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return None
        if job.status not in TERMINAL_STATUSES:
            job.cancel_event.set()
            job.status = "cancelling"
            job.message = "Cancellation requested; waiting for the current API call to return"
        return public_job(job)


def job_payload(job_id: str) -> dict[str, Any] | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return public_job(job) if job else None


def job_file(job_id: str, name: str) -> Path | None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or name not in DOWNLOAD_FILES:
        return None
    path = Path(job.output_directory) / name
    return path if path.is_file() else None


def recover_latest_job(*, include_incomplete: bool = True) -> GroundTruthJob | None:
    if not RUN_ROOT.is_dir():
        return None
    for directory in sorted(RUN_ROOT.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not directory.is_dir() or not (directory / "configuration.json").is_file():
            continue
        manifest = directory / "run_manifest.json"
        preview = directory / "dry_run.json"
        try:
            configuration = json.loads((directory / "configuration.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(configuration.get("protocol_version", "")) != PROTOCOL_VERSION:
            continue
        result_path = manifest if manifest.is_file() else preview if preview.is_file() else None
        if result_path is not None:
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            job = GroundTruthJob(
                job_id=directory.name,
                mode="production" if manifest.is_file() else "dry_run",
                configuration=configuration,
                output_directory=str(directory.resolve()),
                status="complete",
                message="Recovered completed ground-truth run",
                progress=1.0,
                result=result,
                finished_at=timestamp(),
            )
        elif include_incomplete:
            checkpoint_progress = 0.01
            if (directory / "node_candidates.json").is_file():
                checkpoint_progress = 0.25
            if (directory / "edge_candidates.json").is_file():
                checkpoint_progress = 0.42
            if (directory / "edge_candidates_with_decoys.json").is_file():
                checkpoint_progress = 0.46
            job = GroundTruthJob(
                job_id=directory.name,
                mode="production",
                configuration=configuration,
                output_directory=str(directory.resolve()),
                status="failed",
                message="Recovered resumable ground-truth run",
                progress=checkpoint_progress,
                error="The previous process stopped before completion. Enter the API key and retry from the saved checkpoints.",
                finished_at=timestamp(),
            )
        else:
            continue
        with JOBS_LOCK:
            if not JOBS:
                JOBS[job.job_id] = job
                return job
        return None
    return None


def recover_latest_completed_job() -> GroundTruthJob | None:
    """Compatibility wrapper used by older callers and focused tests."""

    return recover_latest_job(include_incomplete=False)
