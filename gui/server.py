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
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlparse

import pandas as pd

from workflow_engine import (
    FULL_GRAPH_PRESENTATION_METRICS,
    GUI_RANKING_LIMIT,
    PATH_UNION_PRESENTATION_METRICS,
    PROJECT_ROOT,
    WorkflowCancelled,
    default_configuration,
    load_registry,
    run_workflow,
    top_node_statistics,
)
from incremental_edge_cache import current_cache_counts
from evidence_inspector import (
    inspect_edge_evidence,
    inspect_edge_evidence_batch,
    inspect_node_evidence,
)
from literature_interpreter import (
    DEFAULT_NETWORK_CHUNK_SIZE,
    interpret_hypothesis,
    interpret_pathway_network,
    interpreter_status,
)
from evidence_provenance import trace_database_evidence
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
    pending_edges: list[tuple[str, str]] = []
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
        if kind == "edge":
            pending_edges.append((symbols[0], symbols[1]))
            automatic_count += 1
            continue
        try:
            result = inspect_node_evidence(
                output_directory,
                symbols[0],
                project_root=PROJECT_ROOT,
            )
            result["export_catalog_source"] = "visible_top_path_network"
            catalog[key] = result
            automatic_count += 1
        except Exception as exc:  # noqa: BLE001 - one ledger must not abort the archive
            errors.append(f"{kind} {' — '.join(symbols)}: {exc}")

    if pending_edges:
        try:
            edge_results = inspect_edge_evidence_batch(
                output_directory,
                pending_edges,
                project_root=PROJECT_ROOT,
            )
        except Exception as batch_exc:  # noqa: BLE001 - retain robust legacy fallback
            errors.append(
                "Batched edge interpretation failed; retried each visible edge "
                f"individually. Batch error: {batch_exc}"
            )
            edge_results = []
            for left, right in pending_edges:
                try:
                    edge_results.append(
                        inspect_edge_evidence(
                            output_directory,
                            left,
                            right,
                            project_root=PROJECT_ROOT,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"edge {left} — {right}: {exc}")
        for result in edge_results:
            result["export_catalog_source"] = "visible_top_path_network"
            catalog[_evidence_payload_key(result)] = result

    automatic_count = sum(
        item.get("export_catalog_source") == "visible_top_path_network"
        for item in catalog.values()
    )

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


def _complete_literature_history(
    output_directory: Path,
    browser_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge browser state with every valid interpretation cached for the run."""

    catalog: dict[str, dict[str, Any]] = {}
    for item in browser_history:
        key = str(item.get("cache_key") or "").strip()
        if key and isinstance(item.get("interpretation"), dict):
            catalog[key] = item
    cache_directory = output_directory / "literature_interpretations"
    if cache_directory.is_dir():
        for path in sorted(cache_directory.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            key = str(item.get("cache_key") or path.stem).strip()
            catalog[key] = item
    return sorted(
        catalog.values(),
        key=lambda item: str(item.get("generated_at", "")),
    )[-1000:]


def _complete_network_literature_history(
    output_directory: Path,
    browser_references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Load completed network audits from disk; browser payloads carry only keys."""

    wanted = {
        str(item.get("cache_key", "")).strip()
        for item in browser_references
        if str(item.get("cache_key", "")).strip()
    }
    catalog: dict[str, dict[str, Any]] = {}
    directory = output_directory / "network_literature_interpretations"
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            if wanted and path.stem not in wanted:
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(record, dict):
                catalog[str(record.get("cache_key") or path.stem)] = record
    return sorted(catalog.values(), key=lambda item: str(item.get("generated_at", "")))[-10:]


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


@dataclass
class NetworkResearchJob:
    research_id: str
    job_id: str
    status: str = "queued"
    message: str = "Queued"
    progress: float = 0.0
    rank_limit: int = 0
    node_count: int = 0
    edge_count: int = 0
    batch_count_estimate: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=utc_timestamp)
    started_at: str | None = None
    finished_at: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)


NETWORK_RESEARCH_JOBS: dict[str, NetworkResearchJob] = {}
NETWORK_RESEARCH_LOCK = threading.Lock()
TERMINAL_RESEARCH_STATUSES = {"complete", "failed", "cancelled"}


def public_network_research(job: NetworkResearchJob) -> dict[str, Any]:
    return {
        "research_id": job.research_id,
        "job_id": job.job_id,
        "status": job.status,
        "message": job.message,
        "progress": job.progress,
        "rank_limit": job.rank_limit,
        "node_count": job.node_count,
        "edge_count": job.edge_count,
        "hypothesis_count": job.node_count + job.edge_count,
        "batch_count_estimate": job.batch_count_estimate,
        "result": job.result if job.status == "complete" else None,
        "error": job.error,
        "cancel_requested": job.cancel_event.is_set(),
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


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


def _rebuild_completed_preview(
    directory: Path,
    summary: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild the browser payload for runs created before it was persisted.

    ``analysis_summary.json`` is the scientific audit summary; it intentionally
    does not contain the tabular rankings and merged-path payload consumed by
    the GUI.  Treating it as a GUI preview makes result rendering fail after a
    server restart.  New runs persist ``gui_preview.json`` directly.  This
    fallback reconstructs the same display fields from the audited artifacts.
    """

    preview_path = directory / "gui_preview.json"
    if preview_path.is_file():
        try:
            preview = json.loads(preview_path.read_text(encoding="utf-8"))
            if isinstance(preview, dict) and isinstance(preview.get("metrics"), dict):
                return preview
        except (OSError, json.JSONDecodeError):
            pass

    node_summary = summary.get("node_selection") or {}
    edge_summary = summary.get("edge_characterization") or {}
    path_summary = summary.get("path_finding") or {}

    def read_table(name: str) -> pd.DataFrame:
        path = directory / name
        if not path.is_file():
            return pd.DataFrame()
        try:
            return pd.read_csv(path, sep="\t", low_memory=False)
        except (OSError, ValueError, pd.errors.ParserError):
            return pd.DataFrame()

    def statistics_preview(
        summary_key: str,
        table_name: str,
        *,
        presentation_metrics: tuple[str, ...],
        robustness_name: str | None = None,
    ) -> dict[str, Any] | None:
        statistics_summary = summary.get(summary_key)
        if not isinstance(statistics_summary, dict):
            return None
        node_statistics = read_table(table_name)
        rankings = (
            top_node_statistics(
                node_statistics,
                limit=GUI_RANKING_LIMIT,
                metrics=presentation_metrics,
            )
            if not node_statistics.empty
            else {
                "available_metrics": [],
                "top_nodes_by_metric": {},
                "display_limit": GUI_RANKING_LIMIT,
            }
        )
        robustness: list[dict[str, Any]] = []
        if robustness_name:
            robustness_table = read_table(robustness_name)
            if not robustness_table.empty:
                robustness = json.loads(
                    robustness_table.to_json(orient="records")
                )
        return {
            "summary": statistics_summary,
            **rankings,
            "robustness": robustness,
        }

    top_paths_table = read_table("ranked_paths.tsv")
    top_paths = (
        json.loads(top_paths_table.to_json(orient="records"))
        if not top_paths_table.empty
        else []
    )
    path_network: dict[str, Any] = {}
    path_network_path = directory / "top_path_network.json"
    if path_network_path.is_file():
        try:
            loaded_network = json.loads(path_network_path.read_text(encoding="utf-8"))
            if isinstance(loaded_network, dict):
                path_network = loaded_network
        except (OSError, json.JSONDecodeError):
            pass

    return {
        "metrics": {
            "selected_nodes": int(node_summary.get("selected_node_count") or 0),
            "supported_edges": int(
                edge_summary.get("reported_supported_edge_count")
                or edge_summary.get("pairs_above_output_cutoff")
                or 0
            ),
            "ranked_paths": int(path_summary.get("paths_found") or len(top_paths)),
        },
        "top_nodes": [],
        "top_paths": top_paths,
        "path_network": path_network,
        "full_graph_statistics": statistics_preview(
            "full_graph_statistics",
            "full_graph_node_statistics.tsv.gz",
            presentation_metrics=FULL_GRAPH_PRESENTATION_METRICS,
            robustness_name="full_graph_robustness.tsv",
        ),
        "found_path_union_statistics": statistics_preview(
            "found_path_union_statistics",
            "found_path_union_node_statistics.tsv.gz",
            presentation_metrics=PATH_UNION_PRESENTATION_METRICS,
        ),
        "probability_distributions": {
            "nodes": node_summary.get("probability_distribution"),
            "edges": edge_summary.get("probability_distribution"),
        },
        "directionality": summary.get("ontology_directionality"),
        "temporal_validation": summary.get("temporal_validation"),
        "calibration": summary.get("calibration"),
        "warnings": summary.get("warnings") or [],
        "files": sorted(
            path.name for path in directory.iterdir() if path.is_file()
        ),
    }


def recover_latest_completed_job() -> Job | None:
    """Reattach the newest complete on-disk run after a local-server restart."""

    runs_directory = PROJECT_ROOT / "results" / "gui_runs"
    if not runs_directory.is_dir():
        return None
    candidates = sorted(
        (path for path in runs_directory.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for directory in candidates:
        configuration_path = directory / "configuration.json"
        summary_path = directory / "analysis_summary.json"
        if not configuration_path.is_file() or not summary_path.is_file():
            continue
        try:
            configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(configuration, dict) or not isinstance(summary, dict):
            continue
        job_id = str(summary.get("run_id") or directory.name).strip()
        if not job_id:
            continue
        timestamp = str(summary.get("generated_at") or utc_timestamp())
        preview = _rebuild_completed_preview(directory, summary)
        recovered = Job(
            job_id=job_id,
            status="complete",
            message="Recovered completed run from disk",
            progress=1.0,
            configuration=configuration,
            preview=preview,
            summary=summary,
            output_directory=str(directory.resolve()),
            created_at=timestamp,
            started_at=timestamp,
            finished_at=timestamp,
        )
        with JOBS_LOCK:
            if not JOBS:
                JOBS[job_id] = recovered
                return recovered
        return None
    return None


def current_or_latest_job_payload() -> dict[str, Any] | None:
    """Prefer active work, otherwise expose the newest recovered completion."""

    with JOBS_LOCK:
        active = next(
            (job for job in JOBS.values() if job.status not in TERMINAL_JOB_STATUSES),
            None,
        )
        if active is not None:
            return public_job(active)
        complete = [job for job in JOBS.values() if job.status == "complete"]
        if not complete:
            return None
        return public_job(max(complete, key=lambda job: job.finished_at or job.created_at))


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


def _visible_network_ledgers(
    job: Job,
    rank_limit: int,
    *,
    progress: Callable[[str, float], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> tuple[list[dict[str, Any]], int, int]:
    """Freeze every unique node and edge in the selected top-path union."""

    if job.output_directory is None or not job.preview:
        raise ValueError("Network literature research requires a completed path run")
    network = job.preview.get("path_network") or {}
    available = int(network.get("visualized_path_count") or 0)
    if available < 1:
        raise ValueError("This run has no displayed top-path network to research")
    limit = min(max(1, int(rank_limit)), available)

    def visible(item: dict[str, Any]) -> bool:
        return any(int(rank) <= limit for rank in item.get("path_ranks", []))

    nodes = [item for item in network.get("nodes", []) if visible(item)]
    edges = [item for item in network.get("edges", []) if visible(item)]
    if len(nodes) + len(edges) > 1000:
        raise ValueError("The visible network exceeds the 1,000-hypothesis research limit")
    output_directory = Path(job.output_directory)
    ledgers: list[dict[str, Any]] = []
    for index, node in enumerate(nodes, start=1):
        if cancel_requested and cancel_requested():
            raise RuntimeError("Network literature research was cancelled")
        if progress and (index == 1 or index % 20 == 0 or index == len(nodes)):
            progress(f"Freezing node evidence {index} of {len(nodes)}", 0.02 + 0.07 * index / max(len(nodes), 1))
        try:
            ledger = inspect_node_evidence(
                output_directory,
                str(node.get("id", "")),
                project_root=PROJECT_ROOT,
            )
        except (ValueError, FileNotFoundError, KeyError):
            ledger = {
                "kind": "node",
                "symbol": str(node.get("id", "")),
                "node_name": node.get("name"),
                "node_classes": node.get("classes"),
                "prior_probability": None,
                "stored_posterior_probability": node.get("posterior_probability"),
                "selected_in_graph": True,
                "equation": "No Bayesian node-selection ledger exists for this curated or external endpoint.",
                "streams": [],
            }
        ledger["visible_path_ranks"] = [
            int(rank) for rank in node.get("path_ranks", []) if int(rank) <= limit
        ]
        ledgers.append(ledger)

    pair_queries = [
        (str(edge.get("node_a", "")), str(edge.get("node_b", "")))
        for edge in edges
    ]
    if progress:
        progress(f"Freezing {len(edges)} edge evidence ledgers", 0.11)
    edge_ledgers = inspect_edge_evidence_batch(
        output_directory,
        pair_queries,
        project_root=PROJECT_ROOT,
    )
    edge_metadata = {
        tuple(sorted((str(edge.get("node_a", "")), str(edge.get("node_b", ""))), key=str.casefold)): edge
        for edge in edges
    }
    for index, ledger in enumerate(edge_ledgers, start=1):
        if cancel_requested and cancel_requested():
            raise RuntimeError("Network literature research was cancelled")
        pair = tuple(
            sorted((str(ledger.get("node_a", "")), str(ledger.get("node_b", ""))), key=str.casefold)
        )
        metadata = edge_metadata.get(pair, {})
        ledger["visible_path_ranks"] = [
            int(rank) for rank in metadata.get("path_ranks", []) if int(rank) <= limit
        ]
        ledger["path_directionality"] = metadata.get("directionality")
        ledger["path_source"] = metadata.get("source")
        ledger["path_target"] = metadata.get("target")
        for stream in ledger.get("streams", []):
            if not stream.get("enabled"):
                continue
            if stream.get("status") == "neutral" and not stream.get("source_record_retained"):
                continue
            try:
                stream["provenance_trace"] = trace_database_evidence(
                    PROJECT_ROOT,
                    output_directory,
                    str(stream.get("stream_id", "")),
                    pair,
                    applied_bayes_factor=stream.get("applied_bayes_factor"),
                    weight=float(stream.get("weight") or 0),
                    multiplier=stream.get("tq_multiplier"),
                    include_source_records=False,
                )
            except Exception as exc:  # provenance commentary must not alter the result
                stream["provenance_trace"] = {
                    "database": stream.get("label") or stream.get("stream_id"),
                    "trace_status": "traceback_error",
                    "summary": f"The stored Bayesian contribution was preserved, but bulk provenance loading failed: {exc}",
                    "records": [],
                    "factors": [],
                    "links": [],
                }
        ledgers.append(ledger)
        if progress and (index == 1 or index % 10 == 0 or index == len(edge_ledgers)):
            progress(f"Attaching database tracebacks {index} of {len(edge_ledgers)}", 0.12 + 0.08 * index / max(len(edge_ledgers), 1))
    return ledgers, len(nodes), len(edges)


def execute_network_research(
    research_id: str,
    transient_api_key: str | None,
    parameters: dict[str, Any],
) -> None:
    """Run a whole-network web audit while keeping the credential in memory only."""

    with NETWORK_RESEARCH_LOCK:
        research = NETWORK_RESEARCH_JOBS[research_id]
    with JOBS_LOCK:
        analysis_job = JOBS.get(research.job_id)
    if analysis_job is None:
        return

    def update(message: str, fraction: float) -> None:
        if research.cancel_event.is_set():
            raise RuntimeError("Network literature research was cancelled")
        with NETWORK_RESEARCH_LOCK:
            current = NETWORK_RESEARCH_JOBS[research_id]
            current.message = message
            current.progress = max(0.0, min(1.0, float(fraction)))

    try:
        with NETWORK_RESEARCH_LOCK:
            current = NETWORK_RESEARCH_JOBS[research_id]
            current.status = "running"
            current.message = "Freezing the displayed top-path network"
            current.started_at = utc_timestamp()
        ledgers, node_count, edge_count = _visible_network_ledgers(
            analysis_job,
            research.rank_limit,
            progress=update,
            cancel_requested=research.cancel_event.is_set,
        )
        with NETWORK_RESEARCH_LOCK:
            current = NETWORK_RESEARCH_JOBS[research_id]
            current.node_count = node_count
            current.edge_count = edge_count
            current.batch_count_estimate = (
                len(ledgers) + DEFAULT_NETWORK_CHUNK_SIZE - 1
            ) // DEFAULT_NETWORK_CHUNK_SIZE

        def interpretation_progress(message: str, fraction: float) -> None:
            update(message, 0.20 + 0.80 * float(fraction))

        result = interpret_pathway_network(
            ledgers,
            Path(analysis_job.output_directory or ""),
            cell_type=parameters.get(
                "cell_type",
                "collecting duct principal cells across CCD, OMCD, and IMCD",
            ),
            signaling_purpose=parameters.get(
                "signaling_purpose",
                "vasopressin-regulated AQP2 signaling and water transport",
            ),
            model=parameters.get("model") or interpreter_status()["default_model"],
            reasoning_effort=(
                parameters.get("reasoning_effort")
                or interpreter_status()["default_reasoning_effort"]
            ),
            force_refresh=bool(parameters.get("force_refresh", False)),
            api_key=transient_api_key,
            api_base_url=parameters.get("api_base_url"),
            progress=interpretation_progress,
            cancel_requested=research.cancel_event.is_set,
        )
        with NETWORK_RESEARCH_LOCK:
            current = NETWORK_RESEARCH_JOBS[research_id]
            current.status = "complete"
            current.message = "Complete"
            current.progress = 1.0
            current.result = result
            current.finished_at = utc_timestamp()
    except Exception as exc:  # noqa: BLE001 - asynchronous boundary
        traceback.print_exc()
        cancelled = research.cancel_event.is_set() or "was cancelled" in str(exc).casefold()
        with NETWORK_RESEARCH_LOCK:
            current = NETWORK_RESEARCH_JOBS[research_id]
            current.status = "cancelled" if cancelled else "failed"
            current.message = "Cancelled" if cancelled else "Literature audit failed"
            current.error = None if cancelled else str(exc)
            current.finished_at = utc_timestamp()


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
            active_job = current_or_latest_job_payload()
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
                    "literature_interpreter": interpreter_status(),
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
            if len(parts) == 5 and parts[3] == "network-literature-analysis":
                research_id = parts[4]
                with NETWORK_RESEARCH_LOCK:
                    research = NETWORK_RESEARCH_JOBS.get(research_id)
                    payload = (
                        public_network_research(research)
                        if research and research.job_id == parts[2]
                        else None
                    )
                if payload is None:
                    self.send_json({"error": "network literature job not found"}, HTTPStatus.NOT_FOUND)
                else:
                    self.send_json(payload)
                return
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
        if (
            len(parts) == 4
            and parts[:2] == ["api", "jobs"]
            and parts[3] == "network-literature-analysis"
        ):
            try:
                payload = self.read_json()
                transient_api_key = self.headers.get("X-OpenAI-API-Key", "").strip() or None
                self.create_network_literature_analysis(
                    parts[2], payload, transient_api_key
                )
            except (ValueError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if (
            len(parts) == 6
            and parts[:2] == ["api", "jobs"]
            and parts[3] == "network-literature-analysis"
            and parts[5] == "cancel"
        ):
            research_id = parts[4]
            with NETWORK_RESEARCH_LOCK:
                research = NETWORK_RESEARCH_JOBS.get(research_id)
                if research is None or research.job_id != parts[2]:
                    response = None
                else:
                    if research.status not in TERMINAL_RESEARCH_STATUSES:
                        research.cancel_event.set()
                        research.status = "cancelling"
                        research.message = "Cancellation requested"
                    response = public_network_research(research)
            if response is None:
                self.send_json({"error": "network literature job not found"}, HTTPStatus.NOT_FOUND)
            else:
                self.send_json(response, HTTPStatus.ACCEPTED)
            return
        if (
            len(parts) == 4
            and parts[:2] == ["api", "jobs"]
            and parts[3] == "literature-interpretation"
        ):
            try:
                payload = self.read_json()
                self.create_literature_interpretation(parts[2], payload)
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

    def create_literature_interpretation(
        self,
        job_id: str,
        payload: dict[str, Any],
    ) -> None:
        """Research one frozen hypothesis without changing workflow results."""

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
                {"error": "Literature interpretation requires a completed run"},
                HTTPStatus.CONFLICT,
            )
            return

        kind = str(payload.get("kind", "")).strip().casefold()
        try:
            if kind == "node":
                ledger = inspect_node_evidence(
                    output_directory,
                    str(payload.get("symbol", "")).strip(),
                    project_root=PROJECT_ROOT,
                )
            elif kind == "edge":
                ledger = inspect_edge_evidence(
                    output_directory,
                    str(payload.get("node_a", "")).strip(),
                    str(payload.get("node_b", "")).strip(),
                    project_root=PROJECT_ROOT,
                )
            else:
                raise ValueError("kind must be 'node' or 'edge'")
            result = interpret_hypothesis(
                ledger,
                output_directory,
                cell_type=payload.get(
                    "cell_type",
                    "collecting duct principal cells across CCD, OMCD, and IMCD",
                ),
                signaling_purpose=payload.get(
                    "signaling_purpose",
                    "vasopressin-regulated AQP2 signaling and water transport",
                ),
                model=payload.get("model") or interpreter_status()["default_model"],
                reasoning_effort=(
                    payload.get("reasoning_effort")
                    or interpreter_status()["default_reasoning_effort"]
                ),
                force_refresh=bool(payload.get("force_refresh", False)),
                api_base_url=payload.get("api_base_url"),
            )
            result["evidence_ledger"] = ledger
            self.send_json(result, HTTPStatus.CREATED)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except RuntimeError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)

    def create_network_literature_analysis(
        self,
        job_id: str,
        payload: dict[str, Any],
        transient_api_key: str | None,
    ) -> None:
        """Start an asynchronous audit of every node/edge in the visible path union."""

        with JOBS_LOCK:
            job = JOBS.get(job_id)
            status = job.status if job else None
            preview = job.preview if job else None
        if job is None:
            self.send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
            return
        if status != "complete" or not job.output_directory:
            self.send_json(
                {"error": "Network literature research requires a completed run"},
                HTTPStatus.CONFLICT,
            )
            return
        if not transient_api_key and not interpreter_status()["available"]:
            self.send_json(
                {"error": "Enter an OpenAI API key. It is used only for this research job and is never saved."},
                HTTPStatus.BAD_REQUEST,
            )
            return
        if transient_api_key and (
            len(transient_api_key) > 1024
            or any(character in transient_api_key for character in "\r\n\0")
        ):
            self.send_json({"error": "The API key has an invalid format"}, HTTPStatus.BAD_REQUEST)
            return

        network = (preview or {}).get("path_network") or {}
        available = int(network.get("visualized_path_count") or 0)
        if available < 1:
            self.send_json(
                {"error": "This run has no displayed top-path network to research"},
                HTTPStatus.BAD_REQUEST,
            )
            return
        rank_limit = min(max(1, int(payload.get("rank_limit") or available)), available)
        node_count = sum(
            any(int(rank) <= rank_limit for rank in node.get("path_ranks", []))
            for node in network.get("nodes", [])
        )
        edge_count = sum(
            any(int(rank) <= rank_limit for rank in edge.get("path_ranks", []))
            for edge in network.get("edges", [])
        )
        hypothesis_count = node_count + edge_count
        if hypothesis_count < 1:
            self.send_json({"error": "The selected path scope is empty"}, HTTPStatus.BAD_REQUEST)
            return
        if hypothesis_count > 1000:
            self.send_json(
                {"error": "The visible network exceeds the 1,000-hypothesis research limit"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        with NETWORK_RESEARCH_LOCK:
            active = next(
                (
                    current
                    for current in NETWORK_RESEARCH_JOBS.values()
                    if current.job_id == job_id
                    and current.status not in TERMINAL_RESEARCH_STATUSES
                ),
                None,
            )
            if active is not None:
                conflict = public_network_research(active)
            else:
                research_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_lit_" + uuid.uuid4().hex[:8]
                research = NetworkResearchJob(
                    research_id=research_id,
                    job_id=job_id,
                    rank_limit=rank_limit,
                    node_count=node_count,
                    edge_count=edge_count,
                    batch_count_estimate=(
                        hypothesis_count + DEFAULT_NETWORK_CHUNK_SIZE - 1
                    ) // DEFAULT_NETWORK_CHUNK_SIZE,
                )
                NETWORK_RESEARCH_JOBS[research_id] = research
                conflict = None
        if conflict is not None:
            self.send_json(
                {"error": "A literature audit is already active for this run", "active_research": conflict},
                HTTPStatus.CONFLICT,
            )
            return
        thread = threading.Thread(
            target=execute_network_research,
            args=(research_id, transient_api_key, dict(payload)),
            daemon=True,
        )
        self.send_json(public_network_research(research), HTTPStatus.ACCEPTED)
        thread.start()

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
        literature_interpretations = payload.get("literature_interpretations", [])
        network_literature_analyses = payload.get("network_literature_analyses", [])
        client_state = payload.get("client_state", {})
        if not isinstance(inspections, list) or not all(
            isinstance(item, dict) for item in inspections
        ):
            raise ValueError("inspection_history must be a list of objects")
        if len(inspections) > 100:
            raise ValueError("inspection_history may contain at most 100 hypotheses")
        if not isinstance(literature_interpretations, list) or not all(
            isinstance(item, dict) for item in literature_interpretations
        ):
            raise ValueError("literature_interpretations must be a list of objects")
        if len(literature_interpretations) > 1000:
            raise ValueError(
                "literature_interpretations may contain at most 1,000 hypotheses"
            )
        if not isinstance(network_literature_analyses, list) or not all(
            isinstance(item, dict) for item in network_literature_analyses
        ):
            raise ValueError("network_literature_analyses must be a list of objects")
        if len(network_literature_analyses) > 10:
            raise ValueError("network_literature_analyses may contain at most 10 audits")
        if not isinstance(client_state, dict):
            raise ValueError("client_state must be an object")

        literature_interpretations = _complete_literature_history(
            output_directory,
            literature_interpretations,
        )
        network_literature_analyses = _complete_network_literature_history(
            output_directory,
            network_literature_analyses,
        )

        interpretation_catalog, interpretation_scope, interpretation_errors = (
            _materialize_export_interpretations(
                job_payload,
                output_directory,
                inspections,
                client_state,
            )
        )

        session = {
            "schema_version": 4,
            "application": "Graphical Bayesian Inference Version 1",
            "job": job_payload,
            "configuration": configuration,
            "registry": load_registry(PROJECT_ROOT),
            "client_state": client_state,
            "inspection_history": inspections,
            "literature_interpretations": literature_interpretations,
            "network_literature_analyses": network_literature_analyses,
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
    recover_latest_completed_job()
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
