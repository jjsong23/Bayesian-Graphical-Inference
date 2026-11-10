"""Import complete BGI agent graphs without discarding evidence ledgers."""

from __future__ import annotations

import gzip
import csv
from collections import Counter
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any, Iterator, TextIO

from .database import RunDatabase, now


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def canonical_edge(node_a: str, node_b: str) -> str:
    left, right = sorted((node_a.strip(), node_b.strip()), key=str.casefold)
    return f"{left}--{right}"


def path_id(session_id: str, query_id: str, symbols: str) -> str:
    digest = hashlib.sha256(f"{session_id}\0{query_id}\0{symbols}".encode("utf-8")).hexdigest()[:20]
    return f"PTH-{digest}"


def _agent_streams(input_path: Path) -> Iterator[tuple[str, TextIO]]:
    legacy_dir = input_path.parent if input_path.suffix.lower() == ".html" else input_path
    if legacy_dir.is_dir() and not (legacy_dir / "agent_graph.json.gz").exists() and (legacy_dir / "ranked_paths.tsv").is_file() and (legacy_dir / "all_returned_path_evidence_ledgers.json.gz").is_file():
        yield str(legacy_dir / "legacy_session"), io.StringIO(json.dumps(_legacy_graph(legacy_dir)))
        return
    if input_path.is_file() and input_path.suffix.casefold() == ".zip":
        archive = zipfile.ZipFile(input_path)
        try:
            names = sorted(name for name in archive.namelist() if name.endswith("agent_graph.json.gz"))
            if not names:
                raise ValueError(f"No agent_graph.json.gz files found in {input_path}")
            for name in names:
                with archive.open(name) as compressed:
                    with gzip.GzipFile(fileobj=compressed) as raw:
                        with io.TextIOWrapper(raw, encoding="utf-8") as text:
                            yield name, text
        finally:
            archive.close()
        return
    if input_path.is_dir():
        files = sorted(input_path.rglob("agent_graph.json.gz"))
    elif input_path.name.endswith("agent_graph.json.gz"):
        files = [input_path]
    else:
        raise ValueError("Input must be an agent-graph ZIP/directory, agent_graph.json.gz, or a legacy session directory/HTML with adjacent ranked_paths.tsv and all_returned_path_evidence_ledgers.json.gz")
    if not files:
        raise ValueError(f"No agent_graph.json.gz files found under {input_path}")
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as text:
            yield str(path), text


def _legacy_graph(directory: Path) -> dict[str, Any]:
    """Read all RETURNED paths, never infer coverage from the displayed graph."""
    with (directory / "ranked_paths.tsv").open(encoding="utf-8-sig", newline="") as handle:
        paths = list(csv.DictReader(handle, delimiter="\t"))
    with gzip.open(directory / "all_returned_path_evidence_ledgers.json.gz", "rt", encoding="utf-8") as handle:
        ledgers = json.load(handle)
    nodes = {str(x["symbol"]): x for x in ledgers if x.get("kind") == "node"}
    edges = {canonical_edge(x["node_a"], x["node_b"]): x for x in ledgers if x.get("kind") == "edge"}
    node_counts: Counter[str] = Counter()
    edge_counts: Counter[str] = Counter()
    edge_pairs: dict[str, tuple[str, str]] = {}
    for path in paths:
        symbols = [x.strip() for x in path["path_symbols"].split(" -> ")]
        node_counts.update(set(symbols))
        pairs = {canonical_edge(a, b): (a, b) for a, b in zip(symbols, symbols[1:])}
        edge_counts.update(pairs.keys())
        edge_pairs.update(pairs)
    missing = [f"node:{x}" for x in node_counts if x not in nodes] + [f"edge:{x}" for x in edge_counts if x not in edges]
    return {
        "run_id": directory.name, "source_nodes": sorted({p["start_symbol"] for p in paths}),
        "target_nodes": sorted({p["target_symbol"] for p in paths}), "ranked_paths": paths,
        "scope": "all returned paths; not the complete pre-search candidate graph",
        "missing_evidence_ledgers": missing,
        "nodes": [{"id": key, "label": key, "name": nodes.get(key, {}).get("name"),
                   "classes": nodes.get(key, {}).get("classes"), "path_count": count,
                   "posterior_probability": nodes.get(key, {}).get("stored_posterior_probability"),
                   "evidence_ledger_missing": key not in nodes, "evidence_ledger": nodes.get(key, {})}
                  for key, count in node_counts.items()],
        "edges": [{"id": key, "node_a": edge_pairs[key][0], "node_b": edge_pairs[key][1],
                   "path_count": count, "edge_probability": edges.get(key, {}).get("stored_posterior_probability"),
                   "directionality": None, "evidence_ledger_missing": key not in edges,
                   "evidence_ledger": edges.get(key, {})} for key, count in edge_counts.items()],
    }


def import_agent_graphs(input_path: str | Path, database: RunDatabase) -> dict[str, Any]:
    source = Path(input_path).expanduser().resolve()
    database.event("import", f"Importing complete agent graphs from {source}")
    sessions = 0
    source_nodes: set[str] = set()
    target_nodes: set[str] = set()
    queries: set[str] = set()
    missing_ledgers: list[dict[str, Any]] = []
    with database.connect() as db:
        for origin, handle in _agent_streams(source):
            graph = json.load(handle)
            sessions += 1
            session_id = str(graph.get("run_id") or Path(origin).parent.name)
            missing_ledgers.extend({"session_id": session_id, "item": item} for item in graph.get("missing_evidence_ledgers", []))
            graph_sources = {str(x) for x in graph.get("source_nodes", [])}
            graph_targets = {str(x) for x in graph.get("target_nodes", [])}
            source_nodes.update(graph_sources)
            target_nodes.update(graph_targets)
            for node in graph.get("nodes", []):
                node_id = str(node.get("id") or node.get("label") or "").strip()
                if not node_id:
                    continue
                db.execute("INSERT OR REPLACE INTO session_evidence VALUES (?,?,?,?)", (session_id, "node", node_id, json.dumps(node, ensure_ascii=False)))
                prior = db.execute("SELECT path_count,best_posterior,payload_json,is_source,is_target FROM nodes WHERE node_id=?", (node_id,)).fetchone()
                posterior = _number(node.get("posterior_probability"), 0.0)
                payload = node
                if prior is not None and _number(prior["best_posterior"]) > posterior:
                    payload = json.loads(prior["payload_json"])
                db.execute(
                    """INSERT OR REPLACE INTO nodes
                    (node_id,label,name,classes,is_source,is_target,path_count,best_posterior,payload_json)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        node_id, str(node.get("label") or node_id), str(node.get("name") or ""),
                        str(node.get("classes") or ""),
                        int(node_id in graph_sources or bool(prior and prior["is_source"])),
                        int(node_id in graph_targets or bool(prior and prior["is_target"])),
                        max(_integer(node.get("path_count")), _integer(prior["path_count"]) if prior else 0),
                        max(posterior, _number(prior["best_posterior"]) if prior else 0.0),
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
            for edge in graph.get("edges", []):
                node_a = str(edge.get("node_a") or "").strip()
                node_b = str(edge.get("node_b") or "").strip()
                if not node_a or not node_b:
                    continue
                edge_id = canonical_edge(node_a, node_b)
                db.execute("INSERT OR REPLACE INTO session_evidence VALUES (?,?,?,?)", (session_id, "edge", edge_id, json.dumps(edge, ensure_ascii=False)))
                prior = db.execute("SELECT path_count,best_probability,payload_json FROM edges WHERE edge_id=?", (edge_id,)).fetchone()
                probability = _number(edge.get("edge_probability"), 0.0)
                payload = edge
                if prior is not None and _number(prior["best_probability"]) > probability:
                    payload = json.loads(prior["payload_json"])
                db.execute(
                    """INSERT OR REPLACE INTO edges
                    (edge_id,node_a,node_b,path_count,best_probability,payload_json) VALUES (?,?,?,?,?,?)""",
                    (
                        edge_id, node_a, node_b,
                        max(_integer(edge.get("path_count")), _integer(prior["path_count"]) if prior else 0),
                        max(probability, _number(prior["best_probability"]) if prior else 0.0),
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
            for path in graph.get("ranked_paths", []):
                query_id = str(path.get("query_id") or f"{path.get('start_symbol')}->{path.get('target_symbol')}")
                symbols = str(path.get("path_symbols") or "")
                if not symbols:
                    continue
                queries.add(query_id)
                identifier = path_id(session_id, query_id, symbols)
                db.execute(
                    """INSERT OR IGNORE INTO paths
                    (path_id,session_id,query_id,start_symbol,target_symbol,rank,hop_count,
                     primary_score,path_symbols,payload_json) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        identifier, session_id, query_id,
                        str(path.get("start_symbol") or symbols.split(" -> ")[0]),
                        str(path.get("target_symbol") or symbols.split(" -> ")[-1]),
                        _integer(path.get("rank"), 999999), _integer(path.get("hop_count")),
                        _number(path.get("primary_path_score")), symbols,
                        json.dumps(path, ensure_ascii=False, separators=(",", ":")),
                    ),
                )
            graph.clear()
    counts = database.counts()
    inventory = {
        "schema_version": 1,
        "imported_at": now(),
        "input_path": str(source),
        "session_count": sessions,
        "query_count": len(queries),
        "source_nodes": sorted(source_nodes, key=str.casefold),
        "target_nodes": sorted(target_nodes, key=str.casefold),
        **counts,
        "coverage_scope": "all imported returned paths; not all edges in the pre-search candidate graph",
        "missing_evidence_ledgers": missing_ledgers,
        "path_first_initial_screen_upper_bound": counts["paths"],
        "legacy_evidence_first_estimated_api_calls": {
            "node_research_batches_of_6": (counts["nodes"] + 5) // 6,
            "node_claim_verification_upper_bound": counts["nodes"] * 8,
            "edge_research_batches_of_3_upper_bound": (counts["edges"] + 2) // 3,
            "edge_claim_verification_upper_bound": counts["edges"] * 8,
            "path_deep_reviews_configurable": 200,
            "network_synthesis": 1,
        },
        "note": "Path-first coverage reports direct reviews separately from verified-rule exclusions. Research is adaptive and cached. Counts are not paid-call measurements.",
    }
    database.set_meta("inventory", inventory)
    database.set_meta("input_path", str(source))
    database.event("import", f"Imported {sessions} sessions, {counts['nodes']} nodes, {counts['edges']} edges, and {counts['paths']} paths", details=inventory)
    return inventory

