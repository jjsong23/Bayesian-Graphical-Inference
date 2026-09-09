#!/usr/bin/env python3
"""Read-only, per-hypothesis evidence inspection for completed GUI runs."""

from __future__ import annotations

import difflib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from workflow_engine import (
    FACTOR_EPSILON,
    PROJECT_ROOT,
    UNIVERSE_RELATIVE,
    _canonical_undirected_pair,
    _raw_seed_edge_factor_table,
    _stream_multiplier,
    edge_stream_eligibility_matrix,
    load_registry,
    stable_expit,
)
from incremental_edge_cache import incremental_pair_factor_table


LOG_2 = math.log(2.0)
STATUS_TOLERANCE = 1e-12


def _read_configuration(run_directory: Path) -> dict[str, Any]:
    path = run_directory / "configuration.json"
    if not path.is_file():
        raise FileNotFoundError("The completed run has no configuration.json file")
    return json.loads(path.read_text(encoding="utf-8"))


def _clean(value: object) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if pd.isna(value):
        return None
    text = str(value)
    return text if text.strip() else None


def _bool(value: object) -> bool:
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    return str(value).strip().casefold() in {"true", "1", "yes"}


def _status(enabled: bool, bayes_factor: float | None) -> str:
    if not enabled:
        return "disabled"
    if bayes_factor is None:
        return "neutral"
    if bayes_factor > 1.0 + STATUS_TOLERANCE:
        return "supports"
    if bayes_factor < 1.0 - STATUS_TOLERANCE:
        return "refutes"
    return "neutral"


def _run_stream_state(
    config: dict[str, Any],
    group: str,
    definition: dict[str, Any],
) -> dict[str, Any]:
    """Return historical run state; streams added later are explicitly disabled."""
    recorded = config.get(group, {}).get(definition["id"])
    if recorded is not None:
        return recorded
    state: dict[str, Any] = {
        "enabled": False,
        "weight": float(definition.get("default_weight", 1.0)),
        "parameters": {
            parameter["id"]: float(parameter["default"])
            for parameter in definition.get("parameters", [])
        },
    }
    if definition.get("normalization", {}).get("user_control", True):
        state["tq_multiplier"] = float(
            definition.get("normalization", {}).get("default_multiplier", 1.0)
        )
    return state


def _posterior_from_log_odds(log_odds: float) -> float:
    return float(stable_expit(np.asarray([log_odds], dtype=float))[0])


def _resolve_symbol(query: str, available: list[str], label: str) -> str:
    stripped = str(query).strip()
    if not stripped:
        raise ValueError(f"{label} is required")
    exact = {symbol.casefold(): symbol for symbol in available}
    resolved = exact.get(stripped.casefold())
    if resolved is not None:
        return resolved
    suggestions = difflib.get_close_matches(stripped, available, n=5, cutoff=0.45)
    suffix = f" Closest symbols: {', '.join(suggestions)}." if suggestions else ""
    raise ValueError(f"{label} {stripped!r} is not present in this completed run.{suffix}")


def _node_raw_value(row: pd.Series, definition: dict[str, Any]) -> tuple[Any, str | None]:
    candidates: list[tuple[str, str]] = []
    raw_column = definition.get("raw_value_column")
    if raw_column:
        candidates.append((str(raw_column), str(raw_column)))
    handler = definition.get("normalization", {}).get("handler")
    candidates.extend(
        {
            "kinase_activity": [("kinase_absolute_lfc", "absolute LFC")],
            "phosphoprotein_response": [
                ("maximum_absolute_site_lfc", "maximum absolute site LFC")
            ],
        }.get(str(handler), [])
    )
    for column, label in candidates:
        if column in row.index:
            value = _clean(row[column])
            if value is not None:
                return value, label
    return None, None


def inspect_node_evidence(
    run_directory: Path | str,
    symbol_query: str,
    *,
    project_root: Path | str = PROJECT_ROOT,
) -> dict[str, Any]:
    """Return the exact node-update ledger recorded in a completed run."""
    run = Path(run_directory).resolve()
    project = Path(project_root).resolve()
    factors_path = run / "node_posteriors.tsv.gz"
    if not factors_path.is_file():
        raise FileNotFoundError("The completed run has no node_posteriors.tsv.gz file")
    factors = pd.read_csv(factors_path, sep="\t")
    available = factors["gene_symbol"].astype(str).tolist()
    symbol = _resolve_symbol(symbol_query, available, "Node")
    row = factors.loc[factors["gene_symbol"].astype(str).eq(symbol)].iloc[0]
    config = _read_configuration(run)
    registry = load_registry(project)
    definitions = {item["id"]: item for item in registry["node_streams"]}
    prior = float(config["node_integration"]["prior_probability"])
    prior_log_odds = math.log(prior / (1.0 - prior))
    reconstructed_log_odds = prior_log_odds
    streams: list[dict[str, Any]] = []
    for stream_id, definition in definitions.items():
        state = _run_stream_state(config, "node_streams", definition)
        enabled = bool(state["enabled"] and float(state["weight"]) > 0)
        prefix = f"gui_{stream_id}_"
        bayes_factor = _clean(row.get(prefix + "bayes_factor")) if enabled else None
        source_factor = _clean(row.get(prefix + "factor")) if enabled else None
        source_bayes_factor = (
            _clean(row.get(prefix + "source_bayes_factor")) if enabled else None
        )
        weighted_log = (
            _clean(row.get(prefix + "weighted_log_bayes_factor")) if enabled else None
        )
        weighted_log = float(weighted_log or 0.0)
        if enabled:
            reconstructed_log_odds += weighted_log
        eligible = _bool(row.get(prefix + "negative_evidence_eligible", False)) if enabled else None
        observed = _bool(row.get(prefix + "observed", False)) if enabled else None
        penalty = _bool(row.get(prefix + "unobserved_penalty_applied", False)) if enabled else False
        continuous = _bool(
            row.get(prefix + "continuous_negative_evidence_applied", False)
        ) if enabled else False
        raw_value, raw_label = _node_raw_value(row, definition)
        if not enabled:
            note = "Disabled in this run; contribution to posterior odds was zero."
        elif eligible is False:
            note = "Outside this stream's negative-evidence scope; neutral if no positive record."
        elif penalty:
            note = "Eligible nondetection received the configured fixed negative Bayes factor."
        elif continuous:
            note = "Weak/zero evidence was evaluated continuously and produced BF below 1."
        elif observed:
            note = "A source measurement mapped to this node."
        else:
            note = "No retained source measurement; this stream remained neutral."
        streams.append(
            {
                "stream_id": stream_id,
                "label": definition["label"],
                "description": definition.get("description"),
                "enabled": enabled,
                "status": _status(enabled, float(bayes_factor) if bayes_factor is not None else None),
                "weight": float(state["weight"]),
                "tq_multiplier": _clean(state.get("tq_multiplier")),
                "normalization_control": definition.get("normalization", {}).get("control_label"),
                "normalization_reference": definition.get("normalization", {}).get("reference"),
                "dependence_group": definition.get("dependence_group"),
                "raw_value": raw_value,
                "raw_value_label": raw_label,
                "source_factor": source_factor,
                "source_bayes_factor_before_absence_rule": source_bayes_factor,
                "applied_bayes_factor": bayes_factor,
                "weighted_log2_odds_contribution": weighted_log / LOG_2,
                "negative_evidence_eligible": eligible,
                "observed": observed,
                "fixed_absence_penalty_applied": penalty,
                "continuous_negative_evidence_applied": continuous,
                "note": note,
            }
        )
    stored_posterior = float(row["gui_posterior"])
    reconstructed = _posterior_from_log_odds(reconstructed_log_odds)
    return {
        "kind": "node",
        "symbol": symbol,
        "name": _clean(row.get("node_name")),
        "classes": _clean(row.get("node_classes")),
        "prior_probability": prior,
        "stored_posterior_probability": stored_posterior,
        "reconstructed_posterior_probability": reconstructed,
        "reconciliation_absolute_difference": abs(stored_posterior - reconstructed),
        "rank": int(row["gui_rank"]),
        "selected_in_graph": _bool(row["gui_selected_in_graph"]),
        "output_probability_cutoff_exclusive": float(
            config["node_integration"]["output_probability_cutoff"]
        ),
        "equation": "posterior log-odds = prior log-odds + sum(weight x ln(BF))",
        "streams": streams,
    }


def _matrix_symbols_and_probability(
    matrix_path: Path,
    left_query: str,
    right_query: str,
) -> tuple[str, str, list[str], float]:
    header = pd.read_csv(matrix_path, sep="\t", nrows=0).columns.astype(str).tolist()
    if len(header) < 2:
        raise ValueError("The stored edge matrix has no graph columns")
    index_column = header[0]
    available = header[1:]
    left = _resolve_symbol(left_query, available, "First edge node")
    right = _resolve_symbol(right_query, available, "Second edge node")
    if left == right:
        raise ValueError("An edge requires two different nodes")
    selected = pd.read_csv(matrix_path, sep="\t", usecols=[index_column, right])
    selected[index_column] = selected[index_column].astype(str)
    match = selected.loc[selected[index_column].eq(left), right]
    if match.empty:
        raise ValueError(f"Stored edge matrix has no row for {left!r}")
    return left, right, available, float(match.iloc[0])


def _graph_metadata(
    project: Path,
    run: Path,
    graph_symbols: list[str],
) -> pd.DataFrame:
    selected = pd.read_csv(run / "selected_nodes.tsv", sep="\t", dtype=str).fillna("")
    metadata = selected.copy()
    missing = set(graph_symbols).difference(metadata["symbol"].astype(str))
    if missing:
        universe = pd.read_csv(project / UNIVERSE_RELATIVE, sep="\t", dtype=str).fillna("")
        universe_rows = universe.loc[universe["symbol"].astype(str).isin(missing)].copy()
        metadata = pd.concat([metadata, universe_rows], ignore_index=True, sort=False).fillna("")
        missing.difference_update(universe_rows["symbol"].astype(str))
    if missing:
        factors = pd.read_csv(
            run / "node_posteriors.tsv.gz",
            sep="\t",
            usecols=["gene_symbol", "node_name", "node_classes"],
        )
        factor_rows = factors.loc[factors["gene_symbol"].astype(str).isin(missing)]
        appended = pd.DataFrame(
            {
                "symbol": factor_rows["gene_symbol"].astype(str),
                "name": factor_rows["node_name"].fillna("").astype(str),
                "classes": factor_rows["node_classes"].fillna("").astype(str),
                "node_type": "protein",
            }
        )
        metadata = pd.concat([metadata, appended], ignore_index=True, sort=False).fillna("")
        missing.difference_update(appended["symbol"].astype(str))
    if missing:
        appended = pd.DataFrame(
            {
                "symbol": sorted(missing),
                "name": "",
                "classes": "external_target",
                "node_type": "protein",
            }
        )
        metadata = pd.concat([metadata, appended], ignore_index=True, sort=False).fillna("")
    return metadata.drop_duplicates("symbol", keep="last")


def _factor_for_pair(
    project: Path,
    definition: dict[str, Any],
    state: dict[str, Any],
    pair: tuple[str, str],
    seed_set: set[str],
    *,
    continuous_negative: bool,
    continuous_floor: float,
) -> tuple[float, bool]:
    multiplier = _stream_multiplier(state)
    if pair[0] in seed_set and pair[1] in seed_set:
        table = _raw_seed_edge_factor_table(
            project,
            definition,
            list(pair),
            multiplier,
            continuous_negative=continuous_negative,
            minimum_bayes_factor=continuous_floor,
        )
    else:
        table = incremental_pair_factor_table(
            project,
            definition["normalization"]["handler"],
            multiplier,
            [pair],
            continuous_negative=continuous_negative,
            minimum_bayes_factor=continuous_floor,
        )
    factor = 1.0
    found = False
    for row in table.itertuples(index=False):
        if _canonical_undirected_pair(row.node_a, row.node_b) == pair:
            factor *= float(row.bayes_factor)
            found = True
    return factor, found


def _derived_factor(run: Path, stream_id: str, pair: tuple[str, str]) -> tuple[float, bool]:
    audit_path = run / f"{stream_id}_audit.tsv.gz"
    if not audit_path.is_file():
        return 1.0, False
    audit = pd.read_csv(audit_path, sep="\t")
    if audit.empty or not {"node_a", "node_b", "bayes_factor"}.issubset(audit.columns):
        return 1.0, False
    keys = [
        _canonical_undirected_pair(left, right)
        for left, right in audit[["node_a", "node_b"]].itertuples(index=False, name=None)
    ]
    matching = audit.loc[[key == pair for key in keys], "bayes_factor"]
    return (float(matching.prod()), True) if len(matching) else (1.0, False)


def inspect_edge_evidence(
    run_directory: Path | str,
    node_a_query: str,
    node_b_query: str,
    *,
    project_root: Path | str = PROJECT_ROOT,
) -> dict[str, Any]:
    """Rescore one stored graph pair and return its per-stream update ledger."""
    run = Path(run_directory).resolve()
    project = Path(project_root).resolve()
    matrix_path = run / "edge_adjacency_matrix.tsv"
    if not matrix_path.is_file():
        raise FileNotFoundError("The completed run has no edge_adjacency_matrix.tsv file")
    left, right, graph_symbols, stored_probability = _matrix_symbols_and_probability(
        matrix_path, node_a_query, node_b_query
    )
    pair = _canonical_undirected_pair(left, right)
    config = _read_configuration(run)
    registry = load_registry(project)
    definitions = {item["id"]: item for item in registry["edge_streams"]}
    metadata = _graph_metadata(project, run, graph_symbols)
    pair_metadata = metadata.loc[metadata["symbol"].astype(str).isin(pair)].copy()
    seed_set = set(
        pd.read_csv(project / UNIVERSE_RELATIVE, sep="\t", usecols=["symbol"])[
            "symbol"
        ].astype(str)
    )
    prior = float(config["edge_integration"]["prior_probability"])
    reconstructed_log_odds = math.log(prior / (1.0 - prior))
    global_continuous = bool(
        config["edge_integration"].get("continuous_negative_evidence", False)
    )
    continuous_floor = float(
        config["edge_integration"].get("continuous_bayes_factor_floor", 1e-6)
    )
    penalize_unsupported = bool(
        config["edge_integration"].get("penalize_unsupported", False)
    )
    unsupported_factor = float(
        config["edge_integration"].get("unsupported_bayes_factor", 0.5)
    )
    streams: list[dict[str, Any]] = []
    for stream_id, definition in definitions.items():
        state = _run_stream_state(config, "edge_streams", definition)
        enabled = bool(state["enabled"] and float(state["weight"]) > 0)
        factor: float | None = None
        found = False
        eligible: bool | None = None
        penalty = False
        continuous_negative = False
        if enabled and definition.get("derived"):
            factor, found = _derived_factor(run, stream_id, pair)
            eligible = found
        elif enabled:
            continuous_negative = global_continuous or bool(
                state.get("continuous_negative_evidence", False)
            )
            factor, found = _factor_for_pair(
                project,
                definition,
                state,
                pair,
                seed_set,
                continuous_negative=continuous_negative,
                continuous_floor=continuous_floor,
            )
            eligibility = edge_stream_eligibility_matrix(
                project,
                definition,
                list(pair),
                graph_metadata=pair_metadata,
            )
            eligible = bool(eligibility[0, 1])
            if not found and eligible and definition.get("negative_evidence"):
                if continuous_negative:
                    factor = continuous_floor
                    penalty = True
                elif penalize_unsupported:
                    factor = unsupported_factor
                    penalty = True
        weighted_log = 0.0
        if enabled:
            factor = float(factor if factor is not None else 1.0)
            weighted_log = float(state["weight"]) * math.log(factor)
            reconstructed_log_odds += weighted_log
        if not enabled:
            note = "Disabled in this run; contribution to posterior odds was zero."
        elif definition.get("derived") and found:
            note = "Derived scaffold-closure rule supplied a factor for this pair."
        elif definition.get("derived"):
            note = "Derived closure was enabled but its shared-scaffold rule did not trigger."
        elif penalty and continuous_negative:
            note = "Eligible pair had no retained record and received the continuous x=0 BF floor."
        elif penalty:
            note = "Eligible pair had no retained record and received the configured unsupported-pair BF."
        elif found:
            note = "A quantitative/curated source record supplied this Bayes factor."
        elif eligible is False:
            note = "Pair was outside this source's assay/mapping scope and remained neutral."
        else:
            note = "No non-neutral factor record; pair remained neutral in this stream."
        streams.append(
            {
                "stream_id": stream_id,
                "label": definition["label"],
                "description": definition.get("description"),
                "enabled": enabled,
                "status": _status(enabled, factor),
                "weight": float(state["weight"]),
                "tq_multiplier": _clean(state.get("tq_multiplier")),
                "normalization_control": definition.get("normalization", {}).get("control_label"),
                "normalization_reference": definition.get("normalization", {}).get("reference"),
                "applied_bayes_factor": factor,
                "weighted_log2_odds_contribution": weighted_log / LOG_2,
                "source_record_retained": found if enabled else None,
                "negative_evidence_eligible": eligible,
                "absence_penalty_applied": penalty,
                "continuous_negative_evidence": continuous_negative if enabled else None,
                "derived": bool(definition.get("derived")),
                "note": note,
            }
        )
    reconstructed = _posterior_from_log_odds(reconstructed_log_odds)
    cutoff = float(config["edge_integration"]["output_probability_cutoff"])
    return {
        "kind": "edge",
        "node_a": left,
        "node_b": right,
        "canonical_undirected_pair": list(pair),
        "prior_probability": prior,
        "stored_posterior_probability": stored_probability,
        "reconstructed_posterior_probability": reconstructed,
        "reconciliation_absolute_difference": abs(stored_probability - reconstructed),
        "supported_above_output_cutoff": stored_probability > cutoff,
        "output_probability_cutoff_exclusive": cutoff,
        "equation": "posterior log-odds = prior log-odds + sum(weight x ln(BF))",
        "streams": streams,
    }
