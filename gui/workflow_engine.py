#!/usr/bin/env python3
"""Configurable end-to-end Bayesian signaling workflow used by the GUI."""

from __future__ import annotations

import gzip
import json
import math
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np
import pandas as pd


GUI_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = GUI_DIR.parent
CODE_DIR = PROJECT_ROOT / "code"
PATH_CODE_DIR = CODE_DIR / "path_finding"
EDGE_CODE_DIR = CODE_DIR / "edge_characterization"
for search_path in (CODE_DIR, PATH_CODE_DIR, EDGE_CODE_DIR):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from build_target_adjacency_vector import (  # noqa: E402
    build_target_adjacency_vector,
    resolve_target,
    safe_name,
)
from find_ranked_paths import (  # noqa: E402
    DEFAULT_SIGNAL_RELAY_CLASSES,
    build_adjacency,
    build_intermediate_eligibility,
    cached_target_extension,
    connected_component_size,
    k_shortest_simple_paths,
    resolve_existing_node,
    serialize_paths,
)
from incremental_edge_cache import (  # noqa: E402
    ensure_incremental_pairs,
    incremental_factor_table,
)


ProgressCallback = Callable[[str, float], None]
NODE_FACTORS_RELATIVE = Path(
    "results/combined_kinase_phosphoprotein_evidence/combined_all_nodes.tsv"
)
UNIVERSE_RELATIVE = Path("data/node_selection/node_universe_combined_nonzero.tsv")
UNIPROT_RELATIVE = Path(
    "data/edge_characterization/kinase_predictor/"
    "phosphosite_database/raw/uniprot_mouse_reference_proteome.tsv.gz"
)
RUNS_RELATIVE = Path("results/gui_runs")
PROTEIN_RAW_RELATIVE = Path("results/mpkccd_protein_abundance_bayes_factors.tsv")
PC_RAW_RELATIVE = Path("results/pc_median_tpm_bayes_factors.tsv")
PHOSPHOPROTEIN_RAW_RELATIVE = Path(
    "results/phosphoprotein_evidence/node_selection_protein_pc_phosphosite_posterior.tsv"
)
MPKCCD_PROFILES_RELATIVE = Path(
    "data/edge_characterization/localization/processed/node_localization_profiles.tsv"
)
KINASE_PREDICTIONS_RELATIVE = Path(
    "results/edge_characterization/localization_kinase_predictor/"
    "observed_phosphosite_top10_predictions.tsv.gz"
)
STRING_SCORE_MATRIX_RELATIVE = Path(
    "results/edge_characterization/localization_kinase_predictor_string/"
    "string_combined_score_matrix.tsv"
)
HPA_PROFILES_RELATIVE = Path(
    "data/edge_characterization/localization/hpa/v25.1/processed/"
    "node_hpa_localization_profiles.tsv"
)
OMNIPATH_EFFORT_MATRIX_RELATIVE = Path(
    "results/edge_characterization/localization_kinase_predictor_string_hpa_omnipath/"
    "omnipath_curation_effort_matrix.tsv"
)
STITCH_UNIVERSE_EDGES_RELATIVE = Path(
    "results/edge_characterization/"
    "localization_kinase_predictor_string_hpa_omnipath_stitch/"
    "stitch_secondary_messenger_edges.tsv"
)
STITCH_ALL_MOUSE_EDGES_RELATIVE = Path(
    "data/edge_characterization/stitch/v5.0/processed/"
    "stitch_secondary_messenger_all_mouse_edges.tsv.gz"
)
STRING_REFERENCE_SCORE = 0.041
STITCH_REFERENCE_SCORE = 0.150
NEUTRAL_LIKELIHOOD = 0.5
FACTOR_EPSILON = 1e-12


@dataclass
class WorkflowResult:
    run_id: str
    output_directory: Path
    summary: dict[str, Any]
    preview: dict[str, Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_registry(project_root: Path | str = PROJECT_ROOT) -> dict[str, Any]:
    project = Path(project_root).resolve()
    registry_path = project / "gui/evidence_registry.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    validate_registry(registry, project)
    return registry


def validate_registry(registry: dict[str, Any], project: Path) -> None:
    if registry.get("schema_version") != 1:
        raise ValueError("unsupported evidence-registry schema")
    for group_name in ("node_streams", "edge_streams"):
        streams = registry.get(group_name, [])
        ids = [stream.get("id") for stream in streams]
        if not streams or None in ids or len(ids) != len(set(ids)):
            raise ValueError(f"invalid or duplicate IDs in {group_name}")
    for stream in registry["edge_streams"]:
        if stream.get("derived"):
            continue
        factor_path = project / stream["factor_file"]
        if not factor_path.exists():
            raise FileNotFoundError(f"edge factor table is missing: {factor_path}")
    ontology_classes = registry.get("path_ontology_classes", [])
    ontology_ids = [item.get("id") for item in ontology_classes]
    if (
        not ontology_classes
        or None in ontology_ids
        or len(ontology_ids) != len(set(ontology_ids))
    ):
        raise ValueError("invalid or duplicate IDs in path_ontology_classes")
    missing_defaults = set(DEFAULT_SIGNAL_RELAY_CLASSES).difference(ontology_ids)
    if missing_defaults:
        raise ValueError(
            "path ontology registry is missing default relay classes: "
            + ", ".join(sorted(missing_defaults))
        )
    enabled_by_default = {
        item["id"] for item in ontology_classes if item.get("default_enabled")
    }
    if enabled_by_default != set(DEFAULT_SIGNAL_RELAY_CLASSES):
        raise ValueError(
            "default-enabled path ontology classes must match the audited "
            "DEFAULT_SIGNAL_RELAY_CLASSES policy"
        )


def default_configuration(registry: dict[str, Any]) -> dict[str, Any]:
    defaults = registry["defaults"]
    return {
        "node_streams": {
            stream["id"]: {
                "enabled": bool(stream["default_enabled"]),
                "weight": float(stream["default_weight"]),
                **(
                    {
                        "tq_multiplier": float(
                            stream.get("normalization", {}).get(
                                "default_multiplier", 1.0
                            )
                        )
                    }
                    if stream.get("normalization", {}).get("user_control", True)
                    else {}
                ),
            }
            for stream in registry["node_streams"]
        },
        "node_integration": {
            "include_second_messengers": bool(defaults["include_second_messengers"]),
            "non_neutral_tolerance": float(defaults["node_non_neutral_tolerance"]),
        },
        "edge_streams": {
            stream["id"]: {
                "enabled": bool(stream["default_enabled"]),
                "weight": float(stream["default_weight"]),
                **(
                    {
                        "tq_multiplier": float(
                            stream.get("normalization", {}).get(
                                "default_multiplier", 1.0
                            )
                        )
                    }
                    if stream.get("normalization", {}).get("user_control", True)
                    else {}
                ),
                "parameters": {
                    parameter["id"]: float(parameter["default"])
                    for parameter in stream.get("parameters", [])
                },
            }
            for stream in registry["edge_streams"]
        },
        "edge_integration": {
            "prior_probability": float(defaults["edge_prior_probability"]),
            "output_probability_cutoff": float(defaults["edge_probability_cutoff"]),
        },
        "path": {
            "enabled": True,
            "start": defaults["start_node"],
            "target": defaults["target_node"],
            "top_k": int(defaults["top_k_paths"]),
            "max_hops": int(defaults["maximum_hops"]),
            "minimum_edge_probability": float(
                defaults["path_minimum_edge_probability"]
            ),
            "signaling_intermediates_only": bool(
                defaults["signaling_intermediates_only"]
            ),
            "exclude_multirole_scaffolds": bool(
                defaults["exclude_multirole_scaffolds"]
            ),
            "allowed_intermediate_classes": [
                item["id"]
                for item in registry["path_ontology_classes"]
                if item.get("default_enabled")
            ],
        },
    }


def _number(value: object, name: str, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return number


def normalize_configuration(
    supplied: dict[str, Any] | None,
    registry: dict[str, Any],
) -> dict[str, Any]:
    config = default_configuration(registry)
    supplied = supplied or {}
    for group in ("node_streams", "edge_streams"):
        incoming = supplied.get(group, {})
        definitions = {item["id"]: item for item in registry[group]}
        for stream_id, state in config[group].items():
            if stream_id in incoming:
                state["enabled"] = bool(incoming[stream_id].get("enabled", state["enabled"]))
                state["weight"] = _number(
                    incoming[stream_id].get("weight", state["weight"]),
                    f"{stream_id} weight",
                    0.0,
                    10.0,
                )
                definition = definitions[stream_id]
                if definition.get("normalization", {}).get("user_control", True):
                    state["tq_multiplier"] = _number(
                        incoming[stream_id].get(
                            "tq_multiplier", state["tq_multiplier"]
                        ),
                        f"{stream_id} Tq multiplier",
                        0.05,
                        20.0,
                    )
                parameter_values = incoming[stream_id].get("parameters", {})
                for parameter in definition.get("parameters", []):
                    parameter_id = parameter["id"]
                    state["parameters"][parameter_id] = _number(
                        parameter_values.get(
                            parameter_id, state["parameters"][parameter_id]
                        ),
                        f"{stream_id} {parameter_id}",
                        float(parameter["minimum"]),
                        float(parameter["maximum"]),
                    )
    config["node_integration"].update(supplied.get("node_integration", {}))
    config["edge_integration"].update(supplied.get("edge_integration", {}))
    config["path"].update(supplied.get("path", {}))

    node_integration = config["node_integration"]
    node_integration["include_second_messengers"] = bool(
        node_integration["include_second_messengers"]
    )
    node_integration["non_neutral_tolerance"] = _number(
        node_integration["non_neutral_tolerance"],
        "node non-neutral tolerance",
        0.0,
        0.1,
    )
    edge_integration = config["edge_integration"]
    edge_integration["prior_probability"] = _number(
        edge_integration["prior_probability"], "edge prior probability", 1e-9, 1 - 1e-9
    )
    edge_integration["output_probability_cutoff"] = _number(
        edge_integration["output_probability_cutoff"],
        "edge output cutoff",
        0.0,
        1.0,
    )
    path = config["path"]
    path["enabled"] = bool(path["enabled"])
    path["start"] = str(path["start"]).strip()
    path["target"] = str(path["target"]).strip()
    path["top_k"] = int(_number(path["top_k"], "top paths", 1, 500))
    path["max_hops"] = int(_number(path["max_hops"], "maximum hops", 1, 12))
    path["minimum_edge_probability"] = _number(
        path["minimum_edge_probability"], "path edge cutoff", 0.0, 1 - 1e-12
    )
    path["signaling_intermediates_only"] = bool(path["signaling_intermediates_only"])
    path["exclude_multirole_scaffolds"] = bool(path["exclude_multirole_scaffolds"])
    selected_classes = path.get("allowed_intermediate_classes")
    if not isinstance(selected_classes, list):
        raise ValueError("allowed intermediate ontology classes must be a list")
    selected_class_ids = {
        str(value).strip() for value in selected_classes if str(value).strip()
    }
    known_class_ids = {
        item["id"] for item in registry["path_ontology_classes"]
    }
    unknown_class_ids = selected_class_ids.difference(known_class_ids)
    if unknown_class_ids:
        raise ValueError(
            "unknown intermediate ontology classes: "
            + ", ".join(sorted(unknown_class_ids))
        )
    path["allowed_intermediate_classes"] = [
        item["id"]
        for item in registry["path_ontology_classes"]
        if item["id"] in selected_class_ids
    ]
    if path["enabled"] and (not path["start"] or not path["target"]):
        raise ValueError("both a starting node and target are required for path finding")

    effective_nodes = [
        stream_id
        for stream_id, state in config["node_streams"].items()
        if state["enabled"] and state["weight"] > 0
    ]
    effective_edges = [
        stream_id
        for stream_id, state in config["edge_streams"].items()
        if state["enabled"] and state["weight"] > 0
    ]
    if not effective_nodes:
        raise ValueError("enable at least one node evidence stream with positive weight")
    if not effective_edges:
        raise ValueError("enable at least one edge evidence stream with positive weight")

    edge_defs = {stream["id"]: stream for stream in registry["edge_streams"]}
    if all(edge_defs[stream_id].get("derived") for stream_id in effective_edges):
        raise ValueError(
            "scaffold closure requires at least one non-derived edge stream to "
            "construct its pre-closure graph"
        )
    exclusive: dict[str, list[str]] = {}
    for stream_id in effective_edges:
        group = edge_defs[stream_id].get("exclusive_group")
        if group:
            exclusive.setdefault(group, []).append(stream_id)
    conflicts = {group: ids for group, ids in exclusive.items() if len(ids) > 1}
    if conflicts:
        text = "; ".join(f"{group}: {', '.join(ids)}" for group, ids in conflicts.items())
        raise ValueError(f"mutually exclusive edge streams are enabled: {text}")
    return config


def stable_expit(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -700.0, 700.0)))


def complement_minimum_likelihood(
    values: np.ndarray | pd.Series,
    thresholds: np.ndarray | pd.Series | float,
) -> np.ndarray:
    """Apply the project's floor-preserving Gaussian complement kernel."""
    x = np.asarray(values, dtype=float)
    tq = np.asarray(thresholds, dtype=float)
    result = np.full(np.broadcast_shapes(x.shape, tq.shape), NEUTRAL_LIKELIHOOD)
    x_broadcast, tq_broadcast = np.broadcast_arrays(x, tq)
    valid = np.isfinite(x_broadcast) & np.isfinite(tq_broadcast) & (tq_broadcast > 0)
    if np.any(valid):
        z = np.maximum(x_broadcast[valid], 0.0) / tq_broadcast[valid]
        result[valid] = np.maximum(
            NEUTRAL_LIKELIHOOD,
            1.0 - np.exp(-0.5 * np.square(z)),
        )
    return result


def _stream_multiplier(state: dict[str, Any]) -> float:
    return float(state.get("tq_multiplier", 1.0))


def _aligned_series(
    table: pd.DataFrame,
    key_column: str,
    value_column: str,
    keys: pd.Series,
) -> np.ndarray:
    lookup = table.drop_duplicates(key_column).set_index(key_column)[value_column]
    return pd.to_numeric(keys.map(lookup), errors="coerce").to_numpy(float)


def node_stream_values(
    project: Path,
    factors: pd.DataFrame,
    definition: dict[str, Any],
    state: dict[str, Any],
) -> np.ndarray:
    """Return one node stream after applying its independent Tq setting."""
    multiplier = _stream_multiplier(state)
    if math.isclose(multiplier, 1.0, rel_tol=0.0, abs_tol=1e-15):
        return pd.to_numeric(
            factors[definition["column"]], errors="coerce"
        ).fillna(definition["neutral_value"]).to_numpy(float)

    handler = definition["normalization"]["handler"]
    genes = factors["gene_symbol"].astype(str)
    if handler == "protein_abundance":
        raw = pd.read_csv(project / PROTEIN_RAW_RELATIVE, sep="\t")
        values = _aligned_series(
            raw, "gene_symbol", "linear_relative_abundance", genes
        )
        base_tq = float(pd.to_numeric(raw["T_q"], errors="coerce").dropna().iloc[0])
        return complement_minimum_likelihood(values, base_tq * multiplier)
    if handler == "pc_transcript":
        raw = pd.read_csv(project / PC_RAW_RELATIVE, sep="\t")
        values = _aligned_series(raw, "gene_symbol", "pc_median_tpm", genes)
        base_tq = float(pd.to_numeric(raw["T_q"], errors="coerce").dropna().iloc[0])
        observed_positive = np.isfinite(values) & (values > 0)
        result = np.full(len(factors), NEUTRAL_LIKELIHOOD)
        result[observed_positive] = complement_minimum_likelihood(
            values[observed_positive], base_tq * multiplier
        )
        return result
    if handler == "kinase_activity":
        values = pd.to_numeric(
            factors["kinase_absolute_lfc"], errors="coerce"
        ).to_numpy(float)
        observed = factors["kinase_evidence_observed"].fillna(False).astype(bool).to_numpy()
        result = np.ones(len(factors), dtype=float)
        result[observed] = complement_minimum_likelihood(
            values[observed], 0.17 * multiplier
        ) / NEUTRAL_LIKELIHOOD
        return result
    if handler == "phosphoprotein_response":
        raw = pd.read_csv(project / PHOSPHOPROTEIN_RAW_RELATIVE, sep="\t")
        values = _aligned_series(raw, "gene_symbol", "max_absolute_lfc", genes)
        thresholds = _aligned_series(raw, "gene_symbol", "matched_T_q", genes)
        observed = np.isfinite(values) & np.isfinite(thresholds) & (thresholds > 0)
        result = np.ones(len(factors), dtype=float)
        result[observed] = complement_minimum_likelihood(
            values[observed], thresholds[observed] * multiplier
        ) / NEUTRAL_LIKELIHOOD
        return result
    raise ValueError(f"unsupported node normalization handler: {handler}")


def select_nodes(
    project: Path,
    registry: dict[str, Any],
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    factors = pd.read_csv(project / NODE_FACTORS_RELATIVE, sep="\t")
    universe = pd.read_csv(project / UNIVERSE_RELATIVE, sep="\t", dtype=str).fillna("")
    stream_defs = {stream["id"]: stream for stream in registry["node_streams"]}
    tolerance = config["node_integration"]["non_neutral_tolerance"]
    log_weight = np.log(pd.to_numeric(factors["initial_prior"], errors="raise").to_numpy(float))
    any_non_neutral = np.zeros(len(factors), dtype=bool)
    active_streams: list[dict[str, Any]] = []
    for stream_id, state in config["node_streams"].items():
        if not state["enabled"] or state["weight"] <= 0:
            continue
        definition = stream_defs[stream_id]
        values = node_stream_values(project, factors, definition, state)
        if (values <= 0).any():
            raise ValueError(f"node stream {stream_id} contains a non-positive factor")
        weight = float(state["weight"])
        log_weight += weight * np.log(values)
        non_neutral = np.abs(values - float(definition["neutral_value"])) > tolerance
        any_non_neutral |= non_neutral
        factors[f"gui_{stream_id}_non_neutral"] = non_neutral
        factors[f"gui_{stream_id}_factor"] = values
        factors[f"gui_{stream_id}_weighted_log_factor"] = weight * np.log(values)
        active_streams.append(
            {
                "id": stream_id,
                "label": definition["label"],
                "weight": weight,
                "tq_multiplier": _stream_multiplier(state),
                "normalization_reference": definition["normalization"]["reference"],
                "non_neutral_candidates": int(non_neutral.sum()),
            }
        )
    scaled = np.exp(log_weight - float(log_weight.max()))
    posterior = scaled / scaled.sum()
    factors["gui_posterior"] = posterior
    factors["gui_rank"] = (
        pd.Series(posterior).rank(method="min", ascending=False).astype(int).to_numpy()
    )
    factors["gui_any_selected_stream_non_neutral"] = any_non_neutral

    seed_symbols = set(universe["symbol"])
    selected_protein_symbols = set(
        factors.loc[any_non_neutral, "gene_symbol"].astype(str)
    )
    selected_seed_symbols = selected_protein_symbols.intersection(seed_symbols)
    added_symbols = selected_protein_symbols.difference(seed_symbols)
    factors["gui_available_in_edge_catalog"] = factors["gene_symbol"].isin(seed_symbols)
    factors["gui_selected_in_graph"] = any_non_neutral
    include_messengers = config["node_integration"]["include_second_messengers"]
    selected_rows = universe[universe["symbol"].isin(selected_seed_symbols)].copy()
    selected_rows["gui_selection_reason"] = "selected_evidence_non_neutral"
    if added_symbols:
        liberal = pd.read_csv(
            project / "data/node_selection/mouse_signaling_nodes_liberal.tsv",
            sep="\t",
            dtype=str,
        ).fillna("")
        liberal = liberal.drop_duplicates("symbol").set_index("symbol")
        dynamic_source = factors.loc[
            factors["gene_symbol"].isin(added_symbols)
        ].copy()
        dynamic_source = dynamic_source.sort_values(
            ["gui_rank", "gene_symbol"], kind="stable"
        )
        dynamic_rows = pd.DataFrame(
            {
                "symbol": dynamic_source["gene_symbol"].astype(str),
                "display_symbol": dynamic_source["gene_symbol"].astype(str),
                "name": dynamic_source["node_name"].fillna("").astype(str),
                "classes": dynamic_source["node_classes"].fillna("").astype(str),
                "node_type": "protein",
                "stable_id": "",
                "scope_tier": "incrementally_characterized",
                "selection_basis": "bayesian_non_neutral",
                "bayesian_score_status": "scored",
                "included_by_curated_rule": False,
                "source_url": "",
                "scope_note": "Added beyond the immutable 891-node seed catalog.",
            }
        )
        dynamic_rows["selected_uniprot"] = dynamic_rows["symbol"].map(
            liberal["uniprot"]
        ).fillna("")
        dynamic_rows["gui_selection_reason"] = "selected_evidence_non_neutral_incremental"
        selected_rows = pd.concat([selected_rows, dynamic_rows], ignore_index=True)
    if include_messengers:
        messengers = universe[universe["node_type"] == "molecule"].copy()
        messengers["gui_selection_reason"] = "curated_second_messenger"
        selected_rows = pd.concat([selected_rows, messengers], ignore_index=True)
    order = {symbol: index for index, symbol in enumerate(universe["symbol"])}
    selected_rows["gui_universe_index"] = selected_rows["symbol"].map(order)
    dynamic_order = {
        symbol: len(order) + index
        for index, symbol in enumerate(
            sorted(added_symbols, key=lambda value: (value.casefold(), value))
        )
    }
    selected_rows["gui_universe_index"] = selected_rows["gui_universe_index"].fillna(
        selected_rows["symbol"].map(dynamic_order)
    )
    selected_rows = selected_rows.sort_values("gui_universe_index").reset_index(drop=True)
    summary = {
        "candidate_protein_count": int(len(factors)),
        "selected_protein_count": int(len(selected_protein_symbols)),
        "selected_seed_protein_count": int(len(selected_seed_symbols)),
        "incrementally_added_protein_count": int(len(added_symbols)),
        "non_neutral_candidates_outside_edge_catalog": int(len(added_symbols)),
        "curated_second_messenger_count": int(
            (selected_rows["node_type"] == "molecule").sum()
        ),
        "selected_node_count": int(len(selected_rows)),
        "active_streams": active_streams,
        "posterior_sum": float(posterior.sum()),
        "selection_rule": (
            "A protein is selected when at least one enabled, positive-weight node "
            "stream differs from its neutral floor. Enabled factors are exponentiated "
            "by their user weights, multiplied with the uniform prior, and normalized."
        ),
        "catalog_constraint": (
            "The validated 891-node graph is the immutable seed. Newly non-neutral "
            "proteins are inserted into the graph after all new unordered pairs are "
            "characterized and written to the persistent incremental edge cache."
        ),
    }
    return factors, selected_rows, summary


def resolve_target_for_graph(
    target: str,
    universe: pd.DataFrame,
    project: Path,
) -> tuple[str, bool, dict[str, Any] | None]:
    symbols_ci = {symbol.casefold(): symbol for symbol in universe["symbol"].astype(str)}
    if target.casefold() in symbols_ci:
        return symbols_ci[target.casefold()], False, None
    symbol, entry, method = resolve_target(target, project / UNIPROT_RELATIVE)
    return symbol, True, {
        "target_mapping_method": method,
        "target_uniprot": str(entry["Entry"]),
        "target_protein_name": str(entry["Protein names"]),
    }


def _read_aligned_matrix(
    path: Path,
    symbols: list[str],
) -> np.ndarray:
    frame = pd.read_csv(path, sep="\t", index_col=0).reindex(
        index=symbols, columns=symbols
    )
    if frame.isna().any().any():
        raise ValueError(f"matrix does not align to requested graph nodes: {path}")
    return frame.to_numpy(float)


def _upper_factor_table(
    symbols: list[str],
    factors: np.ndarray,
    *,
    include: np.ndarray | None = None,
) -> pd.DataFrame:
    left, right = np.triu_indices(len(symbols), 1)
    values = np.asarray(factors, dtype=float)[left, right]
    keep = np.abs(values - 1.0) > FACTOR_EPSILON
    if include is not None:
        keep &= np.asarray(include, dtype=bool)[left, right]
    symbol_array = np.asarray(symbols)
    return pd.DataFrame(
        {
            "node_a": symbol_array[left[keep]],
            "node_b": symbol_array[right[keep]],
            "bayes_factor": values[keep],
        }
    )


def _localization_edge_factors(
    project: Path,
    symbols: list[str],
    multiplier: float,
) -> pd.DataFrame:
    profiles = pd.read_csv(project / MPKCCD_PROFILES_RELATIVE, sep="\t").set_index("symbol")
    profiles = profiles.reindex(symbols)
    columns = ["1K", "4K", "17K", "200Kp", "200Ks"]
    observed = profiles["localization_observed"].fillna(False).astype(bool).to_numpy().copy()
    raw = profiles[columns].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    thresholds = pd.to_numeric(profiles["localization_Tq"], errors="coerce").to_numpy(float)
    observed &= np.isfinite(raw).all(axis=1) & np.isfinite(thresholds) & (thresholds > 0)
    likelihood = np.full((len(symbols), len(symbols)), NEUTRAL_LIKELIHOOD)
    indices = np.flatnonzero(observed)
    if len(indices):
        dot = raw[indices] @ raw[indices].T
        tq = thresholds[indices] * multiplier
        directed = complement_minimum_likelihood(dot, tq[np.newaxis, :])
        symmetric = (directed + directed.T) / 2.0
        likelihood[np.ix_(indices, indices)] = symmetric
    np.fill_diagonal(likelihood, NEUTRAL_LIKELIHOOD)
    return _upper_factor_table(symbols, likelihood / NEUTRAL_LIKELIHOOD)


def _kinase_predictor_edge_factors(
    project: Path,
    symbols: list[str],
    multiplier: float,
) -> pd.DataFrame:
    predictions = pd.read_csv(project / KINASE_PREDICTIONS_RELATIVE, sep="\t")
    used = predictions["used_for_undirected_edge"]
    if used.dtype != bool:
        used = used.astype(str).str.casefold().eq("true")
    predictions = predictions.loc[used].copy()
    symbol_set = set(symbols)
    predictions = predictions.loc[
        predictions["undirected_node_a"].isin(symbol_set)
        & predictions["undirected_node_b"].isin(symbol_set)
    ]
    raw_score = pd.to_numeric(predictions["raw_score"], errors="raise").to_numpy(float)
    tq = pd.to_numeric(predictions["site_Tq_q75"], errors="raise").to_numpy(float)
    site_bf = complement_minimum_likelihood(
        np.maximum(raw_score, 0.0), tq * multiplier
    ) / NEUTRAL_LIKELIHOOD
    predictions["gui_log_bayes_factor"] = np.log(site_bf)
    grouped = predictions.groupby(
        ["undirected_node_a", "undirected_node_b"], sort=False
    )["gui_log_bayes_factor"].sum().reset_index()
    grouped["bayes_factor"] = np.exp(
        np.clip(grouped["gui_log_bayes_factor"].to_numpy(float), -700.0, 700.0)
    )
    grouped = grouped.loc[
        np.abs(grouped["bayes_factor"] - 1.0) > FACTOR_EPSILON
    ].rename(
        columns={"undirected_node_a": "node_a", "undirected_node_b": "node_b"}
    )
    return grouped[["node_a", "node_b", "bayes_factor"]]


def _string_edge_factors(
    project: Path,
    symbols: list[str],
    multiplier: float,
) -> pd.DataFrame:
    score = _read_aligned_matrix(project / STRING_SCORE_MATRIX_RELATIVE, symbols)
    reference = STRING_REFERENCE_SCORE * multiplier
    if not 0 < reference < 1:
        raise ValueError("STRING reference score after scaling must be between 0 and 1")
    observed = score > 0
    safe_score = np.clip(score, 1e-12, 1 - 1e-12)
    score_odds = safe_score / (1.0 - safe_score)
    reference_odds = reference / (1.0 - reference)
    factors = np.ones_like(score)
    factors[observed] = score_odds[observed] / reference_odds
    return _upper_factor_table(symbols, factors, include=observed)


def _hpa_binary_profiles(
    profiles: pd.DataFrame,
    high_confidence: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    location_columns = [column for column in profiles.columns if column.startswith("location__")]
    if high_confidence:
        binary = np.zeros((len(profiles), len(location_columns)), dtype=float)
        column_index = {column.removeprefix("location__"): i for i, column in enumerate(location_columns)}
        for row_index, value in enumerate(profiles["hpa_high_confidence_locations"].fillna("")):
            for location in str(value).split(";"):
                safe = re.sub(r"[^A-Za-z0-9]+", "_", location).strip("_").lower()
                if safe in column_index:
                    binary[row_index, column_index[safe]] = 1.0
        observed = profiles["hpa_high_confidence_profile_observed"].fillna(False).astype(bool).to_numpy().copy()
        threshold_column = "hpa_high_confidence_Tq_q75"
    else:
        binary = profiles[location_columns].apply(pd.to_numeric, errors="coerce").fillna(0).to_numpy(float)
        observed = profiles["hpa_profile_observed"].fillna(False).astype(bool).to_numpy().copy()
        threshold_column = "hpa_Tq_q75"
    thresholds = pd.to_numeric(profiles[threshold_column], errors="coerce").to_numpy(float)
    observed &= binary.sum(axis=1) > 0
    return binary, observed, thresholds


def _hpa_edge_factors(
    project: Path,
    symbols: list[str],
    multiplier: float,
    *,
    high_confidence: bool,
) -> pd.DataFrame:
    profiles = pd.read_csv(project / HPA_PROFILES_RELATIVE, sep="\t").set_index("symbol").reindex(symbols)
    binary, observed, thresholds = _hpa_binary_profiles(profiles, high_confidence)
    likelihood = np.full((len(symbols), len(symbols)), NEUTRAL_LIKELIHOOD)
    indices = np.flatnonzero(observed)
    if len(indices):
        selected = binary[indices]
        unit = selected / np.linalg.norm(selected, axis=1, keepdims=True)
        similarity = unit @ unit.T
        tq = thresholds[indices] * multiplier
        directed = np.full_like(similarity, NEUTRAL_LIKELIHOOD)
        valid_tq = np.isfinite(tq) & (tq > 0)
        if np.any(valid_tq):
            directed[valid_tq] = complement_minimum_likelihood(
                similarity[valid_tq], tq[valid_tq, np.newaxis]
            )
        likelihood[np.ix_(indices, indices)] = (directed + directed.T) / 2.0
    np.fill_diagonal(likelihood, NEUTRAL_LIKELIHOOD)
    return _upper_factor_table(symbols, likelihood / NEUTRAL_LIKELIHOOD)


def _omnipath_edge_factors(
    project: Path,
    symbols: list[str],
    multiplier: float,
) -> pd.DataFrame:
    effort = _read_aligned_matrix(project / OMNIPATH_EFFORT_MATRIX_RELATIVE, symbols)
    observed = effort > 0
    support = 1.0 - np.exp(-0.5 * np.square(effort / (6.0 * multiplier)))
    likelihood = NEUTRAL_LIKELIHOOD + (1.0 - NEUTRAL_LIKELIHOOD) * support
    factors = np.ones_like(effort)
    factors[observed] = likelihood[observed] / NEUTRAL_LIKELIHOOD
    return _upper_factor_table(symbols, factors, include=observed)


def _stitch_bayes_factors(scores: np.ndarray, reference: float) -> np.ndarray:
    """Convert STITCH confidence scores to positive-only evidence factors."""
    if not 0 < reference < 1:
        raise ValueError("STITCH reference score after scaling must be between 0 and 1")
    scores = np.asarray(scores, dtype=float)
    if (~np.isfinite(scores) | (scores <= 0) | (scores >= 1)).any():
        raise ValueError("STITCH scores must be finite probabilities between 0 and 1")
    score_odds = scores / (1.0 - scores)
    reference_odds = reference / (1.0 - reference)
    return np.maximum(1.0, score_odds / reference_odds)


def _stitch_edge_factors(
    project: Path,
    symbols: list[str],
    multiplier: float,
) -> pd.DataFrame:
    evidence = pd.read_csv(project / STITCH_UNIVERSE_EDGES_RELATIVE, sep="\t")
    symbol_set = set(symbols)
    evidence = evidence.loc[
        evidence["node_a"].isin(symbol_set) & evidence["node_b"].isin(symbol_set)
    ].copy()
    reference = STITCH_REFERENCE_SCORE * multiplier
    evidence["bayes_factor"] = _stitch_bayes_factors(
        pd.to_numeric(evidence["stitch_score"], errors="raise").to_numpy(float),
        reference,
    )
    evidence = evidence.loc[
        np.abs(evidence["bayes_factor"] - 1.0) > FACTOR_EPSILON
    ]
    return evidence[["node_a", "node_b", "bayes_factor"]]


def edge_stream_factor_table(
    project: Path,
    definition: dict[str, Any],
    state: dict[str, Any],
    symbols: list[str],
) -> pd.DataFrame:
    """Load default BFs or recompute one stream with its requested scale."""
    if definition.get("derived"):
        raise ValueError(
            f"derived stream {definition['id']} must be calculated from the "
            "pre-closure graph, not loaded as an independent factor table"
        )
    multiplier = _stream_multiplier(state)
    if math.isclose(multiplier, 1.0, rel_tol=0.0, abs_tol=1e-15):
        table = pd.read_csv(
            project / definition["factor_file"], sep="\t", compression="gzip"
        )
        return table[["node_a", "node_b", definition["factor_column"]]].rename(
            columns={definition["factor_column"]: "bayes_factor"}
        )
    handler = definition["normalization"]["handler"]
    if handler == "mpkccd_localization":
        return _localization_edge_factors(project, symbols, multiplier)
    if handler == "kinase_predictor":
        return _kinase_predictor_edge_factors(project, symbols, multiplier)
    if handler == "string_v12":
        return _string_edge_factors(project, symbols, multiplier)
    if handler == "hpa_primary":
        return _hpa_edge_factors(project, symbols, multiplier, high_confidence=False)
    if handler == "hpa_high_confidence":
        return _hpa_edge_factors(project, symbols, multiplier, high_confidence=True)
    if handler == "omnipath_core":
        return _omnipath_edge_factors(project, symbols, multiplier)
    if handler == "stitch_secondary_messenger":
        return _stitch_edge_factors(project, symbols, multiplier)
    raise ValueError(f"unsupported edge normalization handler: {handler}")


def _metadata_for_graph(
    project: Path,
    graph_symbols: list[str],
    graph_metadata: pd.DataFrame | None,
) -> pd.DataFrame:
    """Return one metadata row per graph symbol without inventing classifications."""
    base = pd.read_csv(project / UNIVERSE_RELATIVE, sep="\t", dtype=str).fillna("")
    frames = [base]
    if graph_metadata is not None:
        frames.append(graph_metadata.copy().fillna(""))
    metadata = pd.concat(frames, ignore_index=True, sort=False)
    if "symbol" not in metadata.columns:
        raise ValueError("graph metadata must contain a symbol column")
    metadata["symbol"] = metadata["symbol"].astype(str)
    metadata = metadata.drop_duplicates("symbol", keep="last").set_index("symbol")
    return metadata.reindex(graph_symbols).fillna("")


def scaffold_triadic_closure_factors(
    project: Path,
    graph_symbols: list[str],
    preclosure_probabilities: np.ndarray,
    state: dict[str, Any],
    *,
    graph_metadata: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Assign one fixed factor to every protein pair sharing a strong scaffold.

    Only protein nodes can be endpoints or common scaffolds. A common node must
    contain the exact ``adaptor_scaffold`` class token. A pair qualifies when
    both of its protein-scaffold probabilities are strictly above the selected
    anchor cutoff for at least one shared scaffold. Every qualifying pair gets
    the same configured support likelihood relative to the neutral likelihood;
    scaffold degree and the number of shared scaffolds do not change the factor.
    The operation is one pass, so inferred closure edges never become anchors.
    """
    symbols = list(graph_symbols)
    metadata = _metadata_for_graph(project, symbols, graph_metadata)
    node_types = metadata.get("node_type", pd.Series("", index=metadata.index))
    classes = metadata.get("classes", pd.Series("", index=metadata.index))
    protein_indices = np.flatnonzero(
        node_types.astype(str).str.casefold().eq("protein").to_numpy()
    )
    scaffold_indices = np.asarray(
        [
            index
            for index in protein_indices
            if "adaptor_scaffold"
            in {
                token.strip()
                for token in str(classes.iloc[index]).split(";")
                if token.strip()
            }
        ],
        dtype=int,
    )
    columns = [
        "node_a",
        "node_b",
        "preclosure_probability",
        "bayes_factor",
        "closure_support_likelihood",
        "supporting_scaffold_count",
        "supporting_scaffolds",
        "anchor_probability_cutoff",
        "closure_rule",
    ]
    anchor_cutoff = float(state.get("parameters", {}).get(
        "anchor_probability_cutoff", 0.9
    ))
    closure_likelihood = float(state.get("parameters", {}).get(
        "closure_likelihood", 0.9
    ))
    if not 0.5 <= closure_likelihood < 1.0:
        raise ValueError("closure likelihood must be at least 0.5 and below 1.0")
    closure_factor = closure_likelihood / NEUTRAL_LIKELIHOOD
    rule_text = (
        "Both protein-scaffold probabilities must be strictly above the anchor "
        "cutoff for at least one shared adaptor_scaffold; every qualifying pair "
        "receives the same factor in one non-recursive pass."
    )
    empty_summary = {
        "scaffold_node_count": int(len(scaffold_indices)),
        "usable_scaffold_count": 0,
        "anchor_probability_cutoff_exclusive": anchor_cutoff,
        "closure_support_likelihood": closure_likelihood,
        "closure_bayes_factor": closure_factor,
        "anchored_protein_scaffold_associations": 0,
        "pairs_sharing_at_least_one_scaffold": 0,
        "pairs_with_non_neutral_closure_factor": 0,
        "degree_adjustment": "none",
        "empirical_tq": None,
        "closure_rule": rule_text,
    }
    if len(protein_indices) < 2 or not len(scaffold_indices):
        return pd.DataFrame(columns=columns), empty_summary

    association = preclosure_probabilities[np.ix_(protein_indices, scaffold_indices)]
    anchors = association > anchor_cutoff
    partner_counts = anchors.sum(axis=0)
    usable = np.flatnonzero(partner_counts >= 2)
    if not len(usable):
        return pd.DataFrame(columns=columns), empty_summary

    usable_anchors = anchors[:, usable].astype(np.uint16)
    support_count = usable_anchors @ usable_anchors.T
    n_proteins = len(protein_indices)
    upper_indices = np.triu_indices(n_proteins, 1)
    qualifying = support_count[upper_indices] > 0
    rows_i = upper_indices[0][qualifying]
    rows_j = upper_indices[1][qualifying]
    protein_symbols = np.asarray(symbols, dtype=object)[protein_indices]
    scaffold_symbols = np.asarray(symbols, dtype=object)[scaffold_indices]
    supporting_lists: list[str] = []
    for left, right in zip(rows_i, rows_j, strict=True):
        support = np.flatnonzero(anchors[left] & anchors[right])
        supporting_lists.append(";".join(scaffold_symbols[support].tolist()))
    audit = pd.DataFrame(
        {
            "node_a": protein_symbols[rows_i],
            "node_b": protein_symbols[rows_j],
            "preclosure_probability": preclosure_probabilities[
                protein_indices[rows_i], protein_indices[rows_j]
            ],
            "bayes_factor": np.full(len(rows_i), closure_factor),
            "closure_support_likelihood": np.full(
                len(rows_i), closure_likelihood
            ),
            "supporting_scaffold_count": support_count[rows_i, rows_j],
            "supporting_scaffolds": supporting_lists,
            "anchor_probability_cutoff": anchor_cutoff,
            "closure_rule": rule_text,
        }
    )
    audit = audit.sort_values(
        ["supporting_scaffold_count", "node_a", "node_b"],
        ascending=[False, True, True],
        kind="stable",
    ).reset_index(drop=True)
    summary = {
        **empty_summary,
        "usable_scaffold_count": int(len(usable)),
        "anchored_protein_scaffold_associations": int(anchors.sum()),
        "pairs_sharing_at_least_one_scaffold": int(len(rows_i)),
        "pairs_with_non_neutral_closure_factor": int(
            len(rows_i) if closure_factor > 1.0 + FACTOR_EPSILON else 0
        ),
        "maximum_closure_factor": closure_factor,
    }
    return audit, summary


def combine_edge_factors(
    project: Path,
    registry: dict[str, Any],
    config: dict[str, Any],
    graph_symbols: list[str],
    *,
    graph_metadata: pd.DataFrame | None = None,
    audit_collector: dict[str, pd.DataFrame] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    prior = config["edge_integration"]["prior_probability"]
    base_log_odds = math.log(prior / (1.0 - prior))
    log_odds = np.full((len(graph_symbols), len(graph_symbols)), base_log_odds, dtype=float)
    index = {symbol: position for position, symbol in enumerate(graph_symbols)}
    seed_universe = pd.read_csv(
        project / UNIVERSE_RELATIVE, sep="\t", dtype=str
    ).fillna("")
    seed_set = set(seed_universe["symbol"].astype(str))
    seed_graph_symbols = [symbol for symbol in graph_symbols if symbol in seed_set]
    has_incremental_nodes = len(seed_graph_symbols) != len(graph_symbols)
    stream_defs = {stream["id"]: stream for stream in registry["edge_streams"]}
    active_streams: list[dict[str, Any]] = []
    derived_streams: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for stream_id, state in config["edge_streams"].items():
        if not state["enabled"] or state["weight"] <= 0:
            continue
        definition = stream_defs[stream_id]
        if definition.get("derived"):
            derived_streams.append((stream_id, definition, state))
            continue
        seed_table = edge_stream_factor_table(
            project, definition, state, seed_graph_symbols
        )
        tables = [seed_table]
        if has_incremental_nodes:
            tables.append(
                incremental_factor_table(
                    project,
                    definition["normalization"]["handler"],
                    _stream_multiplier(state),
                    graph_symbols,
                )
            )
        table = pd.concat(tables, ignore_index=True)
        left = table["node_a"].map(index)
        right = table["node_b"].map(index)
        mask = left.notna() & right.notna()
        values = pd.to_numeric(
            table.loc[mask, "bayes_factor"], errors="raise"
        ).to_numpy(float)
        if (values <= 0).any():
            raise ValueError(f"edge stream {stream_id} contains non-positive factors")
        weighted_logs = float(state["weight"]) * np.log(values)
        left_index = left[mask].astype(int).to_numpy()
        right_index = right[mask].astype(int).to_numpy()
        np.add.at(log_odds, (left_index, right_index), weighted_logs)
        np.add.at(log_odds, (right_index, left_index), weighted_logs)
        active_streams.append(
            {
                "id": stream_id,
                "label": definition["label"],
                "weight": float(state["weight"]),
                "tq_multiplier": _stream_multiplier(state),
                "normalization_reference": definition["normalization"]["reference"],
                "supported_pairs_in_selected_graph": int(mask.sum()),
            }
        )
    preclosure_probabilities = stable_expit(log_odds)
    np.fill_diagonal(preclosure_probabilities, 0.0)
    for stream_id, definition, state in derived_streams:
        handler = definition["normalization"]["handler"]
        if handler != "scaffold_triadic_closure":
            raise ValueError(f"unsupported derived edge handler: {handler}")
        table, closure_summary = scaffold_triadic_closure_factors(
            project,
            graph_symbols,
            preclosure_probabilities,
            state,
            graph_metadata=graph_metadata,
        )
        left_index = table["node_a"].map(index).astype(int).to_numpy()
        right_index = table["node_b"].map(index).astype(int).to_numpy()
        values = table["bayes_factor"].to_numpy(float)
        weighted_logs = float(state["weight"]) * np.log(values)
        np.add.at(log_odds, (left_index, right_index), weighted_logs)
        np.add.at(log_odds, (right_index, left_index), weighted_logs)
        if len(table):
            pair_pre = np.clip(
                table["preclosure_probability"].to_numpy(float),
                FACTOR_EPSILON,
                1.0 - FACTOR_EPSILON,
            )
            pair_posteriors = stable_expit(
                np.log(pair_pre / (1.0 - pair_pre)) + weighted_logs
            )
        else:
            pair_posteriors = np.asarray([], dtype=float)
        table["postclosure_probability"] = pair_posteriors
        cutoff = config["edge_integration"]["output_probability_cutoff"]
        closure_summary["newly_above_output_cutoff"] = int(
            (
                (table["preclosure_probability"] <= cutoff)
                & (table["postclosure_probability"] > cutoff)
            ).sum()
        )
        if audit_collector is not None:
            audit_collector[stream_id] = table
        active_streams.append(
            {
                "id": stream_id,
                "label": definition["label"],
                "weight": float(state["weight"]),
                "normalization": "binary_fixed_likelihood",
                "normalization_reference": definition["normalization"]["reference"],
                "supported_pairs_in_selected_graph": int(len(table)),
                "derived": True,
                "parameters": dict(state.get("parameters", {})),
                "derivation_summary": closure_summary,
            }
        )
    probabilities = stable_expit(log_odds)
    np.fill_diagonal(probabilities, 0.0)
    matrix = pd.DataFrame(probabilities, index=graph_symbols, columns=graph_symbols)
    matrix.index.name = "symbol"
    upper = probabilities[np.triu_indices(len(graph_symbols), 1)]
    cutoff = config["edge_integration"]["output_probability_cutoff"]
    summary = {
        "edge_prior_probability": prior,
        "unique_pair_count": int(len(upper)),
        "pairs_above_output_cutoff": int((upper > cutoff).sum()),
        "pairs_at_exact_prior": int(np.isclose(upper, prior, atol=1e-12, rtol=0).sum()),
        "output_probability_cutoff_exclusive": cutoff,
        "active_streams": active_streams,
        "integration_rule": (
            "Edge prior odds are multiplied by each selected Bayes factor raised "
            "to its user-specified weight. Missing sparse-table entries receive BF=1. "
            "If enabled, scaffold closure is derived once from the pre-closure graph "
            "and appended without recursive feedback."
        ),
    }
    return matrix, summary


def load_or_build_external_target_vector(
    project: Path,
    target_input: str,
    target_symbol: str,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    cached = cached_target_extension(project, target_input, target_symbol)
    if cached is None:
        result = build_target_adjacency_vector(
            target_input,
            project_root=project,
            write_outputs=True,
            write_extended_matrix=True,
        )
        return result.vector.copy(), result.summary
    _, matrix_path, summary = cached
    vector_path = matrix_path.parent / "target_adjacency_vector.tsv"
    return pd.read_csv(vector_path, sep="\t"), summary


def _truth_array(values: pd.Series) -> np.ndarray:
    if values.dtype == bool:
        return values.to_numpy(bool)
    return values.fillna(False).astype(str).str.casefold().eq("true").to_numpy()


def external_target_stream_values(
    project: Path,
    target_symbol: str,
    vector_by_symbol: pd.DataFrame,
    symbols: pd.Index,
    definition: dict[str, Any],
    state: dict[str, Any],
) -> np.ndarray | None:
    multiplier = _stream_multiplier(state)
    handler = definition["normalization"]["handler"]
    if handler == "stitch_secondary_messenger":
        result = np.ones(len(symbols), dtype=float)
        if "string_target_id" not in vector_by_symbol.columns:
            return result
        target_ids = (
            vector_by_symbol["string_target_id"].fillna("").astype(str).str.strip()
        )
        target_ids = target_ids.loc[target_ids.ne("")].unique()
        if len(target_ids) != 1:
            return result
        evidence = pd.read_csv(
            project / STITCH_ALL_MOUSE_EDGES_RELATIVE,
            sep="\t",
            compression="gzip",
        )
        evidence = evidence.loc[evidence["protein"].eq(target_ids[0])].copy()
        if evidence.empty:
            return result
        reference = STITCH_REFERENCE_SCORE * multiplier
        evidence["bayes_factor"] = _stitch_bayes_factors(
            pd.to_numeric(evidence["stitch_score"], errors="raise").to_numpy(float),
            reference,
        )
        by_messenger = evidence.groupby("messenger_node")["bayes_factor"].max()
        mapped = symbols.to_series().map(by_messenger)
        found = mapped.notna().to_numpy()
        result[found] = mapped[found].to_numpy(float)
        return result
    column = definition.get("target_factor_column")
    if math.isclose(multiplier, 1.0, rel_tol=0.0, abs_tol=1e-15):
        if not column:
            return None
        return pd.to_numeric(
            vector_by_symbol.reindex(symbols)[column], errors="coerce"
        ).fillna(1.0).to_numpy(float)

    aligned = vector_by_symbol.reindex(symbols)
    result = np.ones(len(aligned), dtype=float)
    if handler == "mpkccd_localization":
        observed = _truth_array(aligned["mpkccd_localization_observed_for_both"])
        dot = pd.to_numeric(aligned["mpkccd_dot_product"], errors="coerce").to_numpy(float)
        target_tq = pd.to_numeric(aligned["mpkccd_target_Tq"], errors="coerce").to_numpy(float)
        node_tq = pd.to_numeric(aligned["mpkccd_node_Tq"], errors="coerce").to_numpy(float)
        valid = observed & np.isfinite(dot) & np.isfinite(target_tq) & np.isfinite(node_tq)
        if np.any(valid):
            target_factor = complement_minimum_likelihood(
                dot[valid], target_tq[valid] * multiplier
            )
            node_factor = complement_minimum_likelihood(
                dot[valid], node_tq[valid] * multiplier
            )
            result[valid] = ((target_factor + node_factor) / 2.0) / NEUTRAL_LIKELIHOOD
        return result
    if handler == "kinase_predictor":
        prediction_path = (
            project / "results/path_finding/target_extensions" / safe_name(target_symbol)
            / "target_kinase_predictions.tsv"
        )
        if not prediction_path.exists():
            return result
        predictions = pd.read_csv(prediction_path, sep="\t")
        raw_score = pd.to_numeric(predictions["raw_score"], errors="raise").to_numpy(float)
        tq = pd.to_numeric(predictions["site_Tq_q75"], errors="raise").to_numpy(float)
        predictions["gui_log_bf"] = np.log(
            complement_minimum_likelihood(
                np.maximum(raw_score, 0.0), tq * multiplier
            ) / NEUTRAL_LIKELIHOOD
        )
        grouped = predictions.groupby("kinase_node")["gui_log_bf"].sum()
        mapped = aligned.index.to_series().map(grouped)
        found = mapped.notna().to_numpy()
        result[found] = np.exp(np.clip(mapped[found].to_numpy(float), -700.0, 700.0))
        return result
    if handler == "string_v12":
        score = pd.to_numeric(aligned["string_combined_score"], errors="coerce").to_numpy(float)
        reference = STRING_REFERENCE_SCORE * multiplier
        if not 0 < reference < 1:
            raise ValueError("STRING reference score after scaling must be between 0 and 1")
        observed = np.isfinite(score) & (score > 0) & (score < 1)
        result[observed] = (
            score[observed] / (1.0 - score[observed])
        ) / (reference / (1.0 - reference))
        return result
    if handler == "hpa_primary":
        observed = _truth_array(aligned["hpa_profiles_observed_for_both"])
        similarity = pd.to_numeric(aligned["hpa_cosine_similarity"], errors="coerce").to_numpy(float)
        target_tq = pd.to_numeric(aligned["hpa_target_Tq"], errors="coerce").to_numpy(float)
        node_tq = pd.to_numeric(aligned["hpa_node_Tq"], errors="coerce").to_numpy(float)
        valid = observed & np.isfinite(similarity) & np.isfinite(target_tq) & np.isfinite(node_tq)
        if np.any(valid):
            target_factor = complement_minimum_likelihood(
                similarity[valid], target_tq[valid] * multiplier
            )
            node_factor = complement_minimum_likelihood(
                similarity[valid], node_tq[valid] * multiplier
            )
            result[valid] = ((target_factor + node_factor) / 2.0) / NEUTRAL_LIKELIHOOD
        return result
    if handler == "hpa_high_confidence":
        return None
    if handler == "omnipath_core":
        effort = pd.to_numeric(aligned["omnipath_curation_effort"], errors="coerce").to_numpy(float)
        observed = np.isfinite(effort) & (effort > 0)
        support = 1.0 - np.exp(-0.5 * np.square(effort[observed] / (6.0 * multiplier)))
        likelihood = NEUTRAL_LIKELIHOOD + (1.0 - NEUTRAL_LIKELIHOOD) * support
        result[observed] = likelihood / NEUTRAL_LIKELIHOOD
        return result
    raise ValueError(f"unsupported target normalization handler: {handler}")


def append_external_target(
    project: Path,
    matrix: pd.DataFrame,
    target_symbol: str,
    target_vector: pd.DataFrame,
    registry: dict[str, Any],
    config: dict[str, Any],
) -> tuple[pd.DataFrame, list[str]]:
    prior = config["edge_integration"]["prior_probability"]
    target_log_odds = np.full(len(matrix), math.log(prior / (1.0 - prior)), dtype=float)
    vector_by_symbol = target_vector.set_index("symbol")
    warnings: list[str] = []
    stream_defs = {stream["id"]: stream for stream in registry["edge_streams"]}
    for stream_id, state in config["edge_streams"].items():
        if not state["enabled"] or state["weight"] <= 0:
            continue
        definition = stream_defs[stream_id]
        values = external_target_stream_values(
            project,
            target_symbol,
            vector_by_symbol,
            matrix.index,
            definition,
            state,
        )
        if values is None:
            warnings.append(
                f"{definition['label']} has no external-target factor implementation; "
                "it was neutral for target edges."
            )
            continue
        if (values <= 0).any() or not np.isfinite(values).all():
            raise ValueError(f"external-target stream {stream_id} produced invalid factors")
        target_log_odds += float(state["weight"]) * np.log(values)
    target_probabilities = stable_expit(target_log_odds)
    symbols = [*matrix.index.astype(str), target_symbol]
    values = np.zeros((len(symbols), len(symbols)), dtype=float)
    values[:-1, :-1] = matrix.to_numpy(float)
    values[-1, :-1] = target_probabilities
    values[:-1, -1] = target_probabilities
    extended = pd.DataFrame(values, index=symbols, columns=symbols)
    extended.index.name = "symbol"
    return extended, warnings


def supported_edge_table(matrix: pd.DataFrame, cutoff: float) -> pd.DataFrame:
    symbols = matrix.index.astype(str).to_numpy()
    values = matrix.to_numpy(float)
    left, right = np.triu_indices(len(symbols), 1)
    probabilities = values[left, right]
    keep = probabilities > cutoff
    result = pd.DataFrame(
        {
            "node_a": symbols[left[keep]],
            "node_b": symbols[right[keep]],
            "edge_probability": probabilities[keep],
        }
    )
    return result.sort_values("edge_probability", ascending=False).reset_index(drop=True)


def run_paths(
    matrix: pd.DataFrame,
    metadata: pd.DataFrame,
    config: dict[str, Any],
    start_input: str,
    target_symbol: str,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    symbols = matrix.index.astype(str).tolist()
    resolved = resolve_existing_node(
        start_input,
        metadata,
        symbols,
        allow_curated_alias=True,
    )
    if resolved is None:
        raise ValueError(
            f"Starting node {start_input!r} was not selected by the chosen node streams"
        )
    start_symbol, resolution_method = resolved
    if target_symbol not in symbols:
        raise ValueError("target is absent from the constructed graph")
    if start_symbol == target_symbol:
        raise ValueError("start and target resolve to the same node")
    path_config = config["path"]
    eligibility = build_intermediate_eligibility(
        metadata,
        symbols,
        start_symbol,
        target_symbol,
        signaling_intermediates_only=path_config["signaling_intermediates_only"],
        relay_classes=path_config["allowed_intermediate_classes"],
        exclude_any_scaffold=path_config["exclude_multirole_scaffolds"],
    )
    permitted = frozenset(
        eligibility.loc[eligibility["permitted_in_search"], "matrix_index"].astype(int)
    )
    eligible = frozenset(
        eligibility.loc[eligibility["allowed_as_intermediate"], "matrix_index"].astype(int)
    )
    adjacency, retained_edges = build_adjacency(
        matrix, path_config["minimum_edge_probability"]
    )
    symbol_index = {symbol: index for index, symbol in enumerate(symbols)}
    start_index = symbol_index[start_symbol]
    target_index = symbol_index[target_symbol]
    ranked = k_shortest_simple_paths(
        adjacency,
        matrix.to_numpy(float),
        start_index,
        target_index,
        top_k=path_config["top_k"],
        max_hops=path_config["max_hops"],
        permitted_nodes=permitted,
    )
    paths, edges = serialize_paths(ranked, matrix, metadata)
    validation = {
        "all_paths_simple": all(len(item.nodes) == len(set(item.nodes)) for item in ranked),
        "all_paths_within_hop_limit": all(
            len(item.nodes) - 1 <= path_config["max_hops"] for item in ranked
        ),
        "all_internal_nodes_eligible": all(
            all(node in eligible for node in item.nodes[1:-1]) for item in ranked
        ),
        "all_edges_above_path_cutoff": bool(
            edges.empty
            or (edges["edge_probability"] > path_config["minimum_edge_probability"]).all()
        ),
    }
    if not all(validation.values()):
        raise AssertionError(f"path validation failed: {validation}")
    summary = {
        "start_symbol": start_symbol,
        "start_resolution_method": resolution_method,
        "target_symbol": target_symbol,
        "paths_found": int(len(paths)),
        "top_k_requested": int(path_config["top_k"]),
        "maximum_hops": int(path_config["max_hops"]),
        "minimum_edge_probability_exclusive": float(
            path_config["minimum_edge_probability"]
        ),
        "signaling_intermediates_only": bool(
            path_config["signaling_intermediates_only"]
        ),
        "exclude_multirole_scaffolds": bool(
            path_config["exclude_multirole_scaffolds"]
        ),
        "allowed_intermediate_classes": list(
            path_config["allowed_intermediate_classes"]
        ),
        "eligible_intermediate_nodes": int(
            eligibility["allowed_as_intermediate"].sum()
        ),
        "retained_unique_edges_before_intermediate_filter": int(retained_edges),
        "start_component_size": connected_component_size(
            adjacency, start_index, permitted
        ),
        "validation": validation,
    }
    return paths, edges, eligibility, summary


def _records(frame: pd.DataFrame, columns: list[str], limit: int) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    result = frame.loc[:, columns].head(limit).copy()
    return json.loads(result.to_json(orient="records"))


def run_workflow(
    supplied_configuration: dict[str, Any] | None = None,
    *,
    project_root: Path | str = PROJECT_ROOT,
    run_id: str | None = None,
    progress: ProgressCallback | None = None,
) -> WorkflowResult:
    project = Path(project_root).resolve()
    registry = load_registry(project)
    config = normalize_configuration(supplied_configuration, registry)
    run_id = run_id or (
        datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    )
    output = project / RUNS_RELATIVE / safe_name(run_id)
    output.mkdir(parents=True, exist_ok=False)

    def update(message: str, fraction: float) -> None:
        if progress:
            progress(message, fraction)

    try:
        update("Selecting nodes", 0.08)
        node_factors, selected_nodes, node_summary = select_nodes(
            project, registry, config
        )
        warnings: list[str] = []
        universe = pd.read_csv(project / UNIVERSE_RELATIVE, sep="\t", dtype=str).fillna("")
        selected_symbols = selected_nodes["symbol"].astype(str).tolist()

        target_symbol = ""
        target_external = False
        target_resolution: dict[str, Any] | None = None
        graph_symbols = selected_symbols.copy()
        graph_metadata = selected_nodes.copy()
        if config["path"]["enabled"]:
            selected_ci = {symbol.casefold(): symbol for symbol in selected_symbols}
            target_key = config["path"]["target"].casefold()
            if target_key in selected_ci:
                target_symbol = selected_ci[target_key]
            else:
                target_symbol, target_external, target_resolution = resolve_target_for_graph(
                    config["path"]["target"], universe, project
                )
            if target_symbol not in graph_symbols:
                graph_symbols.append(target_symbol)
                if target_external:
                    candidate = node_factors.loc[
                        node_factors["gene_symbol"].str.casefold().eq(target_symbol.casefold())
                    ]
                    target_row = {
                        "symbol": target_symbol,
                        "display_symbol": target_symbol,
                        "name": (
                            str(candidate.iloc[0]["node_name"])
                            if len(candidate)
                            else (target_resolution or {}).get("target_protein_name", "")
                        ),
                        "classes": (
                            str(candidate.iloc[0]["node_classes"])
                            if len(candidate)
                            else "external_target"
                        ),
                        "node_type": "protein",
                        "selected_uniprot": (target_resolution or {}).get("target_uniprot", ""),
                        "gui_selection_reason": "path_endpoint_incremental",
                    }
                else:
                    target_row = universe.loc[universe["symbol"] == target_symbol].iloc[0].to_dict()
                graph_metadata = pd.concat(
                    [graph_metadata, pd.DataFrame([target_row])], ignore_index=True
                )

        seed_symbols = universe["symbol"].astype(str).tolist()
        cache_summary: dict[str, Any] | None = None
        if any(symbol not in set(seed_symbols) for symbol in graph_symbols):
            update("Characterizing and caching new edge pairs", 0.18)
            cache_update = ensure_incremental_pairs(
                project,
                graph_metadata,
                seed_symbols,
                progress=(
                    (lambda message, fraction: update(message, fraction))
                    if progress
                    else None
                ),
            )
            cache_summary = cache_update.as_dict()

        update("Integrating edge evidence", 0.30)
        derived_audits: dict[str, pd.DataFrame] = {}
        matrix, edge_summary = combine_edge_factors(
            project,
            registry,
            config,
            graph_symbols,
            graph_metadata=graph_metadata,
            audit_collector=derived_audits,
        )
        if "scaffold_triadic_closure" in derived_audits:
            warnings.append(
                "Scaffold-mediated closure is derived from the selected pre-closure "
                "edge graph. It is dependent proximity/co-complex evidence, not "
                "independent proof of a direct binary PPI."
            )
        cutoff = config["edge_integration"]["output_probability_cutoff"]
        supported = supported_edge_table(matrix, cutoff)
        path_rows = pd.DataFrame()
        path_edges = pd.DataFrame()
        eligibility = pd.DataFrame()
        path_summary: dict[str, Any] | None = None
        if config["path"]["enabled"]:
            update("Ranking paths", 0.74)
            path_rows, path_edges, eligibility, path_summary = run_paths(
                matrix,
                graph_metadata,
                config,
                config["path"]["start"],
                target_symbol,
            )

        update("Writing reproducible outputs", 0.92)
        config_path = output / "configuration.json"
        node_factors_path = output / "node_posteriors.tsv.gz"
        selected_nodes_path = output / "selected_nodes.tsv"
        matrix_path = output / "edge_adjacency_matrix.tsv"
        supported_path = output / "supported_edges.tsv.gz"
        summary_path = output / "analysis_summary.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        node_factors.to_csv(node_factors_path, sep="\t", index=False, compression="gzip")
        selected_nodes.to_csv(selected_nodes_path, sep="\t", index=False)
        matrix.to_csv(matrix_path, sep="\t", float_format="%.9g")
        supported.to_csv(supported_path, sep="\t", index=False, compression="gzip")
        files = [
            config_path,
            node_factors_path,
            selected_nodes_path,
            matrix_path,
            supported_path,
        ]
        for stream_id, audit in derived_audits.items():
            audit_path = output / f"{safe_name(stream_id)}_audit.tsv.gz"
            audit.to_csv(audit_path, sep="\t", index=False, compression="gzip")
            files.append(audit_path)
        if config["path"]["enabled"]:
            paths_path = output / "ranked_paths.tsv"
            path_edges_path = output / "ranked_path_edges.tsv"
            eligibility_path = output / "intermediate_node_eligibility.tsv"
            path_rows.to_csv(paths_path, sep="\t", index=False, float_format="%.12g")
            path_edges.to_csv(path_edges_path, sep="\t", index=False, float_format="%.12g")
            eligibility.to_csv(eligibility_path, sep="\t", index=False)
            files.extend([paths_path, path_edges_path, eligibility_path])

        summary = {
            "schema_version": 1,
            "run_id": run_id,
            "generated_at": utc_now(),
            "node_selection": node_summary,
            "edge_characterization": {
                **edge_summary,
                "incremental_pair_cache": cache_summary,
                "matrix_node_count_after_target_extension": int(len(matrix)),
                "reported_supported_edge_count": int(len(supported)),
            },
            "path_finding": path_summary,
            "external_target": (
                {
                    "symbol": target_symbol,
                    "resolution": target_resolution,
                    "source_summary": cache_summary,
                }
                if target_external
                else None
            ),
            "warnings": warnings,
            "outputs": [path.name for path in files],
        }
        summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
        preview = {
            "metrics": {
                "selected_nodes": node_summary["selected_node_count"],
                "supported_edges": int(len(supported)),
                "ranked_paths": int(len(path_rows)),
            },
            "top_nodes": _records(
                node_factors.sort_values("gui_posterior", ascending=False),
                ["gene_symbol", "gui_posterior", "gui_rank"],
                10,
            ),
            "top_paths": _records(
                path_rows,
                ["rank", "hop_count", "path_probability_product", "path_symbols"],
                config["path"]["top_k"],
            )
            if not path_rows.empty
            else [],
            "warnings": warnings,
            "files": [path.name for path in [*files, summary_path]],
        }
        update("Complete", 1.0)
        return WorkflowResult(run_id, output, summary, preview)
    except Exception:
        # Keep the run directory and its configuration when possible so a failed
        # scientific run can be audited without overwriting previous results.
        failure_config = output / "configuration.json"
        if not failure_config.exists():
            failure_config.write_text(
                json.dumps(config, indent=2) + "\n", encoding="utf-8"
            )
        raise


if __name__ == "__main__":
    result = run_workflow()
    print(json.dumps(result.preview, indent=2))
    print(f"Output directory: {result.output_directory}")
