"""Build small directed candidate graphs from the V1 ranked-path ensemble."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .data import normalize_symbol


@dataclass(frozen=True)
class CandidateGraph:
    graph_id: str
    states: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]
    input_targets: tuple[str, ...]
    source_symbol: str
    target_symbol: str
    construction: str
    edge_prior_support: dict[str, float] = field(default_factory=dict, compare=False)
    contracted_segments: dict[str, list[str]] = field(default_factory=dict, compare=False)

    @property
    def signature(self) -> str:
        payload = {
            "states": self.states,
            "edges": sorted(self.edges),
            "input_targets": sorted(self.input_targets),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def to_record(self) -> dict[str, object]:
        return {
            "graph_id": self.graph_id,
            "signature": self.signature,
            "state_count": len(self.states),
            "edge_count": len(self.edges),
            "input_target_count": len(self.input_targets),
            "states": ";".join(self.states),
            "edges": ";".join(f"{source}->{target}" for source, target in self.edges),
            "input_targets": ";".join(self.input_targets),
            "source_symbol": self.source_symbol,
            "target_symbol": self.target_symbol,
            "construction": self.construction,
            "score_interpretation": "candidate hypothesis only; V1 edge values are prior support, not causal posterior weights",
        }


def _split_path(value: object) -> list[str]:
    return [normalize_symbol(item) for item in str(value).split("->") if normalize_symbol(item)]


def choose_module_states(
    ranked_paths: pd.DataFrame,
    measured_genes: Iterable[str],
    *,
    source_symbol: str,
    target_symbol: str,
    maximum_states: int = 6,
) -> tuple[str, ...]:
    measured = {normalize_symbol(symbol) for symbol in measured_genes}
    source = normalize_symbol(source_symbol)
    target = normalize_symbol(target_symbol)
    frequency: Counter[str] = Counter()
    for path in ranked_paths["path_symbols"]:
        frequency.update(_split_path(path))
    eligible = [
        symbol
        for symbol, _ in frequency.most_common()
        if symbol in measured and symbol != source
    ]
    selected: list[str] = []
    if target in measured:
        selected.append(target)
    for symbol in eligible:
        if symbol not in selected:
            selected.append(symbol)
        if len(selected) >= maximum_states:
            break
    if len(selected) < 2:
        raise ValueError(
            "the ranked paths and phosphoproteomic workbook share fewer than two measurable states"
        )
    return tuple(selected)


def _contract_paths(
    ranked_paths: pd.DataFrame,
    states: tuple[str, ...],
    source_symbol: str,
    top_k: int,
) -> tuple[
    set[tuple[str, str]],
    set[str],
    dict[str, float],
    dict[str, list[str]],
]:
    state_set = set(states)
    source = normalize_symbol(source_symbol)
    edges: set[tuple[str, str]] = set()
    input_targets: set[str] = set()
    prior_values: defaultdict[str, list[float]] = defaultdict(list)
    segments: defaultdict[str, list[str]] = defaultdict(list)
    subset = ranked_paths.sort_values("rank").head(top_k)
    for row in subset.itertuples(index=False):
        raw_nodes = _split_path(row.path_symbols)
        raw_probabilities = [float(value) for value in str(row.edge_probabilities).split(";") if str(value).strip()]
        retained_indices = [index for index, node in enumerate(raw_nodes) if node == source or node in state_set]
        for left, right in zip(retained_indices[:-1], retained_indices[1:]):
            upstream = raw_nodes[left]
            downstream = raw_nodes[right]
            if upstream == downstream:
                continue
            segment_nodes = raw_nodes[left : right + 1]
            segment_probabilities = raw_probabilities[left:right]
            support = (
                float(np.exp(np.mean(np.log(np.clip(segment_probabilities, 1e-12, 1.0)))))
                if segment_probabilities
                else 0.5
            )
            if upstream == source:
                input_targets.add(downstream)
                key = f"INPUT->{downstream}"
            elif upstream in state_set and downstream in state_set:
                edges.add((upstream, downstream))
                key = f"{upstream}->{downstream}"
            else:
                continue
            prior_values[key].append(support)
            segment_text = " -> ".join(segment_nodes)
            if segment_text not in segments[key]:
                segments[key].append(segment_text)
    prior_support = {key: max(values) for key, values in prior_values.items()}
    return edges, input_targets, prior_support, dict(segments)


def build_candidate_graphs(
    ranked_paths_path: str | Path,
    measured_genes: Iterable[str],
    *,
    maximum_states: int = 6,
    path_cutoffs: Iterable[int] = (1, 5, 20, 100),
) -> list[CandidateGraph]:
    """Create canonical candidate graphs by progressively unioning V1 paths.

    Nodes omitted from the compact observed module are contracted.  Every such
    edge is explicitly labeled as an *effective mechanism*, not a claim that the
    retained proteins touch directly.
    """

    path = Path(ranked_paths_path).resolve()
    frame = pd.read_csv(path, sep="\t")
    required = {"rank", "start_symbol", "target_symbol", "path_symbols", "edge_probabilities"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"ranked paths are missing columns: {', '.join(sorted(missing))}")
    source = normalize_symbol(frame.iloc[0]["start_symbol"])
    target = normalize_symbol(frame.iloc[0]["target_symbol"])
    states = choose_module_states(
        frame,
        measured_genes,
        source_symbol=source,
        target_symbol=target,
        maximum_states=maximum_states,
    )
    candidates: list[CandidateGraph] = [
        CandidateGraph(
            graph_id="input_only",
            states=states,
            edges=(),
            input_targets=states,
            source_symbol=source,
            target_symbol=target,
            construction="no-cross-talk baseline: independent input-driven states",
        )
    ]
    seen = {candidates[0].signature}
    maximum_available = int(frame["rank"].max())
    for requested in path_cutoffs:
        top_k = min(int(requested), maximum_available)
        edges, input_targets, support, segments = _contract_paths(frame, states, source, top_k)
        candidate = CandidateGraph(
            graph_id=f"v1_top_{top_k}_path_union",
            states=states,
            edges=tuple(sorted(edges)),
            input_targets=tuple(sorted(input_targets)),
            source_symbol=source,
            target_symbol=target,
            construction=(
                f"directed union of the top {top_k} V1 paths after contracting unmeasured/excluded nodes; "
                "contracted edges are effective mechanisms"
            ),
            edge_prior_support=support,
            contracted_segments=segments,
        )
        if candidate.signature not in seen:
            candidates.append(candidate)
            seen.add(candidate.signature)
    return candidates


def write_candidate_tables(candidates: list[CandidateGraph], output_directory: str | Path) -> None:
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([candidate.to_record() for candidate in candidates]).to_csv(
        output / "candidates.tsv", sep="\t", index=False
    )
    rows: list[dict[str, object]] = []
    for candidate in candidates:
        for target in candidate.input_targets:
            key = f"INPUT->{target}"
            rows.append(
                {
                    "graph_id": candidate.graph_id,
                    "edge_id": key,
                    "source": candidate.source_symbol,
                    "target": target,
                    "mechanism_type": "effective_input",
                    "v1_prior_support": candidate.edge_prior_support.get(key, np.nan),
                    "contracted_segments": " | ".join(candidate.contracted_segments.get(key, [])),
                }
            )
        for source, target in candidate.edges:
            key = f"{source}->{target}"
            rows.append(
                {
                    "graph_id": candidate.graph_id,
                    "edge_id": key,
                    "source": source,
                    "target": target,
                    "mechanism_type": "effective_directed_state_coupling",
                    "v1_prior_support": candidate.edge_prior_support.get(key, np.nan),
                    "contracted_segments": " | ".join(candidate.contracted_segments.get(key, [])),
                }
            )
    pd.DataFrame(rows).to_csv(output / "candidate_edges.tsv", sep="\t", index=False)
