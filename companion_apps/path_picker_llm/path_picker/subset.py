"""Export one complete query from an agent graph, entirely offline.

This is a lossless evidence selection, not a reranking or a new inference run.
The output is directly accepted by :func:`ingest.import_agent_graphs`.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any
import zipfile

from .ingest import _agent_streams, canonical_edge


def select_query(graph: dict[str, Any], query_id: str) -> dict[str, Any]:
    """Keep every returned path of one query and their complete evidence union.

    Original scientific payloads are copied verbatim. Only graph-membership
    metadata (counts, ranks, positions, and observed traversals) is recalculated.
    The source metadata remains available under ``selection_provenance``.
    """
    paths = [path for path in graph.get("ranked_paths", [])
             if str(path.get("query_id") or
                    f"{path.get('start_symbol')}->{path.get('target_symbol')}") == query_id]
    if not paths:
        raise ValueError(f"Unknown or empty query: {query_id}")

    node_ranks: dict[str, list[int]] = defaultdict(list)
    edge_ranks: dict[str, list[int]] = defaultdict(list)
    positions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    traversals: dict[str, set[str]] = defaultdict(set)
    sequences: set[tuple[str, ...]] = set()
    sources: set[str] = set()
    targets: set[str] = set()
    for index, path in enumerate(paths, 1):
        symbols = tuple(symbol.strip() for symbol in str(path.get("path_symbols", "")).split("->"))
        if len(symbols) < 2 or any(not symbol for symbol in symbols):
            raise ValueError(f"Invalid path sequence in query {query_id}")
        if symbols in sequences:
            raise ValueError(f"Duplicate path sequence in query {query_id}")
        sequences.add(symbols)
        start = str(path.get("start_symbol") or symbols[0])
        target = str(path.get("target_symbol") or symbols[-1])
        if (start, target) != (symbols[0], symbols[-1]):
            raise ValueError(f"Path endpoints disagree with sequence in query {query_id}")
        sources.add(start)
        targets.add(target)
        rank = int(path.get("rank") or index)
        for symbol in set(symbols):
            node_ranks[symbol].append(rank)
        for position, symbol in enumerate(symbols):
            positions[symbol].append({"rank": rank, "position": position / (len(symbols) - 1)})
        pairs = {(a, b) for a, b in zip(symbols, symbols[1:])}
        for key in {canonical_edge(a, b) for a, b in pairs}:
            edge_ranks[key].append(rank)
        for a, b in pairs:
            traversals[canonical_edge(a, b)].add(f"{a}->{b}")
    if len(sources) != 1 or len(targets) != 1:
        raise ValueError(f"Query {query_id} does not identify one source-target pair")

    original_nodes: dict[str, dict[str, Any]] = {}
    for node in graph.get("nodes", []):
        key = str(node.get("id") or node.get("label") or "").strip()
        if key in node_ranks:
            if key in original_nodes:
                raise ValueError(f"Duplicate node payload: {key}")
            original_nodes[key] = node
    original_edges: dict[str, dict[str, Any]] = {}
    for edge in graph.get("edges", []):
        key = canonical_edge(str(edge.get("node_a") or ""), str(edge.get("node_b") or ""))
        if key in edge_ranks:
            if key in original_edges:
                raise ValueError(f"Duplicate edge payload: {key}")
            original_edges[key] = edge
    missing = ([f"node:{key}" for key in node_ranks if key not in original_nodes]
               + [f"edge:{key}" for key in edge_ranks if key not in original_edges])
    missing.extend(f"{kind}:{key}:evidence_ledger" for kind, items in
                   (("node", original_nodes), ("edge", original_edges))
                   for key, item in items.items()
                   if not isinstance(item.get("evidence_ledger"), dict)
                   or item.get("evidence_ledger_missing"))
    if missing:
        raise ValueError(f"Selected path union has missing evidence payloads: {missing[:20]}")

    nodes = []
    for key, original in original_nodes.items():
        node = deepcopy(original)
        node.update(path_count=len(node_ranks[key]), path_ranks=sorted(node_ranks[key]),
                    best_path_rank=min(node_ranks[key]), is_start=key in sources,
                    is_target=key in targets, path_positions=positions[key],
                    mean_path_position=sum(p["position"] for p in positions[key]) / len(positions[key]))
        nodes.append(node)
    edges = []
    for key, original in original_edges.items():
        edge = deepcopy(original)
        edge.update(path_count=len(edge_ranks[key]), path_ranks=sorted(edge_ranks[key]),
                    best_path_rank=min(edge_ranks[key]), path_traversals=sorted(traversals[key]))
        edges.append(edge)

    original_metadata = {key: deepcopy(value) for key, value in graph.items()
                         if key not in ("nodes", "edges", "ranked_paths")}
    configuration = deepcopy(graph.get("configuration", {}))
    configuration.setdefault("path", {}).update(
        start=next(iter(sources)), target=next(iter(targets)),
        starts=sorted(sources), targets=sorted(targets), top_k=len(paths))
    selected = {key: deepcopy(graph[key]) for key in
                ("schema", "schema_version", "run_id", "semantics") if key in graph}
    selected.update(
        generated_at=datetime.now(timezone.utc).isoformat(),
        intended_question=f"Which of the returned {query_id} paths are mechanistically sound?",
        source_nodes=sorted(sources), target_nodes=sorted(targets),
        query_id=query_id, nodes=nodes, edges=edges, ranked_paths=deepcopy(paths),
        configuration=configuration,
        coverage={
            "coverage": "Every unique node and edge in the union of all returned paths for the selected query; not a top-50 display graph or the pre-search candidate graph.",
            "query_count": 1, "path_count": len(paths), "distinct_path_count": len(sequences),
            "visible_path_rank_limit": len(paths), "network_node_count": len(nodes),
            "network_edge_count": len(edges), "node_ledger_count": len(nodes),
            "edge_ledger_count": len(edges), "ledger_count": len(nodes) + len(edges),
            "errors": [],
        },
        warnings=["Offline query selection only. Original path ranks and scientific scores are unchanged; no new inference or paid API calls were performed. See selection_provenance.original_metadata.warnings for source-run caveats."],
        missing_evidence_ledgers=[],
        selection_provenance={
            "operation": "all_returned_paths_for_one_query",
            "query_id": query_id, "source_session_id": graph.get("run_id"),
            "original_counts": {"nodes": len(graph.get("nodes", [])),
                                "edges": len(graph.get("edges", [])),
                                "paths": len(graph.get("ranked_paths", []))},
            "original_metadata": original_metadata,
            "preservation": "All selected path payloads and all evidence ledgers, interpretations, scores, and directionality are preserved exactly. Membership counts, path ranks/positions, and observed traversal lists are recalculated for this query.",
        },
    )
    return selected


def _find_session(source: Path, session_id: str, archive_member: str | None) -> tuple[str, dict[str, Any]]:
    if archive_member:
        if source.suffix.lower() != ".zip":
            raise ValueError("--archive-member requires a ZIP input")
        with zipfile.ZipFile(source) as archive:
            graph = json.loads(gzip.decompress(archive.read(archive_member)))
        if str(graph.get("run_id")) != session_id:
            raise ValueError(f"Archive member does not contain requested session {session_id}")
        return archive_member, graph
    match = None
    for origin, stream in _agent_streams(source):
        graph = json.load(stream)
        if str(graph.get("run_id") or Path(origin).parent.name) == session_id:
            if match is not None:
                raise ValueError(f"Session is ambiguous; specify --archive-member: {session_id}")
            match = (origin, graph)
    if match is None:
        raise ValueError(f"Unknown session: {session_id}")
    return match


def export_query(input_path: str | Path, output_directory: str | Path, *,
                 session_id: str, query_id: str, expected_path_count: int | None = None,
                 archive_member: str | None = None) -> dict[str, Any]:
    """Create ``agent_graph.json.gz`` and an auditable ``selection.json``."""
    source = Path(input_path).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    graph_path = output / "agent_graph.json.gz"
    selection_path = output / "selection.json"
    if graph_path.exists() or selection_path.exists():
        raise FileExistsError(f"Refusing to overwrite an existing selection in {output}")
    origin, original = _find_session(source, session_id, archive_member)
    selected = select_query(original, query_id)
    if expected_path_count is not None and len(selected["ranked_paths"]) != expected_path_count:
        raise ValueError(f"Expected {expected_path_count} paths, found {len(selected['ranked_paths'])}")
    source_hash = None
    if source.is_file():
        with source.open("rb") as handle:
            source_hash = hashlib.file_digest(handle, "sha256").hexdigest()
    selected["selection_provenance"].update(source_input=str(source), source_origin=origin,
                                              source_input_sha256=source_hash)
    encoded = json.dumps(selected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    compressed = gzip.compress(encoded, mtime=0)
    manifest = {
        "schema_version": 1, "created_at": selected["generated_at"],
        "input_path": str(source), "archive_member": origin,
        "source_input_sha256": source_hash, "session_id": session_id,
        "query_id": query_id, "source_nodes": selected["source_nodes"],
        "target_nodes": selected["target_nodes"],
        "counts": {"sessions": 1, "queries": 1, "paths": len(selected["ranked_paths"]),
                   "distinct_path_sequences": len(selected["ranked_paths"]),
                   "nodes": len(selected["nodes"]), "edges": len(selected["edges"])},
        "original_session_counts": selected["selection_provenance"]["original_counts"],
        "output_graph": str(graph_path), "graph_sha256": hashlib.sha256(compressed).hexdigest(),
        "all_selected_path_nodes_and_edges_covered": True,
        "evidence_ledgers_preserved_exactly": True, "paid_api_calls": 0,
        "note": "Offline selection, not a rerun. Original ranks, scores, directionality, complete Bayes-factor ledgers, and interpretations are retained. No inventory database or LLM run was created.",
    }
    output.mkdir(parents=True, exist_ok=True)
    with graph_path.open("xb") as handle:
        handle.write(compressed)
    with selection_path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_path")
    parser.add_argument("output_directory")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--query-id", required=True)
    parser.add_argument("--expected-path-count", type=int)
    parser.add_argument("--archive-member")
    args = parser.parse_args()
    print(json.dumps(export_query(**vars(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
