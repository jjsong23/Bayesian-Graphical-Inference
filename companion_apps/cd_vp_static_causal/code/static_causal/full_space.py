from __future__ import annotations

import csv
import gzip
import hashlib
import html
import json
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .data import InputBundle, load_inputs, sha256_file
from .exploration import (
    ExplorationGraph,
    TypedEdge,
    _has_cycle,
    _kirby_deltas,
    _select_dynamic_graphs,
    common_background_likelihood,
    fit_dynamic_graph,
)


def _safe_float(value: object, fallback: float = 0.5) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def rank_map_preferences(
    values: np.ndarray, target_mean: float, temperature: float = 1.0, epsilon: float = 1e-7
) -> np.ndarray:
    """Map arbitrary confidence ranks to sparse participation preferences.

    This deliberately does not claim that the source values are calibrated
    inclusion probabilities.  The intercept is solved so that the configured
    mean graph density is respected while ranks remain monotone.
    """
    values = np.asarray(values, dtype=float)
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    quantile = (ranks + 0.5) / max(1, len(values))
    logit_rank = np.log(quantile / (1.0 - quantile)) * float(temperature)
    low, high = -40.0, 40.0
    for _ in range(100):
        midpoint = (low + high) / 2
        mapped = 1.0 / (1.0 + np.exp(-(midpoint + logit_rank)))
        if float(mapped.mean()) > target_mean:
            high = midpoint
        else:
            low = midpoint
    mapped = 1.0 / (1.0 + np.exp(-(((low + high) / 2) + logit_rank)))
    return np.clip(mapped, epsilon, 1.0 - epsilon)


@dataclass
class FullRegistryIndex:
    node_names: list[str]
    node_raw: np.ndarray
    node_preference: np.ndarray
    node_position: dict[str, int]
    edge_a: list[str]
    edge_b: list[str]
    edge_raw: np.ndarray
    edge_preference: np.ndarray
    edge_order: np.ndarray
    edge_cdf: np.ndarray
    adjacency: dict[str, np.ndarray]
    anchor_connectors: dict[str, int]
    structural_baseline: float
    node_log_odds: np.ndarray
    edge_log_odds: np.ndarray
    alpha_node: float
    alpha_edge: float
    complexity_penalty: float

    @property
    def edge_count(self) -> int:
        return len(self.edge_a)

    def edge_id(self, index: int) -> str:
        return f"FE{index + 1:07d}"


@dataclass
class FullGraph:
    graph_id: str
    nodes: frozenset[str]
    edge_states: tuple[tuple[int, str, str, str], ...]
    strategy: str
    seed: int

    def digest(self) -> str:
        node_part = ",".join(sorted(self.nodes))
        edge_part = ";".join(f"{idx}:{direction}:{sign}:{mechanism}" for idx, direction, sign, mechanism in self.edge_states)
        return hashlib.sha256(f"N[{node_part}]E[{edge_part}]".encode()).hexdigest()


def build_full_index(bundle: InputBundle, config: dict[str, object]) -> tuple[FullRegistryIndex, dict[str, object]]:
    node_names = sorted(bundle.nodes)
    node_raw = np.array([
        _safe_float(bundle.nodes[name].get("gui_posterior"), _safe_float(bundle.nodes[name].get("mean_incident_edge_probability"), 0.5))
        for name in node_names
    ])
    edge_a: list[str] = []
    edge_b: list[str] = []
    edge_raw_values: list[float] = []
    adjacency_lists: dict[str, list[int]] = defaultdict(list)
    for index, (a, b, value) in enumerate(bundle.edges):
        edge_a.append(a)
        edge_b.append(b)
        edge_raw_values.append(float(value))
        adjacency_lists[a].append(index)
        adjacency_lists[b].append(index)
    edge_raw = np.asarray(edge_raw_values, dtype=float)
    node_preference = rank_map_preferences(
        node_raw,
        float(config.get("target_node_fraction", 0.03)),
        float(config.get("node_rank_temperature", 1.0)),
        float(config.get("epsilon", 1e-7)),
    )
    edge_preference = rank_map_preferences(
        edge_raw,
        float(config.get("target_edge_fraction", 5e-5)),
        float(config.get("edge_rank_temperature", 1.0)),
        float(config.get("epsilon", 1e-7)),
    )
    alpha_node = float(config.get("alpha_node", 0.35))
    alpha_edge = float(config.get("alpha_edge", 0.65))
    node_log_odds = np.log(node_preference / (1.0 - node_preference))
    edge_log_odds = np.log(edge_preference / (1.0 - edge_preference))
    baseline = alpha_node * float(np.log1p(-node_preference).sum()) + alpha_edge * float(np.log1p(-edge_preference).sum())
    edge_order = np.argsort(edge_raw, kind="mergesort")
    edge_cdf = np.cumsum(edge_preference)
    edge_cdf /= edge_cdf[-1]
    adjacency = {name: np.asarray(indices, dtype=np.int32) for name, indices in adjacency_lists.items()}
    anchor = str(config.get("anchor_gene", "PRKACA")).upper()
    connectors: dict[str, int] = {}
    for edge_index in adjacency.get(anchor, np.array([], dtype=np.int32)):
        other = edge_b[int(edge_index)] if edge_a[int(edge_index)] == anchor else edge_a[int(edge_index)]
        current = connectors.get(other)
        if current is None or edge_raw[int(edge_index)] > edge_raw[current]:
            connectors[other] = int(edge_index)
    index = FullRegistryIndex(
        node_names, node_raw, node_preference, {name: i for i, name in enumerate(node_names)},
        edge_a, edge_b, edge_raw, edge_preference, edge_order, edge_cdf, adjacency,
        connectors, baseline, node_log_odds, edge_log_odds, alpha_node, alpha_edge,
        float(config.get("complexity_penalty", 0.03)),
    )
    audit = {
        "full_registry_node_count": len(node_names),
        "full_registry_edge_count": len(edge_a),
        "universe_mode": "full_registry",
        "node_source_score_range": [float(node_raw.min()), float(node_raw.max())],
        "edge_source_score_range": [float(edge_raw.min()), float(edge_raw.max())],
        "mapped_node_preference_range": [float(node_preference.min()), float(node_preference.max())],
        "mapped_edge_preference_range": [float(edge_preference.min()), float(edge_preference.max())],
        "mapped_node_preference_mean": float(node_preference.mean()),
        "mapped_edge_preference_mean": float(edge_preference.mean()),
        "mapping": "monotone rank-logit mapping with intercept chosen for configured sparse mean participation",
        "score_semantics": "soft preferences; source scores are preserved separately and are not asserted to be calibrated inclusion probabilities",
        "sparse_scoring": "full absent-state baseline precomputed once; each graph adds only selected node/edge log-odds deltas",
    }
    return index, audit


def _edge_quartile_range(index: FullRegistryIndex, quartile: int) -> np.ndarray:
    start = quartile * index.edge_count // 4
    stop = (quartile + 1) * index.edge_count // 4
    return index.edge_order[start:stop]


def _sample_global_edge(rng: np.random.Generator, index: FullRegistryIndex, strategy: str) -> int:
    if strategy == "prior_weighted_frontier":
        return int(np.searchsorted(index.edge_cdf, rng.random(), side="right"))
    if strategy == "low_confidence_frontier":
        pool = _edge_quartile_range(index, 0)
        return int(pool[int(rng.integers(0, len(pool)))])
    if strategy == "score_stratified_frontier":
        pool = _edge_quartile_range(index, int(rng.integers(0, 4)))
        return int(pool[int(rng.integers(0, len(pool)))])
    return int(rng.integers(0, index.edge_count))


def _type_state(rng: np.random.Generator, feedback: bool = False) -> tuple[str, str, str]:
    direction = str(rng.choice(
        ["a_to_b", "b_to_a", "both", "unknown"],
        p=[0.33, 0.33, 0.25, 0.09] if feedback else [0.39, 0.39, 0.12, 0.10],
    ))
    sign = str(rng.choice(["activation", "inhibition", "unknown"], p=[0.35, 0.25, 0.40]))
    mechanism = str(rng.choice(["regulatory", "phosphorylation", "physical_unspecified", "unknown"], p=[0.35, 0.20, 0.15, 0.30]))
    return direction, sign, mechanism


def construct_full_graph(
    rng: np.random.Generator,
    index: FullRegistryIndex,
    config: dict[str, object],
    strategy: str,
    seed: int,
    graph_number: int,
    forced_edges: list[int] | None = None,
) -> FullGraph:
    anchor = str(config.get("anchor_gene", "PRKACA")).upper()
    max_nodes = int(config.get("max_graph_nodes", 64))
    target_edges = int(rng.integers(int(config.get("min_graph_edges", 18)), int(config.get("max_graph_edges", 64)) + 1))
    active = {anchor} if anchor in index.node_position else set()
    selected: dict[int, tuple[str, str, str]] = {}

    def add_edge(edge_index: int, connect: bool = True) -> bool:
        edge_index = int(edge_index)
        a, b = index.edge_a[edge_index], index.edge_b[edge_index]
        new_nodes = {a, b} - active
        connector_indices = []
        if connect and active and anchor in active:
            for node in new_nodes:
                connector = index.anchor_connectors.get(node)
                if connector is not None and connector not in selected:
                    connector_indices.append(connector)
        connector_nodes = set()
        for connector in connector_indices:
            connector_nodes.update((index.edge_a[connector], index.edge_b[connector]))
        if len(active | {a, b} | connector_nodes) > max_nodes:
            return False
        active.update((a, b))
        selected.setdefault(edge_index, _type_state(rng, strategy == "feedback_frontier"))
        for connector in connector_indices:
            active.update((index.edge_a[connector], index.edge_b[connector]))
            selected.setdefault(connector, _type_state(rng, False))
        return True

    for edge_index in forced_edges or []:
        add_edge(edge_index, connect=True)
    if not selected:
        add_edge(_sample_global_edge(rng, index, strategy), connect=True)
    attempts = 0
    while len(selected) < target_edges and attempts < target_edges * 30:
        attempts += 1
        if strategy == "uniform_node_frontier":
            node = str(rng.choice(index.node_names))
            if len(active) < max_nodes:
                active.add(node)
            incident = index.adjacency.get(node)
            if incident is None or not len(incident):
                continue
            edge_index = int(incident[int(rng.integers(0, len(incident)))])
        elif active and rng.random() < 0.75:
            node = str(rng.choice(sorted(active)))
            incident = index.adjacency.get(node)
            if incident is None or not len(incident):
                continue
            edge_index = int(incident[int(rng.integers(0, len(incident)))])
        else:
            edge_index = _sample_global_edge(rng, index, strategy)
        add_edge(edge_index, connect=True)
    states = tuple(sorted((edge_index, *state) for edge_index, state in selected.items()))
    return FullGraph(f"FG{graph_number:07d}", frozenset(active), states, strategy, seed)


def graph_structural_score(graph: FullGraph, index: FullRegistryIndex, config: dict[str, object]) -> dict[str, float]:
    node_positions = [index.node_position[node] for node in graph.nodes]
    edge_positions = [state[0] for state in graph.edge_states]
    node_delta = index.alpha_node * float(index.node_log_odds[node_positions].sum())
    edge_delta = index.alpha_edge * float(index.edge_log_odds[edge_positions].sum())
    direction_weights = config.get("direction_weights", {"a_to_b": 0.42, "b_to_a": 0.42, "both": 0.10, "unknown": 0.06})
    sign_weights = config.get("sign_weights", {"activation": 0.30, "inhibition": 0.25, "unknown": 0.45})
    mechanism_weights = config.get("mechanism_weights", {"regulatory": 0.30, "phosphorylation": 0.20, "physical_unspecified": 0.15, "unknown": 0.35})
    typed = sum(
        math.log(float(direction_weights[direction])) + math.log(float(sign_weights[sign])) + math.log(float(mechanism_weights[mechanism]))
        for _, direction, sign, mechanism in graph.edge_states
    )
    penalty = -index.complexity_penalty * (len(graph.nodes) + len(graph.edge_states))
    return {
        "structural_absent_baseline": index.structural_baseline,
        "selected_node_log_odds_delta": node_delta,
        "selected_edge_log_odds_delta": edge_delta,
        "direction_sign_mechanism_preference": typed,
        "structural_complexity_penalty": penalty,
        "structural_preference": index.structural_baseline + node_delta + edge_delta + typed + penalty,
    }


def to_exploration_graph(graph: FullGraph, index: FullRegistryIndex) -> ExplorationGraph:
    edges = []
    for edge_index, direction, sign, mechanism in graph.edge_states:
        edges.append(TypedEdge(index.edge_id(edge_index), index.edge_a[edge_index], index.edge_b[edge_index], direction, sign, mechanism))
    return ExplorationGraph(graph.graph_id, graph.nodes, tuple(edges), graph.strategy, graph.seed)


def coverage_seed_batches(index: FullRegistryIndex, config: dict[str, object]) -> list[list[int]]:
    scan = dict(config.get("coverage_scan", {}))
    if not scan.get("enabled", False):
        return []
    shard_count = int(scan.get("shard_count", 64))
    shard_index = int(scan.get("shard_index", 0))
    if not 0 <= shard_index < shard_count:
        raise ValueError("coverage_scan.shard_index must be in [0, shard_count)")
    passes = int(scan.get("passes", 1))
    batch_size = int(scan.get("seed_edges_per_graph", 16))
    seed = int(scan.get("seed", 101))
    members = np.arange(shard_index, index.edge_count, shard_count, dtype=np.int64)
    batches: list[list[int]] = []
    for pass_index in range(passes):
        order = members.copy()
        np.random.default_rng(seed + pass_index).shuffle(order)
        batches.extend(order[start : start + batch_size].astype(int).tolist() for start in range(0, len(order), batch_size))
    return batches


def generate_full_bank(
    index: FullRegistryIndex,
    config: dict[str, object],
    deadline: float | None = None,
) -> tuple[list[FullGraph], int, bool]:
    strategies = [
        "uniform_edge_frontier", "score_stratified_frontier", "low_confidence_frontier",
        "uniform_node_frontier", "prior_weighted_frontier", "feedback_frontier",
    ]
    seeds = [int(value) for value in config.get("seeds", [17, 29, 43, 71])]
    forced_batches = coverage_seed_batches(index, config)
    scan = dict(config.get("coverage_scan", {}))
    target = len(forced_batches) if scan.get("only", False) else max(int(config.get("target_graph_count", 10_000)), len(forced_batches))
    bank: list[FullGraph] = []
    seen: set[str] = set()
    attempts = 0
    while len(bank) < target and attempts < target * 30:
        if deadline is not None and time.monotonic() >= deadline:
            break
        attempts += 1
        forced = forced_batches[len(bank)] if len(bank) < len(forced_batches) else None
        strategy = "coverage_shard_scan" if forced else strategies[(attempts - 1) % len(strategies)]
        seed = seeds[(attempts - 1) % len(seeds)]
        rng = np.random.default_rng(seed * 1_000_003 + attempts)
        graph = construct_full_graph(rng, index, config, strategy, seed, len(bank) + 1, forced)
        digest = graph.digest()
        if digest in seen:
            continue
        seen.add(digest)
        bank.append(graph)
    return bank, attempts, len(bank) < target


def _full_graph_record(graph: FullGraph, index: FullRegistryIndex) -> dict[str, object]:
    return {
        "graph_id": graph.graph_id, "canonical_sha256": graph.digest(), "strategy": graph.strategy,
        "seed": graph.seed, "nodes": sorted(graph.nodes),
        "edges": [{
            "edge_id": index.edge_id(edge_index), "source_node_a": index.edge_a[edge_index],
            "source_node_b": index.edge_b[edge_index], "source_score": float(index.edge_raw[edge_index]),
            "mapped_preference": float(index.edge_preference[edge_index]), "direction": direction,
            "sign": sign, "mechanism": mechanism,
        } for edge_index, direction, sign, mechanism in graph.edge_states],
    }


def _write_coverage(
    output: Path, bank: list[FullGraph], index: FullRegistryIndex, sparse_only: bool = False
) -> tuple[list[dict[str, object]], list[dict[str, object]], np.ndarray, str]:
    node_count = Counter(node for graph in bank for node in graph.nodes)
    edge_count = np.zeros(index.edge_count, dtype=np.uint32)
    for graph in bank:
        edge_count[[state[0] for state in graph.edge_states]] += 1
    node_rows = [{
        "node": name, "source_score": index.node_raw[pos], "mapped_preference": index.node_preference[pos],
        "graphs_containing": node_count[name], "measured": "",
    } for pos, name in enumerate(index.node_names)]
    pd.DataFrame(node_rows).to_csv(output / "full_node_coverage.tsv", sep="\t", index=False)
    quartile_summary = []
    coverage_name = "shard_edge_coverage.tsv.gz" if sparse_only else "full_edge_coverage.tsv.gz"
    with gzip.open(output / coverage_name, "wt", encoding="utf-8", newline="") as handle:
        fields = ["edge_id", "node_a", "node_b", "source_score", "mapped_preference", "confidence_quartile", "graphs_containing"]
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        quartile_by_index = np.empty(index.edge_count, dtype=np.uint8)
        for quartile in range(4):
            quartile_by_index[_edge_quartile_range(index, quartile)] = quartile + 1
        for edge_index in range(index.edge_count):
            if sparse_only and edge_count[edge_index] == 0:
                continue
            writer.writerow({
                "edge_id": index.edge_id(edge_index), "node_a": index.edge_a[edge_index], "node_b": index.edge_b[edge_index],
                "source_score": index.edge_raw[edge_index], "mapped_preference": index.edge_preference[edge_index],
                "confidence_quartile": int(quartile_by_index[edge_index]), "graphs_containing": int(edge_count[edge_index]),
            })
    for quartile in range(4):
        positions = _edge_quartile_range(index, quartile)
        counts = edge_count[positions]
        quartile_summary.append({
            "confidence_quartile": quartile + 1, "eligible_edges": len(positions),
            "covered_edges": int((counts > 0).sum()), "coverage_fraction": float((counts > 0).mean()),
            "total_appearances": int(counts.sum()), "mean_appearances": float(counts.mean()),
            "maximum_appearances": int(counts.max()) if len(counts) else 0,
        })
    pd.DataFrame(quartile_summary).to_csv(output / "full_edge_coverage_summary.tsv", sep="\t", index=False)
    return node_rows, quartile_summary, edge_count, coverage_name


def _write_shard_plan(output: Path, index: FullRegistryIndex, config: dict[str, object]) -> list[dict[str, object]]:
    scan = dict(config.get("coverage_scan", {}))
    shard_count = int(scan.get("shard_count", 64))
    batch = int(scan.get("seed_edges_per_graph", 16))
    rows = []
    for shard in range(shard_count):
        count = (index.edge_count - 1 - shard) // shard_count + 1 if shard < index.edge_count else 0
        rows.append({
            "shard_index": shard, "shard_count": shard_count, "edge_count": count,
            "seed_edges_per_graph": batch, "graphs_for_one_complete_pass": int(math.ceil(count / max(1, batch))),
            "parallel_command_suffix": f'--coverage-only --coverage-shard-index {shard} --coverage-shard-count {shard_count}',
        })
    pd.DataFrame(rows).to_csv(output / "edge_coverage_shard_plan.tsv", sep="\t", index=False)
    return rows


def _render_report(output: Path, manifest: dict[str, object], rankings: list[dict[str, object]], quartiles: list[dict[str, object]]) -> None:
    def table(rows: list[dict[str, object]], columns: list[str]) -> str:
        head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(column, '')))}</td>" for column in columns) + "</tr>" for row in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    top = sorted(rankings, key=lambda row: -float(row["structural_preference"]))[:15]
    dynamic = sorted([row for row in rankings if row["dynamic_fit_status"] == "dynamic_fit"], key=lambda row: -float(row["model_evidence_approximation"]))[:15]
    columns = ["graph_id", "strategy", "node_count", "edge_count", "has_feedback_cycle", "unmeasured_intermediate_count", "structural_preference", "dynamic_fit_status", "graph_specific_log_likelihood_improvement", "predictive_validation"]
    text = f"""<!doctype html><meta charset='utf-8'><title>Full-registry graph exploration</title>
<style>body{{font:15px system-ui;max-width:1250px;margin:35px auto;color:#17372f;background:#f3f5f0}}section{{background:white;padding:22px;margin:16px 0;border-radius:14px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:7px;border-bottom:1px solid #dbe4df;text-align:left}}.warn{{padding:12px;background:#fff4d8;border-left:4px solid #d29a22}}</style>
<h1>Full-registry signaling-graph exploration</h1>
<section><h2>Scope</h2><p>All <strong>{manifest['full_registry_node_count']:,}</strong> nodes and <strong>{manifest['full_registry_edge_count']:,}</strong> edges are eligible for generation. This run created {manifest['actual_graph_count']:,} unique sparse graphs. Eligibility and nonzero proposal probability do not imply exhaustive coverage.</p><div class='warn'>The source scores were rank-mapped to sparse participation preferences. Original values are retained. Exploration frequencies are not posterior probabilities.</div></section>
<section><h2>Observed coverage</h2>{table(quartiles,['confidence_quartile','eligible_edges','covered_edges','coverage_fraction','total_appearances','mean_appearances','maximum_appearances'])}<p><a href='{manifest.get('coverage_ledger','full_edge_coverage.tsv.gz')}'>Edge coverage ledger</a> · <a href='full_node_coverage.tsv'>Node coverage</a> · <a href='edge_coverage_shard_plan.tsv'>Parallel full-pass plan</a></p></section>
<section><h2>Efficient whole-space mechanism</h2><p>Structural scoring precomputes the full absent-state contribution once and adds sparse log-odds deltas only for selected items. Global-uniform, score-stratified, low-confidence, node-uniform, prior-weighted, and feedback frontier proposals can reach every registry item. Deterministic shards can force every edge into at least one graph across parallel jobs.</p></section>
<section><h2>Leading structural alternatives</h2>{table(top,columns)}</section>
<section><h2>Leading dynamically fitted alternatives</h2>{table(dynamic,columns)}</section>"""
    (output / "full_space_report.html").write_text(text, encoding="utf-8")


def run_full_space(config: dict[str, object], output: str | Path) -> dict[str, object]:
    started = time.time()
    monotonic_started = time.monotonic()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = load_inputs(config)
    full_config = dict(config.get("full_space", {}))
    dynamic_config = dict(config.get("dynamic_fit", {}))
    runtime_budget = float(full_config.get("runtime_budget_seconds", 0.0))
    finalization_reserve = float(full_config.get("finalization_reserve_seconds", 180.0))
    dynamic_reserve = float(full_config.get("dynamic_fit_reserve_seconds", 300.0))
    overall_deadline = monotonic_started + runtime_budget if runtime_budget > 0 else None
    generation_deadline = None
    if overall_deadline is not None:
        generation_deadline = overall_deadline - finalization_reserve - dynamic_reserve
        if generation_deadline <= time.monotonic():
            raise ValueError("runtime budget must exceed dynamic and finalization reserves")
    index, audit = build_full_index(bundle, full_config)
    bank, generation_attempts, generation_budget_exhausted = generate_full_bank(
        index, full_config, generation_deadline
    )
    converted = [to_exploration_graph(graph, index) for graph in bank]
    scores = {graph.graph_id: graph_structural_score(full, index, full_config) for graph, full in zip(converted, bank)}
    dynamic_selected, unusual_ids = _select_dynamic_graphs(converted, scores, dynamic_config)
    times, responses, _ = _kirby_deltas(bundle)
    background = common_background_likelihood(responses)
    dynamic_rows: dict[str, dict[str, object]] = {}
    parameter_rows: list[dict[str, object]] = []
    prediction_rows: list[dict[str, object]] = []
    dynamic_budget_exhausted = False
    for graph in dynamic_selected:
        if overall_deadline is not None and time.monotonic() >= overall_deadline - finalization_reserve:
            dynamic_budget_exhausted = True
            break
        row, parameters, predictions = fit_dynamic_graph(graph, bundle, times, responses, dynamic_config, background)
        dynamic_rows[graph.graph_id] = row
        parameter_rows.extend(parameters)
        prediction_rows.extend(predictions)
    measured = set(bundle.representative_sites)
    rankings = []
    for full, graph in zip(bank, converted):
        row: dict[str, object] = {
            "graph_id": full.graph_id, "canonical_sha256": full.digest(), "strategy": full.strategy, "seed": full.seed,
            "node_count": len(full.nodes), "edge_count": len(full.edge_states), "has_feedback_cycle": _has_cycle(graph),
            "measured_node_count": sum(node in measured for node in full.nodes),
            "unmeasured_intermediate_count": sum(node not in measured for node in full.nodes),
            **scores[full.graph_id], "scoring_status": "structure_only",
        }
        if full.graph_id in dynamic_rows:
            row.update(dynamic_rows[full.graph_id])
            row["scoring_status"] = row["dynamic_fit_status"]
            row["dynamic_allocation"] = "deliberately_unusual" if full.graph_id in unusual_ids else "top_structural"
        else:
            row.update({
                "dynamic_fit_status": "not_allocated", "fit_failure_reason": "outside configured dynamic-fitting budget",
                "data_log_likelihood": math.nan, "parameter_regularization": math.nan,
                "predictive_validation": math.nan, "model_evidence_approximation": math.nan,
                "graph_specific_log_likelihood_improvement": math.nan, "dynamic_allocation": "not_allocated",
            })
        rankings.append(row)
    with gzip.open(output / "full_graph_bank.jsonl.gz", "wt", encoding="utf-8") as handle:
        for graph in bank:
            handle.write(json.dumps(_full_graph_record(graph, index), separators=(",", ":")) + "\n")
    pd.DataFrame(rankings).to_csv(output / "full_graph_rankings.tsv", sep="\t", index=False)
    pd.DataFrame(parameter_rows).to_csv(output / "full_dynamic_fit_parameters.tsv", sep="\t", index=False)
    pd.DataFrame(prediction_rows).to_csv(output / "full_dynamic_fit_predictions.tsv", sep="\t", index=False)
    pd.DataFrame([row for row in dynamic_rows.values() if row.get("dynamic_fit_status") == "fit_failed"]).to_csv(output / "full_dynamic_fit_failures.tsv", sep="\t", index=False)
    scan_only = bool(dict(full_config.get("coverage_scan", {})).get("only", False))
    _, quartiles, edge_counts, coverage_name = _write_coverage(output, bank, index, sparse_only=scan_only)
    shard_plan = _write_shard_plan(output, index, full_config)
    (output / "full_space_resolved_configuration.json").write_text(json.dumps(config, indent=2), encoding="utf-8")
    provenance = {
        **audit, "source_v1_run": str(bundle.source_run),
        "source_node_file": str(bundle.source_run / "node_posteriors.tsv.gz"),
        "source_edge_file": str(bundle.source_run / "supported_edges.tsv.gz"),
        "source_node_sha256": sha256_file(bundle.source_run / "node_posteriors.tsv.gz"),
        "source_edge_sha256": sha256_file(bundle.source_run / "supported_edges.tsv.gz"),
        "pooled_workbook": str(Path(str(config["pooled_workbook"])).resolve()),
        "pooled_workbook_sha256": sha256_file(Path(str(config["pooled_workbook"])).resolve()),
        "preprocessing": bundle.normalization,
    }
    (output / "full_space_provenance.json").write_text(json.dumps(provenance, indent=2), encoding="utf-8")
    manifest = {
        "schema_version": 1, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "status": "complete",
        "full_registry_node_count": len(index.node_names), "full_registry_edge_count": index.edge_count,
        "target_graph_count": int(full_config.get("target_graph_count", 10_000)), "actual_graph_count": len(bank),
        "generation_attempts": generation_attempts,
        "runtime_budget_seconds": runtime_budget if runtime_budget > 0 else None,
        "finalization_reserve_seconds": finalization_reserve if runtime_budget > 0 else None,
        "dynamic_fit_reserve_seconds": dynamic_reserve if runtime_budget > 0 else None,
        "generation_budget_exhausted": generation_budget_exhausted,
        "dynamic_budget_exhausted": dynamic_budget_exhausted,
        "covered_node_count": len({node for graph in bank for node in graph.nodes}),
        "covered_edge_count": int((edge_counts > 0).sum()),
        "covered_edge_fraction": float((edge_counts > 0).mean()),
        "dynamic_fit_selected": len(dynamic_selected),
        "dynamic_fit_attempted": len(dynamic_rows),
        "dynamic_fit_succeeded": sum(row.get("dynamic_fit_status") == "dynamic_fit" for row in dynamic_rows.values()),
        "dynamic_fit_failed": sum(row.get("dynamic_fit_status") == "fit_failed" for row in dynamic_rows.values()),
        "unusual_dynamic_allocation": len(unusual_ids),
        "coverage_scan_enabled": bool(dict(full_config.get("coverage_scan", {})).get("enabled", False)),
        "coverage_ledger": coverage_name,
        "parallel_shard_count": len(shard_plan), "elapsed_seconds": time.time() - started,
        "warnings": [
            "All registry items are eligible, but this finite run is not an exhaustive enumeration.",
            "Exploration frequency is not a posterior probability.",
            "Rank-mapped sparse preferences preserve source ordering but are not calibrated biological probabilities.",
            "Deterministic coverage shards establish exposure, not biological plausibility or posterior mass.",
            "Dynamic likelihood uses only within-Kirby matched treatment/control responses; mouse layers remain separate.",
            "A wall-clock budget can stop generation or dynamic fitting early; actual counts and budget flags are recorded.",
        ],
    }
    _render_report(output, manifest, rankings, quartiles)
    manifest["outputs"] = sorted(path.name for path in output.iterdir() if path.is_file() and path.name != "full_space_manifest.json") + ["full_space_manifest.json"]
    (output / "full_space_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest

