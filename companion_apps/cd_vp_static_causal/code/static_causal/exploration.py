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
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.optimize import least_squares

from .data import InputBundle, load_inputs, norm, sha256_file


@dataclass(frozen=True, order=True)
class CandidateEdge:
    edge_id: str
    node_a: str
    node_b: str
    preference: float
    source_score: float


@dataclass(frozen=True, order=True)
class TypedEdge:
    candidate_id: str
    node_a: str
    node_b: str
    direction: str
    sign: str
    mechanism: str

    def arrows(self) -> tuple[tuple[str, str, str], ...]:
        if self.direction == "a_to_b":
            return ((self.node_a, self.node_b, self.sign),)
        if self.direction == "b_to_a":
            return ((self.node_b, self.node_a, self.sign),)
        if self.direction == "both":
            return ((self.node_a, self.node_b, self.sign), (self.node_b, self.node_a, self.sign))
        return ()


@dataclass
class ExplorationGraph:
    graph_id: str
    nodes: frozenset[str]
    edges: tuple[TypedEdge, ...]
    strategy: str
    seed: int

    @property
    def signature(self) -> str:
        node_part = ",".join(sorted(self.nodes))
        edge_part = ";".join(
            f"{e.candidate_id}:{e.direction}:{e.sign}:{e.mechanism}" for e in sorted(self.edges)
        )
        return f"N[{node_part}]E[{edge_part}]"

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.signature.encode("utf-8")).hexdigest()


def _clip(value: float, epsilon: float) -> float:
    return min(1.0 - epsilon, max(epsilon, float(value)))


def _safe_float(value: object, fallback: float = math.nan) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if math.isfinite(result) else fallback


def _quantile_indices(length: int, count: int) -> list[int]:
    if count <= 0 or length <= 0:
        return []
    return sorted(set(int(round(v)) for v in np.linspace(0, length - 1, min(count, length))))


def build_feasible_universe(bundle: InputBundle, config: dict[str, object]) -> tuple[
    dict[str, float], list[CandidateEdge], dict[str, object]
]:
    """Create a documented finite universe without claiming it is the full graph space."""
    epsilon = float(config.get("epsilon", 0.02))
    max_nodes = int(config.get("max_candidate_nodes", 48))
    max_edges = int(config.get("max_candidate_edges", 180))
    required = {norm(v) for v in config.get("required_genes", ["PRKACA", "AQP2", "DYRK1A", "GSK3B", "CDK12"])}
    measured = set(bundle.representative_sites)
    ranked = []
    missing_preference = 0
    for symbol, row in bundle.nodes.items():
        value = _safe_float(row.get("gui_posterior"))
        if not math.isfinite(value):
            value = _safe_float(row.get("mean_incident_edge_probability"), 0.5)
            missing_preference += 1
        ranked.append((symbol, _clip(value, epsilon)))
    ranked.sort(key=lambda item: (item[1], item[0]))
    measured_ranked = [item for item in ranked if item[0] in measured]
    unmeasured_ranked = [item for item in ranked if item[0] not in measured]

    selected: dict[str, float] = {}
    for symbol in required:
        if symbol in bundle.nodes:
            selected[symbol] = dict(ranked).get(symbol, 0.5)
    remaining = max(0, max_nodes - len(selected))
    measured_budget = int(round(remaining * 0.75))
    unmeasured_budget = remaining - measured_budget

    def add_stratified(pool: list[tuple[str, float]], budget: int) -> None:
        # Equal low/middle/high representation prevents a top-score-only universe.
        thirds = [budget // 3, budget // 3, budget - 2 * (budget // 3)]
        sections = [
            pool[: max(1, len(pool) // 3)],
            pool[len(pool) // 3 : 2 * len(pool) // 3],
            pool[2 * len(pool) // 3 :],
        ]
        for section, amount in zip(sections, thirds):
            added = 0
            for index in _quantile_indices(len(section), amount * 3):
                symbol, value = section[index]
                if symbol not in selected:
                    selected[symbol] = value
                    added += 1
                if added >= amount:
                    break

    add_stratified(measured_ranked, measured_budget)
    add_stratified(unmeasured_ranked, unmeasured_budget)
    if len(selected) < max_nodes:
        for symbol, value in ranked:
            selected.setdefault(symbol, value)
            if len(selected) >= max_nodes:
                break

    eligible = [(a, b, p) for a, b, p in bundle.edges if a in selected and b in selected]
    eligible.sort(key=lambda item: (item[2], item[0], item[1]))
    chosen: dict[tuple[str, str], float] = {}
    # Preserve score tails and middle, plus many PRKACA-adjacent alternatives.
    quarters = [
        eligible[: max(1, len(eligible) // 4)],
        eligible[len(eligible) // 4 : len(eligible) // 2],
        eligible[len(eligible) // 2 : 3 * len(eligible) // 4],
        eligible[3 * len(eligible) // 4 :],
    ]
    per = max_edges // 4
    for section_index, section in enumerate(quarters):
        amount = per if section_index < 3 else max_edges - 3 * per
        added = 0
        for index in _quantile_indices(len(section), amount * 2):
            a, b, score = section[index]
            key = tuple(sorted((a, b)))
            if key not in chosen:
                chosen[key] = score
                added += 1
            if added >= amount or len(chosen) >= max_edges:
                break
    incident = [row for row in eligible if "PRKACA" in row[:2]]
    for a, b, score in sorted(incident, key=lambda item: (-item[2], item[0], item[1])):
        if len(chosen) >= max_edges:
            break
        chosen.setdefault(tuple(sorted((a, b))), score)
    edges = []
    for (a, b), source_score in sorted(chosen.items()):
        edge_id = "CE_" + hashlib.sha1(f"{a}|{b}".encode()).hexdigest()[:12]
        edges.append(CandidateEdge(edge_id, a, b, _clip(source_score, epsilon), source_score))
    audit = {
        "full_registry_node_count": len(bundle.nodes),
        "full_registry_edge_count": len(bundle.edges),
        "feasible_node_count": len(selected),
        "feasible_edge_count": len(edges),
        "measured_gene_count": len(measured),
        "node_preference_source": "node_posteriors.tsv.gz:gui_posterior; fallback mean_incident_edge_probability",
        "nodes_using_fallback_before_universe_selection": missing_preference,
        "edge_preference_source": "supported_edges.tsv.gz:edge_probability",
        "score_semantics": "soft structural preferences; not asserted to be calibrated graph-participation probabilities",
        "universe_rule": "required genes plus score-stratified measured (75%) and unmeasured (25%) proteins; score-stratified within-universe edges",
        "feasible_measured_node_count": sum(name in measured for name in selected),
        "feasible_unmeasured_node_count": sum(name not in measured for name in selected),
        "excluded_nodes": len(bundle.nodes) - len(selected),
        "excluded_edges": len(bundle.edges) - len(edges),
    }
    return selected, edges, audit


def _weighted_choice_without_replacement(
    rng: np.random.Generator, values: list[str], weights: np.ndarray, count: int
) -> set[str]:
    count = min(count, len(values))
    weights = np.asarray(weights, dtype=float)
    weights = weights / weights.sum() if weights.sum() > 0 else None
    return set(rng.choice(values, size=count, replace=False, p=weights).tolist())


def _has_cycle(graph: ExplorationGraph) -> bool:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        for source, target, _ in edge.arrows():
            adjacency[source].add(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(child) for child in adjacency[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph.nodes)


def _typed_edge(rng: np.random.Generator, edge: CandidateEdge, strategy: str) -> TypedEdge:
    if strategy == "feedback_cycle" and rng.random() < 0.55:
        direction = "both"
    else:
        direction = str(rng.choice(["a_to_b", "b_to_a", "both", "unknown"], p=[0.38, 0.38, 0.14, 0.10]))
    sign = str(rng.choice(["activation", "inhibition", "unknown"], p=[0.35, 0.25, 0.40]))
    mechanism = str(rng.choice(
        ["regulatory", "phosphorylation", "physical_unspecified", "unknown"], p=[0.35, 0.20, 0.15, 0.30]
    ))
    return TypedEdge(edge.edge_id, edge.node_a, edge.node_b, direction, sign, mechanism)


def _construct_graph(
    rng: np.random.Generator,
    nodes: dict[str, float],
    edges: list[CandidateEdge],
    strategy: str,
    seed: int,
    index: int,
    force_nodes: Iterable[str] = (),
    force_edges: Iterable[str] = (),
) -> ExplorationGraph:
    names = sorted(nodes)
    low_nodes = {name for name, _ in sorted(nodes.items(), key=lambda item: (item[1], item[0]))[: max(1, len(nodes) // 4)]}
    node_count = int(rng.integers(max(6, len(names) // 5), max(8, 4 * len(names) // 5) + 1))
    if strategy == "prior_weighted":
        active = _weighted_choice_without_replacement(rng, names, np.array([nodes[n] for n in names]), node_count)
    else:
        active = set(rng.choice(names, size=min(node_count, len(names)), replace=False).tolist())
    active.update(name for name in force_nodes if name in nodes)
    if strategy == "low_confidence_forced":
        active.update(rng.choice(sorted(low_nodes), size=min(4, len(low_nodes)), replace=False).tolist())
    active.add("PRKACA") if "PRKACA" in nodes else None

    edge_by_id = {edge.edge_id: edge for edge in edges}
    forced = [edge_by_id[eid] for eid in force_edges if eid in edge_by_id]
    for edge in forced:
        active.update((edge.node_a, edge.node_b))
    feasible = [edge for edge in edges if edge.node_a in active and edge.node_b in active]
    low_edges = sorted(feasible, key=lambda edge: (edge.preference, edge.edge_id))[: max(1, len(feasible) // 4)]
    min_edges = max(3, len(active) // 3)
    max_edges = min(len(feasible), max(min_edges, int(1.6 * len(active))))
    edge_count = int(rng.integers(min_edges, max_edges + 1)) if max_edges >= min_edges else len(feasible)
    selected: dict[str, CandidateEdge] = {edge.edge_id: edge for edge in forced}
    pool = feasible
    if strategy == "prior_weighted" and feasible:
        remaining = [edge for edge in feasible if edge.edge_id not in selected]
        amount = min(max(0, edge_count - len(selected)), len(remaining))
        if amount:
            probs = np.array([edge.preference for edge in remaining])
            for edge in rng.choice(remaining, size=amount, replace=False, p=probs / probs.sum()).tolist():
                selected[edge.edge_id] = edge
    else:
        if strategy == "low_confidence_forced" and low_edges:
            for edge in rng.choice(low_edges, size=min(5, len(low_edges)), replace=False).tolist():
                selected[edge.edge_id] = edge
        remaining = [edge for edge in pool if edge.edge_id not in selected]
        amount = min(max(0, edge_count - len(selected)), len(remaining))
        if amount:
            for edge in rng.choice(remaining, size=amount, replace=False).tolist():
                selected[edge.edge_id] = edge
    typed = tuple(sorted(_typed_edge(rng, edge, strategy) for edge in selected.values()))
    graph_id = f"G{index:06d}"
    return ExplorationGraph(graph_id, frozenset(active), typed, strategy, seed)


def _mutate_graph(
    rng: np.random.Generator,
    parent: ExplorationGraph,
    nodes: dict[str, float],
    edges: list[CandidateEdge],
    seed: int,
    index: int,
) -> ExplorationGraph:
    """Apply explicit add/remove/retype moves while preserving endpoint validity."""
    active = set(parent.nodes)
    selected = {edge.candidate_id: edge for edge in parent.edges}
    edge_by_id = {edge.edge_id: edge for edge in edges}
    move = str(rng.choice(["add_node", "remove_node", "add_edge", "remove_edge", "reverse", "change_sign", "change_mechanism"]))
    if move == "add_node":
        available = sorted(set(nodes) - active)
        if available:
            active.add(str(rng.choice(available)))
    elif move == "remove_node":
        removable = sorted(active - {"PRKACA"})
        if removable:
            removed = str(rng.choice(removable))
            active.remove(removed)
            selected = {key: value for key, value in selected.items() if removed not in (value.node_a, value.node_b)}
    elif move == "add_edge":
        available = [edge for edge in edges if edge.node_a in active and edge.node_b in active and edge.edge_id not in selected]
        if available:
            edge = rng.choice(available)
            selected[edge.edge_id] = _typed_edge(rng, edge, "mutation_combination")
    elif move == "remove_edge" and selected:
        selected.pop(str(rng.choice(sorted(selected))))
    elif selected:
        key = str(rng.choice(sorted(selected)))
        edge = selected[key]
        if move == "reverse":
            direction = {"a_to_b": "b_to_a", "b_to_a": "a_to_b", "both": "unknown", "unknown": "both"}[edge.direction]
            selected[key] = TypedEdge(edge.candidate_id, edge.node_a, edge.node_b, direction, edge.sign, edge.mechanism)
        elif move == "change_sign":
            sign = str(rng.choice([value for value in ["activation", "inhibition", "unknown"] if value != edge.sign]))
            selected[key] = TypedEdge(edge.candidate_id, edge.node_a, edge.node_b, edge.direction, sign, edge.mechanism)
        elif move == "change_mechanism":
            mechanism = str(rng.choice([value for value in ["regulatory", "phosphorylation", "physical_unspecified", "unknown"] if value != edge.mechanism]))
            selected[key] = TypedEdge(edge.candidate_id, edge.node_a, edge.node_b, edge.direction, edge.sign, mechanism)
    # A second-parent combination move makes this genuinely a mutation/combination strategy.
    if rng.random() < 0.35:
        available = [edge for edge in edges if edge.node_a in active and edge.node_b in active and edge.edge_id not in selected]
        if available:
            edge = rng.choice(available)
            selected[edge.edge_id] = _typed_edge(rng, edge, "mutation_combination")
    return ExplorationGraph(f"G{index:06d}", frozenset(active), tuple(sorted(selected.values())), "mutation_combination", seed)


def structural_score(
    graph: ExplorationGraph,
    nodes: dict[str, float],
    edges: list[CandidateEdge],
    config: dict[str, object],
) -> dict[str, float]:
    alpha_node = float(config.get("alpha_node", 0.35))
    alpha_edge = float(config.get("alpha_edge", 0.65))
    complexity = float(config.get("complexity_penalty", 0.03))
    selected_edges = {edge.candidate_id for edge in graph.edges}
    node_term = sum(math.log(p if name in graph.nodes else 1 - p) for name, p in nodes.items())
    edge_term = sum(math.log(edge.preference if edge.edge_id in selected_edges else 1 - edge.preference) for edge in edges)
    direction_weights = config.get("direction_weights", {"a_to_b": 0.42, "b_to_a": 0.42, "both": 0.10, "unknown": 0.06})
    sign_weights = config.get("sign_weights", {"activation": 0.30, "inhibition": 0.25, "unknown": 0.45})
    mechanism_weights = config.get("mechanism_weights", {"regulatory": 0.30, "phosphorylation": 0.20, "physical_unspecified": 0.15, "unknown": 0.35})
    other = 0.0
    for edge in graph.edges:
        other += math.log(max(1e-12, float(direction_weights[edge.direction])))
        other += math.log(max(1e-12, float(sign_weights[edge.sign])))
        other += math.log(max(1e-12, float(mechanism_weights[edge.mechanism])))
    penalty = -complexity * (len(graph.nodes) + len(graph.edges))
    total = alpha_node * node_term + alpha_edge * edge_term + other + penalty
    return {
        "node_preference": alpha_node * node_term,
        "edge_preference": alpha_edge * edge_term,
        "direction_sign_mechanism_preference": other,
        "structural_complexity_penalty": penalty,
        "structural_preference": total,
    }


def generate_bank(
    nodes: dict[str, float], edges: list[CandidateEdge], config: dict[str, object]
) -> tuple[list[ExplorationGraph], list[dict[str, object]]]:
    target = int(config.get("target_graph_count", 10_000))
    coverage_target = int(config.get("low_confidence_coverage_target", 20))
    seeds = [int(v) for v in config.get("seeds", [17, 29, 43, 71])]
    strategies = [
        "prior_weighted", "score_insensitive_stratified", "low_confidence_forced",
        "orientation_sign", "feedback_cycle", "mutation_combination",
    ]
    bank: list[ExplorationGraph] = []
    seen: set[str] = set()
    checkpoints: list[dict[str, object]] = []
    attempts = 0
    max_attempts = target * 30
    while len(bank) < target and attempts < max_attempts:
        attempts += 1
        strategy = strategies[(attempts - 1) % len(strategies)]
        seed = seeds[(attempts - 1) % len(seeds)]
        rng = np.random.default_rng(seed * 1_000_003 + attempts)
        if strategy == "mutation_combination" and bank:
            parent = bank[int(rng.integers(0, len(bank)))]
            graph = _mutate_graph(rng, parent, nodes, edges, seed, len(bank) + 1)
        else:
            graph = _construct_graph(rng, nodes, edges, strategy, seed, len(bank) + 1)
        if graph.digest in seen:
            continue
        seen.add(graph.digest)
        bank.append(graph)
        if len(bank) % int(config.get("checkpoint_every", 2500)) == 0:
            checkpoints.append({"graph_count": len(bank), "attempt_count": attempts, "last_graph_id": graph.graph_id})

    low_nodes = [name for name, _ in sorted(nodes.items(), key=lambda item: (item[1], item[0]))[: max(1, len(nodes) // 4)]]
    low_edges = [edge.edge_id for edge in sorted(edges, key=lambda edge: (edge.preference, edge.edge_id))[: max(1, len(edges) // 4)]]
    node_counts = Counter(node for graph in bank for node in graph.nodes)
    edge_counts = Counter(edge.candidate_id for graph in bank for edge in graph.edges)
    repair_limit = int(config.get("coverage_repair_graph_limit", 5000))
    while repair_limit > 0:
        deficient_nodes = [name for name in low_nodes if node_counts[name] < coverage_target]
        deficient_edges = [edge_id for edge_id in low_edges if edge_counts[edge_id] < coverage_target]
        if not deficient_nodes and not deficient_edges:
            break
        repair_limit -= 1
        attempts += 1
        force_nodes = deficient_nodes[:4]
        force_edges = deficient_edges[:6]
        rng = np.random.default_rng(seeds[attempts % len(seeds)] * 1_000_003 + attempts)
        graph = _construct_graph(rng, nodes, edges, "coverage_repair", seeds[attempts % len(seeds)], len(bank) + 1, force_nodes, force_edges)
        if graph.digest in seen:
            continue
        seen.add(graph.digest)
        bank.append(graph)
        node_counts.update(graph.nodes)
        edge_counts.update(edge.candidate_id for edge in graph.edges)
    checkpoints.append({"graph_count": len(bank), "attempt_count": attempts, "last_graph_id": bank[-1].graph_id if bank else ""})
    return bank, checkpoints


def coverage_rows(
    bank: list[ExplorationGraph], nodes: dict[str, float], edges: list[CandidateEdge], target: int
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    node_counts = Counter(node for graph in bank for node in graph.nodes)
    edge_counts = Counter(edge.candidate_id for graph in bank for edge in graph.edges)
    sorted_nodes = sorted(nodes.items(), key=lambda item: (item[1], item[0]))
    sorted_edges = sorted(edges, key=lambda edge: (edge.preference, edge.edge_id))
    node_quartile = {name: min(4, 1 + 4 * index // max(1, len(sorted_nodes))) for index, (name, _) in enumerate(sorted_nodes)}
    edge_quartile = {edge.edge_id: min(4, 1 + 4 * index // max(1, len(sorted_edges))) for index, edge in enumerate(sorted_edges)}
    node_rows = [{
        "node": name, "preference": preference, "confidence_quartile": node_quartile[name],
        "graphs_containing": node_counts[name], "coverage_target": target if node_quartile[name] == 1 else "",
        "target_met": node_counts[name] >= target if node_quartile[name] == 1 else "not_applicable",
    } for name, preference in sorted_nodes]
    edge_rows = [{
        "edge_id": edge.edge_id, "node_a": edge.node_a, "node_b": edge.node_b,
        "preference": edge.preference, "source_score": edge.source_score,
        "confidence_quartile": edge_quartile[edge.edge_id], "graphs_containing": edge_counts[edge.edge_id],
        "coverage_target": target if edge_quartile[edge.edge_id] == 1 else "",
        "target_met": edge_counts[edge.edge_id] >= target if edge_quartile[edge.edge_id] == 1 else "not_applicable",
    } for edge in sorted_edges]
    return node_rows, edge_rows


def _kirby_deltas(bundle: InputBundle) -> tuple[np.ndarray, dict[str, np.ndarray], dict[str, np.ndarray]]:
    rows = [(i, row) for i, row in enumerate(bundle.samples) if "Kirby-Leo" in str(row.get("measurement_layer", ""))]
    times = np.array(sorted({_safe_float(row.get("time_minutes")) for _, row in rows}), dtype=float)
    by_time_block: dict[tuple[float, str], dict[str, int]] = defaultdict(dict)
    for index, row in rows:
        time_value = _safe_float(row.get("time_minutes"))
        block = str(row.get("paired_block_id") or row.get("block_id"))
        by_time_block[(time_value, block)][str(row.get("condition", "")).casefold()] = index
    responses: dict[str, np.ndarray] = {}
    masks: dict[str, np.ndarray] = {}
    for gene, node_id in bundle.representative_sites.items():
        values = bundle.matrix.get(node_id)
        if values is None:
            continue
        array = np.full((len(times), 3), np.nan)
        for ti, time_value in enumerate(times):
            pairs = sorted((block, mapping) for (t, block), mapping in by_time_block.items() if t == time_value)
            for ri, (_, mapping) in enumerate(pairs[:3]):
                if "ddavp" in mapping and "control" in mapping:
                    treated, control = values[mapping["ddavp"]], values[mapping["control"]]
                    if np.isfinite(treated) and np.isfinite(control):
                        array[ti, ri] = treated - control
        responses[gene] = array
        masks[gene] = np.isfinite(array)
    return times, responses, masks


def fit_response_model(
    times: np.ndarray,
    target: np.ndarray,
    parents: list[np.ndarray],
    signs: list[str],
    ridge: float,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Fit one signed linear continuous-time response curve.

    Inputs are time-by-block treatment-minus-control responses.  Means are
    estimated independently at each time; replicate labels are not interpreted
    as longitudinal trajectories. Gradient matching supplies one optimizer
    start; a zero-response start is also tried, and trajectory residuals choose
    the solution.
    """
    counts = np.isfinite(target).sum(axis=1)
    target_mean = np.divide(np.nansum(target, axis=1), counts, out=np.full(target.shape[0], np.nan), where=counts > 0)
    if np.isfinite(target_mean).sum() < 4:
        raise ValueError("target has fewer than four finite time means")
    derivative = np.gradient(target_mean, times)
    columns = [np.ones(len(times)), -target_mean]
    for parent in parents:
        parent_counts = np.isfinite(parent).sum(axis=1)
        columns.append(np.divide(np.nansum(parent, axis=1), parent_counts, out=np.full(parent.shape[0], np.nan), where=parent_counts > 0))
    design = np.column_stack(columns)
    valid = np.isfinite(design).all(axis=1) & np.isfinite(derivative)
    if valid.sum() < design.shape[1]:
        raise ValueError("insufficient finite time means for local ODE parameters")
    penalty = np.eye(design.shape[1]) * ridge
    penalty[0, 0] = 0.0
    beta = np.linalg.solve(design[valid].T @ design[valid] + penalty, design[valid].T @ derivative[valid])
    # The coefficient is a decay rate in b0 - decay*x, so negative values
    # would create an unconstrained explosive self-amplifier.
    beta[1] = max(0.0, beta[1])
    for index, sign in enumerate(signs, start=2):
        if sign == "activation" and beta[index] < 0:
            beta[index] = 0.0
        if sign == "inhibition" and beta[index] > 0:
            beta[index] = 0.0
    parent_means = []
    for parent in parents:
        parent_counts = np.isfinite(parent).sum(axis=1)
        parent_means.append(np.divide(np.nansum(parent, axis=1), parent_counts, out=np.full(parent.shape[0], np.nan), where=parent_counts > 0))
    def simulate(parameters: np.ndarray) -> np.ndarray:
        initial = float(parameters[0])
        coefficients = parameters[1:]
        predicted = np.empty(len(times), dtype=float)
        predicted[0] = initial
        for index in range(1, len(times)):
            previous = index - 1
            interval = times[index] - times[index - 1]
            steps = max(1, int(math.ceil(interval / 0.25)))
            step = interval / steps
            state = predicted[previous]
            for substep in range(steps):
                fraction = substep / steps
                rhs = coefficients[0] - coefficients[1] * state
                rhs += sum(
                    coefficients[j + 2] * ((1 - fraction) * values[previous] + fraction * values[index])
                    for j, values in enumerate(parent_means)
                )
                state = float(np.clip(state + step * rhs, -10.0, 10.0))
            predicted[index] = state
        return predicted

    finite_observation = np.isfinite(target)

    def residual(parameters: np.ndarray) -> np.ndarray:
        predicted = simulate(parameters)
        data_residual = (target - predicted[:, None])[finite_observation]
        regularization = math.sqrt(ridge) * parameters[2:]
        return np.concatenate([data_residual, regularization])

    lower = np.full(len(beta) + 1, -5.0)
    upper = np.full(len(beta) + 1, 5.0)
    lower[2] = 0.0
    for index, sign in enumerate(signs, start=3):
        if sign == "activation":
            lower[index] = 0.0
        elif sign == "inhibition":
            upper[index] = 0.0
    starts = [np.r_[target_mean[0], beta], np.zeros(len(beta) + 1)]
    fits = [least_squares(residual, start, bounds=(lower, upper), max_nfev=300) for start in starts]
    fit = min(fits, key=lambda result: float(np.sum(residual(result.x) ** 2)))
    if not fit.success or not np.isfinite(fit.x).all():
        raise ValueError(f"trajectory optimization failed: {fit.message}")
    beta = fit.x[1:]
    predicted = simulate(fit.x)
    return beta, predicted, float(ridge * np.sum(beta[1:] ** 2))


def _observation_variance(values: np.ndarray) -> float:
    residuals = []
    for row in values:
        finite = row[np.isfinite(row)]
        if finite.size > 1:
            residuals.extend((finite - finite.mean()).tolist())
    return max(float(np.mean(np.square(residuals))) if residuals else 1.0, 0.05)


def _fixed_gaussian_log_likelihood(values: np.ndarray, prediction: np.ndarray, variance: float) -> float:
    residual = values - prediction[:, None]
    finite = residual[np.isfinite(residual)]
    if finite.size == 0:
        return 0.0
    return float(-0.5 * np.sum(np.log(2 * math.pi * variance) + (finite ** 2) / variance))


def common_background_likelihood(responses: dict[str, np.ndarray]) -> tuple[float, dict[str, float], dict[str, float]]:
    """Score every available Kirby response under the same zero-response background."""
    rows: dict[str, float] = {}
    variances: dict[str, float] = {}
    for gene, values in responses.items():
        variance = _observation_variance(values)
        variances[gene] = variance
        rows[gene] = _fixed_gaussian_log_likelihood(values, np.zeros(values.shape[0]), variance)
    return float(sum(rows.values())), rows, variances


def fit_dynamic_graph(
    graph: ExplorationGraph,
    bundle: InputBundle,
    times: np.ndarray,
    responses: dict[str, np.ndarray],
    config: dict[str, object],
    background: tuple[float, dict[str, float], dict[str, float]] | None = None,
) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    ridge = float(config.get("ridge", 1.0))
    max_parents = int(config.get("max_parents_per_target", 1))
    edge_map: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for edge in graph.edges:
        for source, target, sign in edge.arrows():
            if source in responses and target in responses:
                edge_map[target].append((source, sign))
    background_total, background_by_gene, variances = background or common_background_likelihood(responses)
    log_likelihood = background_total
    graph_specific_improvement = 0.0
    regularization = 0.0
    predictive_sse: list[float] = []
    parameter_rows: list[dict[str, object]] = []
    prediction_rows: list[dict[str, object]] = []
    fitted_targets = 0
    parameter_count = 0
    for target in sorted(graph.nodes & responses.keys()):
        parent_info = sorted(edge_map.get(target, []))[:max_parents]
        parents = [responses[source] for source, _ in parent_info]
        signs = [sign for _, sign in parent_info]
        try:
            beta, predicted, reg = fit_response_model(times, responses[target], parents, signs, ridge)
        except (ValueError, np.linalg.LinAlgError):
            continue
        observed = responses[target]
        residuals = observed - predicted[:, None]
        finite = residuals[np.isfinite(residuals)]
        if finite.size < 6:
            continue
        variance = variances[target]
        fitted_ll = _fixed_gaussian_log_likelihood(observed, predicted, variance)
        improvement = fitted_ll - background_by_gene[target]
        log_likelihood += improvement
        graph_specific_improvement += improvement
        regularization -= reg
        fitted_targets += 1
        parameter_count += len(beta)
        for fold in range(observed.shape[1]):
            train = observed.copy()
            train[:, fold] = np.nan
            parent_train = []
            for parent in parents:
                value = parent.copy()
                value[:, fold] = np.nan
                parent_train.append(value)
            try:
                _, fold_prediction, _ = fit_response_model(times, train, parent_train, signs, ridge)
                held = observed[:, fold]
                valid = np.isfinite(held) & np.isfinite(fold_prediction)
                if valid.any():
                    predictive_sse.extend(((held[valid] - fold_prediction[valid]) ** 2).tolist())
            except (ValueError, np.linalg.LinAlgError):
                pass
        parameter_rows.append({
            "graph_id": graph.graph_id, "target": target, "intercept": beta[0], "decay": beta[1],
            "parents": ";".join(source for source, _ in parent_info),
            "parent_signs": ";".join(signs),
            "parent_coefficients": ";".join(f"{value:.9g}" for value in beta[2:]),
            "ridge_penalty": reg,
        })
        for index, time_value in enumerate(times):
            prediction_rows.append({
                "graph_id": graph.graph_id, "target": target, "time_minutes": time_value,
                "observed_mean_ddavp_minus_control": float(np.nanmean(observed[index])),
                "predicted_mean_ddavp_minus_control": predicted[index],
                "finite_matched_blocks": int(np.isfinite(observed[index]).sum()),
                "observed_sd": float(np.nanstd(observed[index], ddof=1)) if np.isfinite(observed[index]).sum() > 1 else math.nan,
                "observation_variance_used": variance,
            })
    if fitted_targets < int(config.get("minimum_fitted_targets", 3)):
        return ({
            "graph_id": graph.graph_id, "dynamic_fit_status": "fit_failed",
            "fit_failure_reason": "fewer than minimum targets had an estimable parsimonious local ODE",
            "data_log_likelihood": math.nan, "parameter_regularization": math.nan,
            "predictive_validation": math.nan, "model_evidence_approximation": math.nan,
            "fitted_target_count": fitted_targets, "parameter_count": parameter_count,
            "common_background_log_likelihood": background_total,
            "graph_specific_log_likelihood_improvement": graph_specific_improvement,
        }, parameter_rows, prediction_rows)
    predictive = -float(np.mean(predictive_sse)) if predictive_sse else math.nan
    aic_like = log_likelihood - parameter_count
    return ({
        "graph_id": graph.graph_id, "dynamic_fit_status": "dynamic_fit",
        "fit_failure_reason": "", "data_log_likelihood": log_likelihood,
        "common_background_log_likelihood": background_total,
        "graph_specific_log_likelihood_improvement": graph_specific_improvement,
        "parameter_regularization": regularization,
        "predictive_validation": predictive,
        "model_evidence_approximation": aic_like,
        "model_evidence_label": "AIC-like optimized-fit approximation; not marginal likelihood",
        "fitted_target_count": fitted_targets, "parameter_count": parameter_count,
        "matched_block_count": 12,
    }, parameter_rows, prediction_rows)


def _select_dynamic_graphs(
    bank: list[ExplorationGraph], scores: dict[str, dict[str, float]], config: dict[str, object]
) -> tuple[list[ExplorationGraph], set[str]]:
    budget = min(int(config.get("budget", 100)), len(bank))
    if budget <= 0:
        return [], set()
    diverse_count = max(int(math.ceil(budget * float(config.get("diverse_fraction", 0.25)))), 1)
    ranked = sorted(bank, key=lambda graph: (-scores[graph.graph_id]["structural_preference"], graph.graph_id))
    selected = ranked[: budget - diverse_count]
    chosen = {graph.graph_id for graph in selected}
    unusual_pool = sorted(
        [graph for graph in bank if graph.graph_id not in chosen],
        key=lambda graph: (
            0 if graph.strategy in {"low_confidence_forced", "feedback_cycle", "coverage_repair"} else 1,
            scores[graph.graph_id]["structural_preference"],
            graph.graph_id,
        ),
    )
    unusual = unusual_pool[:diverse_count]
    selected.extend(unusual)
    return selected, {graph.graph_id for graph in unusual}


def _write_tsv(rows: list[dict[str, object]], path: Path) -> None:
    pd.DataFrame(rows).to_csv(path, sep="\t", index=False)


def structural_sensitivity(
    bank: list[ExplorationGraph],
    nodes: dict[str, float],
    edges: list[CandidateEdge],
    config: dict[str, object],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    variants: dict[str, tuple[dict[str, float], list[CandidateEdge], dict[str, object]]] = {}
    variants["baseline"] = (nodes, edges, dict(config))
    shrink_nodes = {name: 0.5 + 0.5 * (value - 0.5) for name, value in nodes.items()}
    shrink_edges = [CandidateEdge(edge.edge_id, edge.node_a, edge.node_b, 0.5 + 0.5 * (edge.preference - 0.5), edge.source_score) for edge in edges]
    variants["score_shrunk_50pct_toward_neutral"] = (shrink_nodes, shrink_edges, dict(config))
    clipped_nodes = {name: min(0.9, max(0.1, value)) for name, value in nodes.items()}
    clipped_edges = [CandidateEdge(edge.edge_id, edge.node_a, edge.node_b, min(0.9, max(0.1, edge.preference)), edge.source_score) for edge in edges]
    variants["score_clipped_0.10_0.90"] = (clipped_nodes, clipped_edges, dict(config))
    weak = dict(config)
    weak["alpha_node"] = float(config.get("alpha_node", 0.35)) * 0.5
    weak["alpha_edge"] = float(config.get("alpha_edge", 0.65)) * 0.5
    variants["half_node_edge_prior_strength"] = (nodes, edges, weak)
    strong = dict(config)
    strong["alpha_node"] = float(config.get("alpha_node", 0.35)) * 2.0
    strong["alpha_edge"] = float(config.get("alpha_edge", 0.65)) * 2.0
    variants["double_node_edge_prior_strength"] = (nodes, edges, strong)
    rows = []
    for graph in bank:
        row: dict[str, object] = {"graph_id": graph.graph_id}
        for name, (variant_nodes, variant_edges, variant_config) in variants.items():
            row[name] = structural_score(graph, variant_nodes, variant_edges, variant_config)["structural_preference"]
        rows.append(row)
    frame = pd.DataFrame(rows).set_index("graph_id")
    baseline_rank = frame["baseline"].rank(method="average")
    baseline_top = set(frame.nlargest(100, "baseline").index)
    summary = []
    for name in variants:
        variant_rank = frame[name].rank(method="average")
        top = set(frame.nlargest(100, name).index)
        summary.append({
            "variant": name,
            "spearman_rank_correlation_with_baseline": float(baseline_rank.corr(variant_rank, method="pearson")),
            "top_100_overlap_with_baseline": len(top & baseline_top),
            "interpretation": "sensitivity analysis only; not a posterior comparison",
        })
    return rows, summary


def _graph_record(graph: ExplorationGraph) -> dict[str, object]:
    return {
        "graph_id": graph.graph_id, "canonical_sha256": graph.digest, "strategy": graph.strategy,
        "seed": graph.seed, "node_count": len(graph.nodes), "edge_assertion_count": len(graph.edges),
        "directed_arrow_count": sum(len(edge.arrows()) for edge in graph.edges),
        "has_feedback_cycle": _has_cycle(graph), "nodes": sorted(graph.nodes),
        "edges": [edge.__dict__ for edge in graph.edges],
    }


def _render_report(output: Path, manifest: dict[str, object], rankings: list[dict[str, object]]) -> None:
    best_structural = sorted(rankings, key=lambda row: -float(row["structural_preference"]))[:12]
    dynamic = [row for row in rankings if row.get("dynamic_fit_status") == "dynamic_fit"]
    best_dynamic = sorted(dynamic, key=lambda row: -float(row["model_evidence_approximation"]))[:12]
    unusual = [row for row in rankings if row.get("dynamic_allocation") == "deliberately_unusual"][:12]

    def table(rows: list[dict[str, object]], columns: list[str]) -> str:
        head = "".join(f"<th>{html.escape(column)}</th>" for column in columns)
        body = "".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(column, '')))}</td>" for column in columns) + "</tr>" for row in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    columns = ["graph_id", "strategy", "node_count", "edge_assertion_count", "has_feedback_cycle", "structural_preference", "dynamic_fit_status", "data_log_likelihood", "predictive_validation", "model_evidence_approximation"]
    text = f"""<!doctype html><meta charset='utf-8'><title>Diverse signaling graph exploration</title>
<style>body{{font:15px system-ui;max-width:1250px;margin:35px auto;color:#17372f;background:#f3f5f0}}section{{background:white;padding:22px;margin:16px 0;border-radius:14px}}table{{border-collapse:collapse;width:100%;font-size:12px}}th,td{{padding:7px;border-bottom:1px solid #dbe4df;text-align:left}}.warn{{padding:12px;background:#fff4d8;border-left:4px solid #d29a22}}code{{background:#edf2ef;padding:2px 5px}}</style>
<h1>Diverse signaling-graph exploration</h1>
<section><h2>What this run is</h2><p>A reproducible exploration bank of <strong>{manifest['actual_graph_count']:,}</strong> unique typed graphs drawn from a declared feasible universe of {manifest['feasible_node_count']} proteins and {manifest['feasible_edge_count']} candidate interactions. Bank frequencies are coverage statistics, <strong>not posterior probabilities</strong>.</p><div class='warn'>Direction, sign, and mechanism were not supplied by the V1 registry. They are explored as explicit alternatives. Structural preference is not kinetic evidence; dynamic scores use only within-Kirby dDAVP-minus-control responses.</div></section>
<section><h2>Experimental design</h2><p>Dynamic fitting uses 24 Kirby–Leo measurements as 12 time-matched treatment/control blocks at 1, 2, 5, and 15 minutes. Replicate labels across times are not treated as trajectories. Mouse control layers remain outside the dynamic likelihood.</p></section>
<section><h2>Coverage and sensitivity</h2><p>Bottom-quartile node targets met: {manifest['bottom_node_targets_met']}/{manifest['bottom_node_target_count']}. Bottom-quartile edge targets met: {manifest['bottom_edge_targets_met']}/{manifest['bottom_edge_target_count']}. Required appearances per feasible low-confidence candidate: {manifest['low_confidence_coverage_target']}.</p><p><a href='exploration_coverage_nodes.tsv'>Node coverage</a> · <a href='exploration_coverage_edges.tsv'>Edge coverage</a> · <a href='exploration_diversity.tsv'>Diversity</a> · <a href='structural_sensitivity_summary.tsv'>Prior/score sensitivity</a> · <a href='graph_bank.jsonl.gz'>Complete graph bank</a></p></section>
<section><h2>Best structural preference</h2>{table(best_structural, columns)}</section>
<section><h2>Best dynamic-fit approximation</h2>{table(best_dynamic, columns)}<p>The last column is an AIC-like optimized-fit approximation, not a marginal likelihood or posterior.</p></section>
<section><h2>Deliberately unusual fitted alternatives</h2>{table(unusual, columns)}</section>
<section><h2>Interpretation</h2><p>Supplied preferences determine <code>structural_preference</code>. Kirby measurements determine <code>data_log_likelihood</code> and held-block <code>predictive_validation</code>. The local continuous-time signed ridge model is intentionally parsimonious because four sampling times cannot identify unrestricted nonlinear kinetics.</p></section>"""
    (output / "exploration_report.html").write_text(text, encoding="utf-8")


def run_exploration(config: dict[str, object], output: str | Path) -> dict[str, object]:
    started = time.time()
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle = load_inputs(config)
    exploration = dict(config.get("exploration", {}))
    dynamic_config = dict(config.get("dynamic_fit", {}))
    nodes, edges, universe_audit = build_feasible_universe(bundle, exploration)
    bank, checkpoints = generate_bank(nodes, edges, exploration)
    scores = {graph.graph_id: structural_score(graph, nodes, edges, exploration) for graph in bank}
    coverage_target = int(exploration.get("low_confidence_coverage_target", 20))
    node_coverage, edge_coverage = coverage_rows(bank, nodes, edges, coverage_target)

    times, responses, _ = _kirby_deltas(bundle)
    background = common_background_likelihood(responses)
    selected_dynamic, unusual_ids = _select_dynamic_graphs(bank, scores, dynamic_config)
    dynamic_rows: dict[str, dict[str, object]] = {}
    parameter_rows: list[dict[str, object]] = []
    prediction_rows: list[dict[str, object]] = []
    for graph in selected_dynamic:
        row, params, predictions = fit_dynamic_graph(graph, bundle, times, responses, dynamic_config, background)
        dynamic_rows[graph.graph_id] = row
        parameter_rows.extend(params)
        prediction_rows.extend(predictions)

    rankings: list[dict[str, object]] = []
    for graph in bank:
        typed = Counter((edge.direction, edge.sign, edge.mechanism) for edge in graph.edges)
        row: dict[str, object] = {
            "graph_id": graph.graph_id, "canonical_sha256": graph.digest, "strategy": graph.strategy,
            "seed": graph.seed, "node_count": len(graph.nodes), "edge_assertion_count": len(graph.edges),
            "directed_arrow_count": sum(len(edge.arrows()) for edge in graph.edges), "has_feedback_cycle": _has_cycle(graph),
            "measured_node_count": sum(node in bundle.representative_sites for node in graph.nodes),
            "unmeasured_intermediate_count": sum(node not in bundle.representative_sites for node in graph.nodes),
            "reciprocal_assertion_count": sum(edge.direction == "both" for edge in graph.edges),
            "unknown_direction_count": sum(edge.direction == "unknown" for edge in graph.edges),
            "unknown_sign_count": sum(edge.sign == "unknown" for edge in graph.edges),
            "unknown_mechanism_count": sum(edge.mechanism == "unknown" for edge in graph.edges),
            **scores[graph.graph_id],
            "scoring_status": "structure_only",
        }
        if graph.graph_id in dynamic_rows:
            row.update(dynamic_rows[graph.graph_id])
            row["scoring_status"] = row["dynamic_fit_status"]
            row["dynamic_allocation"] = "deliberately_unusual" if graph.graph_id in unusual_ids else "top_structural"
        else:
            row.update({
                "dynamic_fit_status": "not_allocated", "fit_failure_reason": "outside configured dynamic-fitting budget",
                "data_log_likelihood": math.nan, "parameter_regularization": math.nan,
                "predictive_validation": math.nan, "model_evidence_approximation": math.nan,
                "common_background_log_likelihood": background[0],
                "graph_specific_log_likelihood_improvement": math.nan,
                "dynamic_allocation": "not_allocated",
            })
        rankings.append(row)

    with gzip.open(output / "graph_bank.jsonl.gz", "wt", encoding="utf-8") as handle:
        for graph in bank:
            handle.write(json.dumps(_graph_record(graph), separators=(",", ":")) + "\n")
    _write_tsv(rankings, output / "graph_rankings.tsv")
    _write_tsv(node_coverage, output / "exploration_coverage_nodes.tsv")
    _write_tsv(edge_coverage, output / "exploration_coverage_edges.tsv")
    _write_tsv(parameter_rows, output / "dynamic_fit_parameters.tsv")
    _write_tsv(prediction_rows, output / "dynamic_fit_predictions.tsv")
    _write_tsv(
        [row for row in dynamic_rows.values() if row.get("dynamic_fit_status") == "fit_failed"],
        output / "dynamic_fit_failures.tsv",
    )
    _write_tsv(checkpoints, output / "exploration_checkpoints.tsv")
    diversity = []
    for strategy, graphs in __import__("itertools").groupby(sorted(bank, key=lambda graph: graph.strategy), key=lambda graph: graph.strategy):
        group = list(graphs)
        diversity.append({
            "strategy": strategy, "graph_count": len(group),
            "mean_nodes": float(np.mean([len(graph.nodes) for graph in group])),
            "mean_edge_assertions": float(np.mean([len(graph.edges) for graph in group])),
            "cycle_fraction": float(np.mean([_has_cycle(graph) for graph in group])),
            "mean_reciprocal_assertions": float(np.mean([sum(edge.direction == "both" for edge in graph.edges) for graph in group])),
        })
    _write_tsv(diversity, output / "exploration_diversity.tsv")
    sensitivity_rows, sensitivity_summary = structural_sensitivity(bank, nodes, edges, exploration)
    _write_tsv(sensitivity_rows, output / "structural_sensitivity.tsv")
    _write_tsv(sensitivity_summary, output / "structural_sensitivity_summary.tsv")
    representative_ids = []
    representative_ids.extend(row["graph_id"] for row in sorted(rankings, key=lambda row: -float(row["structural_preference"]))[:10])
    dynamic_success = [row for row in rankings if row.get("dynamic_fit_status") == "dynamic_fit"]
    representative_ids.extend(row["graph_id"] for row in sorted(dynamic_success, key=lambda row: -float(row["model_evidence_approximation"]))[:10])
    representative_ids.extend(row["graph_id"] for row in rankings if row.get("dynamic_allocation") == "deliberately_unusual")
    representative_set = set(representative_ids)
    (output / "representative_graphs.json").write_text(
        json.dumps([_graph_record(graph) for graph in bank if graph.graph_id in representative_set], indent=2), encoding="utf-8"
    )

    resolved = {**config, "exploration": exploration, "dynamic_fit": dynamic_config}
    (output / "resolved_configuration.json").write_text(json.dumps(resolved, indent=2), encoding="utf-8")
    inputs = {
        "source_v1_run": str(bundle.source_run), "pooled_workbook": str(Path(str(config["pooled_workbook"])).resolve()),
        "source_node_file": str(bundle.source_run / "node_posteriors.tsv.gz"),
        "source_edge_file": str(bundle.source_run / "supported_edges.tsv.gz"),
        "source_node_sha256": sha256_file(bundle.source_run / "node_posteriors.tsv.gz"),
        "source_edge_sha256": sha256_file(bundle.source_run / "supported_edges.tsv.gz"),
        "pooled_workbook_sha256": sha256_file(Path(str(config["pooled_workbook"])).resolve()),
        "species_and_layers": sorted({f"{row.get('species')}|{row.get('measurement_layer')}" for row in bundle.samples}),
        "preprocessing": bundle.normalization, **universe_audit,
        "node_source_score_range": [min(nodes.values()), max(nodes.values())],
        "edge_source_score_range": [min(edge.source_score for edge in edges), max(edge.source_score for edge in edges)],
    }
    (output / "input_provenance_manifest.json").write_text(json.dumps(inputs, indent=2), encoding="utf-8")
    low_nodes = [row for row in node_coverage if row["confidence_quartile"] == 1]
    low_edges = [row for row in edge_coverage if row["confidence_quartile"] == 1]
    manifest = {
        "schema_version": 1, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "status": "complete",
        "target_graph_count": int(exploration.get("target_graph_count", 10_000)), "actual_graph_count": len(bank),
        "feasible_node_count": len(nodes), "feasible_edge_count": len(edges),
        "low_confidence_coverage_target": coverage_target,
        "bottom_node_target_count": len(low_nodes), "bottom_node_targets_met": sum(bool(row["target_met"]) for row in low_nodes),
        "bottom_edge_target_count": len(low_edges), "bottom_edge_targets_met": sum(bool(row["target_met"]) for row in low_edges),
        "dynamic_fit_budget": int(dynamic_config.get("budget", 100)), "dynamic_fit_attempted": len(selected_dynamic),
        "dynamic_fit_succeeded": sum(row.get("dynamic_fit_status") == "dynamic_fit" for row in dynamic_rows.values()),
        "dynamic_fit_failed": sum(row.get("dynamic_fit_status") == "fit_failed" for row in dynamic_rows.values()),
        "unusual_dynamic_allocation": len(unusual_ids), "unusual_dynamic_fraction": len(unusual_ids) / max(1, len(selected_dynamic)),
        "elapsed_seconds": time.time() - started,
        "score_semantics": {
            "structural_preference": "joint soft preference conditioned on the declared feasible universe and graph validity",
            "data_log_likelihood": "optimized Gaussian observation likelihood for within-Kirby matched-block responses",
            "parameter_regularization": "negative signed-ridge penalty",
            "predictive_validation": "negative balanced held-block mean squared error",
            "model_evidence_approximation": "AIC-like optimized-fit score; not marginal likelihood or posterior",
        },
        "warnings": [
            "Exploration frequencies are coverage statistics, not posterior probabilities.",
            "The V1 registry does not calibrate direction, sign, or mechanism; those are explicit exploration alternatives.",
            "Dynamic fitting is a parsimonious signed linear continuous-time surrogate initialized from gradient matching and optimized on trajectories; it is not unrestricted biochemical kinetic identification.",
            "Each local dynamic fit tries both a gradient-matched and a zero-response starting condition.",
            "Only within-Kirby dDAVP-control differences enter the dynamic likelihood; mouse layers remain separate.",
            "Standardized measurements are dimensionless readouts, not concentrations.",
        ],
    }
    _render_report(output, manifest, rankings)
    manifest["outputs"] = sorted(path.name for path in output.iterdir() if path.is_file() and path.name != "exploration_manifest.json")
    manifest["outputs"].append("exploration_manifest.json")
    (output / "exploration_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest

