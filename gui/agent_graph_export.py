"""Write a stable, LLM-friendly evidence graph beside a session HTML."""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
PACKAGE_FILES = (
    "agent_graph.json",
    "agent_graph.jsonl",
    "agent_prompt.md",
    "agent_manifest.json",
)


def _key(item: dict[str, Any]) -> str:
    if item.get("kind") == "node":
        return f"node:{str(item.get('symbol', '')).casefold()}"
    left, right = sorted(
        (str(item.get("node_a", "")), str(item.get("node_b", ""))),
        key=str.casefold,
    )
    return f"edge:{left.casefold()}|{right.casefold()}"


def _stream_record(stream: dict[str, Any]) -> dict[str, Any]:
    return {
        "stream_id": stream.get("stream_id"),
        "label": stream.get("label"),
        "status": stream.get("status"),
        "enabled": stream.get("enabled"),
        "observed_record": stream.get("source_record_retained"),
        "negative_scope_eligible": stream.get("negative_evidence_eligible"),
        "raw_or_precalibration_bf": stream.get(
            "pre_distribution_normalization_bayes_factor",
            stream.get("source_factor"),
        ),
        "applied_bf": stream.get("applied_bayes_factor"),
        "weight": stream.get("weight"),
        "delta_log2_odds": stream.get("weighted_log2_odds_contribution"),
        "raw_value": stream.get("raw_value"),
        "raw_value_label": stream.get("raw_value_label"),
        "distribution_position": stream.get("distribution_position"),
        "empirical_likelihood": stream.get("empirical_likelihood"),
        "dependence_group": stream.get("dependence_group"),
        "note": stream.get("note"),
        "provenance": stream.get("provenance_trace"),
        "conditional_site_evidence": stream.get("conditional_site_evidence"),
    }


def _ledger_record(item: dict[str, Any]) -> dict[str, Any]:
    common = {
        "record_type": item.get("kind"),
        "prior_probability": item.get("prior_probability"),
        "posterior_probability": item.get("stored_posterior_probability"),
        "reconstructed_posterior_probability": item.get(
            "reconstructed_posterior_probability"
        ),
        "arithmetic_difference": item.get("reconciliation_absolute_difference"),
        "curated_reference": item.get("curated_edge_reference"),
        "evidence": [_stream_record(stream) for stream in item.get("streams", [])],
    }
    if item.get("kind") == "node":
        return {"id": str(item.get("symbol", "")), **common}
    return {
        "id": f"{item.get('node_a', '')}--{item.get('node_b', '')}",
        "node_a": item.get("node_a"),
        "node_b": item.get("node_b"),
        **common,
    }


def _read_paths(output: Path) -> list[dict[str, Any]]:
    path = output / "ranked_paths.tsv"
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _read_path_edges(output: Path) -> list[dict[str, Any]]:
    path = output / "ranked_path_edges.tsv"
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_agent_graph_package(
    session: dict[str, Any], output_directory: Path | str
) -> dict[str, Any]:
    """Write JSON, JSONL, prompt, and manifest files for downstream agents."""
    output = Path(output_directory).resolve()
    job = session.get("job") or {}
    preview = job.get("preview") or session.get("preview") or {}
    summary = job.get("summary") or session.get("summary") or {}
    configuration = session.get("configuration") or {}
    network = preview.get("path_network") or {}
    catalog = {
        _key(item): _ledger_record(item)
        for item in session.get("interpretation_catalog") or []
        if item.get("kind") in {"node", "edge"}
    }
    path_edge_rows = _read_path_edges(output)
    preview_nodes = {str(item.get("id", "")): item for item in network.get("nodes", [])}
    preview_edges: dict[str, dict[str, Any]] = {}
    for item in network.get("edges", []):
        left, right = sorted(
            (str(item.get("node_a", "")), str(item.get("node_b", ""))),
            key=str.casefold,
        )
        preview_edges[f"{left.casefold()}|{right.casefold()}"] = item

    node_ranks: dict[str, set[int]] = {}
    edge_accumulator: dict[str, dict[str, Any]] = {}
    for item in path_edge_rows:
        source = str(item.get("source_symbol", ""))
        target = str(item.get("target_symbol", ""))
        if not source or not target:
            continue
        try:
            rank = int(float(item.get("path_rank") or 0))
        except (TypeError, ValueError):
            rank = 0
        node_ranks.setdefault(source, set()).add(rank)
        node_ranks.setdefault(target, set()).add(rank)
        left, right = sorted((source, target), key=str.casefold)
        key = f"{left.casefold()}|{right.casefold()}"
        record = edge_accumulator.setdefault(
            key,
            {
                "id": f"{left}--{right}",
                "node_a": left,
                "node_b": right,
                "path_ranks": set(),
                "path_traversals": set(),
                "edge_probability": 0.0,
            },
        )
        record["path_ranks"].add(rank)
        record["path_traversals"].add(f"{source}->{target}")
        try:
            record["edge_probability"] = max(
                float(record["edge_probability"]),
                float(item.get("edge_probability") or 0.0),
            )
        except (TypeError, ValueError):
            pass

    if not node_ranks:
        for item in network.get("nodes", []):
            node_ranks[str(item.get("id", ""))] = {
                int(rank) for rank in item.get("path_ranks", [])
            }
    nodes = []
    for symbol, ranks in sorted(node_ranks.items(), key=lambda item: item[0].casefold()):
        record = dict(preview_nodes.get(symbol) or {"id": symbol, "label": symbol})
        record["path_ranks"] = sorted(rank for rank in ranks if rank > 0)
        record["path_count"] = len(record["path_ranks"])
        record["evidence_ledger"] = catalog.get(f"node:{symbol.casefold()}")
        nodes.append(record)

    if not edge_accumulator:
        for item in network.get("edges", []):
            left, right = sorted(
                (str(item.get("node_a", "")), str(item.get("node_b", ""))),
                key=str.casefold,
            )
            edge_accumulator[f"{left.casefold()}|{right.casefold()}"] = dict(item)
    edges = []
    for key, item in sorted(edge_accumulator.items()):
        preview = preview_edges.get(key) or {}
        record = {**item, **preview}
        record["path_ranks"] = sorted(
            int(rank) for rank in item.get("path_ranks", []) if int(rank) > 0
        )
        record["path_traversals"] = sorted(item.get("path_traversals", []))
        record["path_count"] = len(record["path_ranks"])
        record.setdefault(
            "directionality",
            "ranked_path_traversal_only; consult ontology_directionality_audit.tsv.gz",
        )
        record["evidence_ledger"] = catalog.get(f"edge:{key}")
        edges.append(record)
    run_id = str(job.get("job_id") or summary.get("run_id") or output.name)
    sources = list((configuration.get("path") or {}).get("starts") or [])
    targets = list((configuration.get("path") or {}).get("targets") or [])
    payload = {
        "schema": "gbi-agent-evidence-graph",
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "intended_question": (
            "Given these predicted signaling relationships, which paths from the "
            "specified source nodes to target nodes are mechanistically sound?"
        ),
        "source_nodes": sources,
        "target_nodes": targets,
        "semantics": {
            "applied_bf": "P(data|edge or node present) / P(data|absent) after the run's selected calibration mode",
            "delta_log2_odds": "weight × log2(applied_bf)",
            "posterior": "prior odds updated by all enabled evidence contributions",
            "directionality": "uniquely_directed means reverse traversal was removed; unresolved_bidirectional is not proof of bidirectionality",
            "missing_record": "absence is neutral unless negative_scope_eligible is true and an explicit penalty was applied",
            "kinase_site_concordance": "KinasePredictor support is gated by response at the same substrate phosphosite in Kirby dDAVP or PKA-Cα/PKA-Cβ KO data",
        },
        "coverage": session.get("interpretation_scope") or {},
        "nodes": nodes,
        "edges": edges,
        "ranked_paths": _read_paths(output),
        "warnings": preview.get("warnings") or summary.get("warnings") or [],
        "configuration": configuration,
    }
    json_path = output / "agent_graph.json"
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    jsonl_path = output / "agent_graph.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps({"record_type": "meta", **{k: payload[k] for k in (
            "schema", "schema_version", "generated_at", "run_id", "source_nodes",
            "target_nodes", "semantics", "coverage", "warnings"
        )}}, ensure_ascii=False, default=str) + "\n")
        for record in nodes:
            handle.write(json.dumps({"record_type": "node", **record}, ensure_ascii=False, default=str) + "\n")
        for record in edges:
            handle.write(json.dumps({"record_type": "edge", **record}, ensure_ascii=False, default=str) + "\n")
        for record in payload["ranked_paths"]:
            handle.write(json.dumps({"record_type": "path", **record}, ensure_ascii=False, default=str) + "\n")
    prompt_path = output / "agent_prompt.md"
    prompt_path.write_text(
        f"""# Mechanistic path review for GBI run `{run_id}`

Read `agent_graph.json` (or stream `agent_graph.jsonl` if the JSON is too large).

## Task

Given the predicted signaling relationships, identify which paths from
`{', '.join(sources) or '[source_nodes]'}` to
`{', '.join(targets) or '[target_nodes]'}` are mechanistically sound.

For every path you recommend:

1. preserve the directed order encoded by each edge;
2. explain the biochemical mechanism at every step;
3. cite the decisive per-stream Bayes factors, weights, and Δlog2-odds;
4. distinguish direct evidence from database association, colocalization,
   motif compatibility, structural prediction, and closure-derived evidence;
5. for kinase→substrate steps, require exact-site response concordance and
   report the responsive phosphosite and dataset(s);
6. flag redundant or potentially dependent evidence instead of treating it as
   independent corroboration;
7. identify the weakest or directionally ambiguous step and propose a concrete
   experiment that would resolve it.

Return a ranked table with: path rank, path, verdict, mechanistic rationale,
strongest evidence, weakest evidence, directionality concerns, and proposed
validation experiment. Do not infer that a missing source record is negative
unless the record says an absence penalty was applied.
""",
        encoding="utf-8",
    )
    manifest_path = output / "agent_manifest.json"
    files = [json_path, jsonl_path, prompt_path]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "path_count": len(payload["ranked_paths"]),
        "node_ledgers": sum(item.get("evidence_ledger") is not None for item in nodes),
        "edge_ledgers": sum(item.get("evidence_ledger") is not None for item in edges),
        "files": [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path)}
            for path in files
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    # A compressed copy is convenient for archival/agent APIs that accept gzip.
    with gzip.open(output / "agent_graph.json.gz", "wb", compresslevel=6) as handle:
        handle.write(json_path.read_bytes())
    return manifest

