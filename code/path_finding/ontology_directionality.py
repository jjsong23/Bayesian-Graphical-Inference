#!/usr/bin/env python3
"""Partially orient a probability graph using auditable ontology-role rules."""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd


RULE_CATALOG_PATH = Path(__file__).with_name("ontology_direction_rules.json")

OMNIPATH_DIRECTION_COLUMNS = [
    "node_a",
    "node_b",
    "directed_record_count",
    "directional_interactions",
    "omnipath_supports_a_to_b",
    "omnipath_supports_b_to_a",
    "omnipath_bidirectional",
    "consensus_direction_record_count",
    "resources",
    "references",
]

KINASE_PREDICTOR_DIRECTION_COLUMNS = [
    "node_a",
    "node_b",
    "kinase_predictor_hit_count",
    "kinase_predictor_supports_a_to_b",
    "kinase_predictor_supports_b_to_a",
    "kinase_predictor_bidirectional",
    "kinase_predictor_hit_count_a_to_b",
    "kinase_predictor_hit_count_b_to_a",
    "kinase_predictor_sites_a_to_b",
    "kinase_predictor_sites_b_to_a",
    "kinase_predictor_labels",
]


def split_classes(value: object) -> set[str]:
    return {token.strip() for token in str(value).split(";") if token.strip()}


def _boolean_series(values: pd.Series) -> pd.Series:
    return values.astype(str).str.strip().str.casefold().isin({"true", "1", "yes"})


def _joined_tokens(values: Iterable[object]) -> str:
    tokens = {
        token.strip()
        for value in values
        for token in str(value).split(";")
        if token.strip() and token.strip().casefold() != "nan"
    }
    return ";".join(sorted(tokens, key=lambda value: (value.casefold(), value)))


def build_omnipath_direction_evidence(
    raw: pd.DataFrame,
    graph_symbols: Iterable[str],
) -> pd.DataFrame:
    """Collapse mapped directed OmniPath rows to one audit row per graph pair.

    Every directed source-target record is retained as directional evidence;
    ``consensus_direction`` is reported for provenance but is not required.
    A pair is unidirectional only when all mapped records agree. Records in
    both directions deliberately preserve both traversals downstream.
    """
    required = {"source_genesymbol", "target_genesymbol", "is_directed"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(
            "OmniPath direction table is missing columns: " + ", ".join(sorted(missing))
        )
    symbols = [str(value) for value in graph_symbols]
    if len(symbols) != len(set(symbols)):
        raise ValueError("OmniPath direction mapping requires unique graph symbols")
    folded: dict[str, str] = {}
    for symbol in symbols:
        key = symbol.casefold()
        if key in folded and folded[key] != symbol:
            raise ValueError(
                f"graph symbols {folded[key]!r} and {symbol!r} collide case-insensitively"
            )
        folded[key] = symbol
    position = {symbol: index for index, symbol in enumerate(symbols)}
    directed = raw.loc[_boolean_series(raw["is_directed"])].copy()
    directed["mapped_source"] = (
        directed["source_genesymbol"].astype(str).str.strip().str.casefold().map(folded)
    )
    directed["mapped_target"] = (
        directed["target_genesymbol"].astype(str).str.strip().str.casefold().map(folded)
    )
    directed = directed.loc[
        directed["mapped_source"].notna()
        & directed["mapped_target"].notna()
        & directed["mapped_source"].ne(directed["mapped_target"])
    ].copy()
    if directed.empty:
        return pd.DataFrame(columns=OMNIPATH_DIRECTION_COLUMNS)

    source_position = directed["mapped_source"].map(position).to_numpy(int)
    target_position = directed["mapped_target"].map(position).to_numpy(int)
    source_first = source_position < target_position
    directed["node_a"] = np.where(
        source_first, directed["mapped_source"], directed["mapped_target"]
    )
    directed["node_b"] = np.where(
        source_first, directed["mapped_target"], directed["mapped_source"]
    )
    directed["record_a_to_b"] = source_first
    directed["record_b_to_a"] = ~source_first
    if "consensus_direction" in directed:
        directed["consensus_direction_bool"] = _boolean_series(
            directed["consensus_direction"]
        )
    else:
        directed["consensus_direction_bool"] = False

    rows: list[dict[str, Any]] = []
    for (node_a, node_b), group in directed.groupby(["node_a", "node_b"], sort=False):
        supports_a_to_b = bool(group["record_a_to_b"].any())
        supports_b_to_a = bool(group["record_b_to_a"].any())
        interactions = []
        if supports_a_to_b:
            interactions.append(f"{node_a}->{node_b}")
        if supports_b_to_a:
            interactions.append(f"{node_b}->{node_a}")
        rows.append(
            {
                "node_a": node_a,
                "node_b": node_b,
                "directed_record_count": int(len(group)),
                "directional_interactions": ";".join(interactions),
                "omnipath_supports_a_to_b": supports_a_to_b,
                "omnipath_supports_b_to_a": supports_b_to_a,
                "omnipath_bidirectional": supports_a_to_b and supports_b_to_a,
                "consensus_direction_record_count": int(
                    group["consensus_direction_bool"].sum()
                ),
                "resources": _joined_tokens(group.get("sources", pd.Series(dtype=object))),
                "references": _joined_tokens(
                    group.get("references", pd.Series(dtype=object))
                ),
            }
        )
    return pd.DataFrame(rows, columns=OMNIPATH_DIRECTION_COLUMNS)


def build_kinase_predictor_direction_evidence(
    predictions: pd.DataFrame,
    graph_symbols: Iterable[str],
) -> pd.DataFrame:
    """Collapse supported kinase-to-substrate records to graph-pair directions.

    ``predictions`` must already be restricted to records whose *applied*
    KinasePredictor evidence supports the relationship under the run's Tq and
    negative-evidence settings. The canonical pair remains undirected for
    edge-existence inference, while ``kinase_node -> target_protein`` supplies
    a hard traversal constraint to path finding.
    """
    required = {"kinase_node", "target_protein"}
    missing = required.difference(predictions.columns)
    if missing:
        raise ValueError(
            "KinasePredictor direction records are missing columns: "
            + ", ".join(sorted(missing))
        )
    symbols = [str(value) for value in graph_symbols]
    if len(symbols) != len(set(symbols)):
        raise ValueError("KinasePredictor direction mapping requires unique graph symbols")
    folded: dict[str, str] = {}
    for symbol in symbols:
        key = symbol.casefold()
        if key in folded and folded[key] != symbol:
            raise ValueError(
                f"graph symbols {folded[key]!r} and {symbol!r} collide case-insensitively"
            )
        folded[key] = symbol
    position = {symbol: index for index, symbol in enumerate(symbols)}
    directed = predictions.copy()
    directed["mapped_kinase"] = (
        directed["kinase_node"].astype(str).str.strip().str.casefold().map(folded)
    )
    directed["mapped_target"] = (
        directed["target_protein"].astype(str).str.strip().str.casefold().map(folded)
    )
    directed = directed.loc[
        directed["mapped_kinase"].notna()
        & directed["mapped_target"].notna()
        & directed["mapped_kinase"].ne(directed["mapped_target"])
    ].copy()
    if directed.empty:
        return pd.DataFrame(columns=KINASE_PREDICTOR_DIRECTION_COLUMNS)

    kinase_position = directed["mapped_kinase"].map(position).to_numpy(int)
    target_position = directed["mapped_target"].map(position).to_numpy(int)
    kinase_first = kinase_position < target_position
    directed["node_a"] = np.where(
        kinase_first, directed["mapped_kinase"], directed["mapped_target"]
    )
    directed["node_b"] = np.where(
        kinase_first, directed["mapped_target"], directed["mapped_kinase"]
    )
    directed["record_a_to_b"] = kinase_first
    directed["record_b_to_a"] = ~kinase_first

    rows: list[dict[str, Any]] = []
    for (node_a, node_b), group in directed.groupby(["node_a", "node_b"], sort=False):
        a_to_b = group.loc[group["record_a_to_b"]]
        b_to_a = group.loc[group["record_b_to_a"]]
        supports_a_to_b = not a_to_b.empty
        supports_b_to_a = not b_to_a.empty
        rows.append(
            {
                "node_a": node_a,
                "node_b": node_b,
                "kinase_predictor_hit_count": int(len(group)),
                "kinase_predictor_supports_a_to_b": supports_a_to_b,
                "kinase_predictor_supports_b_to_a": supports_b_to_a,
                "kinase_predictor_bidirectional": supports_a_to_b and supports_b_to_a,
                "kinase_predictor_hit_count_a_to_b": int(len(a_to_b)),
                "kinase_predictor_hit_count_b_to_a": int(len(b_to_a)),
                "kinase_predictor_sites_a_to_b": _joined_tokens(
                    a_to_b.get("site", pd.Series(dtype=object))
                ),
                "kinase_predictor_sites_b_to_a": _joined_tokens(
                    b_to_a.get("site", pd.Series(dtype=object))
                ),
                "kinase_predictor_labels": _joined_tokens(
                    group.get("predictor_label", pd.Series(dtype=object))
                ),
            }
        )
    return pd.DataFrame(rows, columns=KINASE_PREDICTOR_DIRECTION_COLUMNS)


def load_direction_rule_catalog(
    path: Path | str = RULE_CATALOG_PATH,
    *,
    known_classes: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Load and validate the versioned ontology-direction policy."""
    resolved = Path(path).resolve()
    catalog = json.loads(resolved.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != 1:
        raise ValueError("unsupported ontology-direction rule schema")
    rules = catalog.get("rules")
    if not isinstance(rules, list) or not rules:
        raise ValueError("ontology-direction rule catalog has no rules")
    rule_ids = [str(rule.get("id", "")).strip() for rule in rules]
    if any(not rule_id for rule_id in rule_ids) or len(rule_ids) != len(set(rule_ids)):
        raise ValueError("ontology-direction rule IDs are missing or duplicated")
    for rule in rules:
        if not str(rule.get("source_class", "")).strip():
            raise ValueError(f"rule {rule.get('id')} has no source_class")
        if not str(rule.get("target_class", "")).strip():
            raise ValueError(f"rule {rule.get('id')} has no target_class")
        if rule["source_class"] == rule["target_class"]:
            raise ValueError(f"rule {rule['id']} cannot orient a class toward itself")
    if len(rules) > 63:
        raise ValueError("ontology-direction implementation supports at most 63 rules")
    catalog_classes = catalog.get("ontology_classes")
    if (
        not isinstance(catalog_classes, list)
        or not catalog_classes
        or len(catalog_classes) != len(set(catalog_classes))
    ):
        raise ValueError("ontology-direction catalog has invalid ontology_classes")
    if known_classes is not None:
        known = {str(value) for value in known_classes}
        used = {
            str(rule[field])
            for rule in rules
            for field in ("source_class", "target_class")
        }
        unknown = used.difference(known)
        if unknown:
            raise ValueError(
                "ontology-direction rules use unknown classes: "
                + ", ".join(sorted(unknown))
            )
        missing_catalog_classes = set(catalog_classes).difference(known)
        if missing_catalog_classes:
            raise ValueError(
                "ontology-direction catalog lists unknown classes: "
                + ", ".join(sorted(missing_catalog_classes))
            )
    catalog["catalog_path"] = str(resolved)
    return catalog


def _matching_rule_ids(
    source_classes: set[str],
    target_classes: set[str],
    rules: list[dict[str, Any]],
) -> list[str]:
    return [
        str(rule["id"])
        for rule in rules
        if rule["source_class"] in source_classes
        and rule["target_class"] in target_classes
    ]


def build_complete_class_pair_catalog(
    catalog: dict[str, Any],
    ontology_classes: Iterable[str],
) -> pd.DataFrame:
    """Enumerate every unordered ontology-class pair and its rule outcome."""
    classes = sorted({str(value).strip() for value in ontology_classes if str(value).strip()})
    rules = catalog["rules"]
    rows: list[dict[str, Any]] = []
    for class_a, class_b in itertools.combinations_with_replacement(classes, 2):
        a_to_b = _matching_rule_ids({class_a}, {class_b}, rules)
        b_to_a = _matching_rule_ids({class_b}, {class_a}, rules)
        if a_to_b and not b_to_a:
            outcome = "class_a_to_class_b"
        elif b_to_a and not a_to_b:
            outcome = "class_b_to_class_a"
        elif a_to_b and b_to_a:
            outcome = "unresolved_conflicting_rules"
        else:
            outcome = "unresolved_no_matching_rule"
        rows.append(
            {
                "class_a": class_a,
                "class_b": class_b,
                "catalog_outcome": outcome,
                "rule_ids_a_to_b": ";".join(a_to_b),
                "rule_ids_b_to_a": ";".join(b_to_a),
            }
        )
    return pd.DataFrame(rows)


def _rule_ids_from_bits(bits: np.ndarray, rules: list[dict[str, Any]]) -> np.ndarray:
    lookup = {
        int(value): ";".join(
            str(rule["id"])
            for index, rule in enumerate(rules)
            if int(value) & (1 << index)
        )
        for value in np.unique(bits)
    }
    return np.asarray([lookup[int(value)] for value in bits], dtype=object)


def _status_summary(
    probabilities: np.ndarray,
    uniquely_oriented: np.ndarray,
    conflicts: np.ndarray,
    ontology_a_to_b: np.ndarray,
    ontology_b_to_a: np.ndarray,
    kinase_a_to_b: np.ndarray,
    kinase_b_to_a: np.ndarray,
    omnipath_a_to_b: np.ndarray,
    omnipath_b_to_a: np.ndarray,
    cutoff: float,
) -> dict[str, Any]:
    retained = probabilities > cutoff
    total = int(retained.sum())
    oriented = int((retained & uniquely_oriented).sum())
    conflict_count = int((retained & conflicts).sum())
    no_rule = total - oriented - conflict_count
    ontology_unique = ontology_a_to_b ^ ontology_b_to_a
    kinase_unique = kinase_a_to_b ^ kinase_b_to_a
    omnipath_unique = omnipath_a_to_b ^ omnipath_b_to_a
    sources_agree = ontology_unique & omnipath_unique & (
        (ontology_a_to_b & omnipath_a_to_b)
        | (ontology_b_to_a & omnipath_b_to_a)
    )
    sources_oppose = ontology_unique & omnipath_unique & ~sources_agree
    ontology_kinase_agree = ontology_unique & kinase_unique & (
        (ontology_a_to_b & kinase_a_to_b)
        | (ontology_b_to_a & kinase_b_to_a)
    )
    ontology_kinase_oppose = ontology_unique & kinase_unique & ~ontology_kinase_agree
    kinase_omnipath_agree = kinase_unique & omnipath_unique & (
        (kinase_a_to_b & omnipath_a_to_b)
        | (kinase_b_to_a & omnipath_b_to_a)
    )
    kinase_omnipath_oppose = kinase_unique & omnipath_unique & ~kinase_omnipath_agree
    oriented_ontology_only = int(
        (retained & uniquely_oriented & ontology_unique & ~sources_agree).sum()
    )
    oriented_omnipath_only = int(
        (
            retained
            & uniquely_oriented
            & ~ontology_unique
            & ~kinase_unique
            & omnipath_unique
        ).sum()
    )
    oriented_by_both = int(
        (retained & uniquely_oriented & sources_agree).sum()
    )
    return {
        "probability_cutoff_exclusive": float(cutoff),
        "retained_unique_edge_count": total,
        "uniquely_oriented_edge_count": oriented,
        "uniquely_oriented_by_ontology_only_count": oriented_ontology_only,
        "uniquely_oriented_by_kinase_predictor_only_count": int(
            (
                retained
                & uniquely_oriented
                & ~ontology_unique
                & kinase_unique
                & ~kinase_omnipath_agree
            ).sum()
        ),
        "uniquely_oriented_by_omnipath_only_count": oriented_omnipath_only,
        "uniquely_oriented_by_both_count": oriented_by_both,
        "uniquely_oriented_with_kinase_predictor_count": int(
            (retained & uniquely_oriented & kinase_unique).sum()
        ),
        "kinase_predictor_direction_pair_count": int(
            (retained & (kinase_a_to_b | kinase_b_to_a)).sum()
        ),
        "kinase_predictor_bidirectional_pair_count": int(
            (retained & kinase_a_to_b & kinase_b_to_a).sum()
        ),
        "ontology_precedence_over_opposing_omnipath_count": int(
            (retained & sources_oppose).sum()
        ),
        "ontology_precedence_over_opposing_kinase_predictor_count": int(
            (retained & ontology_kinase_oppose).sum()
        ),
        "kinase_predictor_precedence_over_opposing_omnipath_count": int(
            (retained & ~ontology_unique & kinase_omnipath_oppose).sum()
        ),
        "unresolved_no_direction_evidence_count": no_rule,
        "unresolved_conflicting_direction_count": conflict_count,
        # Backward-compatible aliases retained for older GUI/result readers.
        "unresolved_no_matching_rule_count": no_rule,
        "unresolved_conflicting_rules_count": conflict_count,
        "proportion_uniquely_oriented": oriented / total if total else 0.0,
        "proportion_unresolved": (total - oriented) / total if total else 0.0,
        "directed_traversal_count_after_partial_orientation": (
            oriented + 2 * (total - oriented)
        ),
        "reverse_traversals_removed": oriented,
    }


def apply_ontology_directionality(
    matrix: pd.DataFrame,
    metadata: pd.DataFrame,
    catalog: dict[str, Any],
    *,
    audit_probability_cutoff: float,
    edge_output_cutoff: float,
    path_probability_cutoff: float,
    omnipath_directions: pd.DataFrame | None = None,
    kinase_predictor_directions: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Return a partially directed propagation matrix and pair-level audit.

    A unique ontology direction removes the disallowed reverse traversal.
    A supported KinasePredictor kinase-to-substrate direction may orient a pair
    that ontology left unresolved, and OmniPath may orient a pair left
    unresolved by both earlier layers. Missing and remaining bidirectional
    cases retain both directions. Edge probabilities are never increased or
    re-estimated.
    """
    if matrix.shape[0] != matrix.shape[1]:
        raise ValueError("ontology directionality requires a square matrix")
    symbols = matrix.index.astype(str).tolist()
    if symbols != matrix.columns.astype(str).tolist():
        raise ValueError("ontology directionality matrix labels are not aligned")
    values = matrix.to_numpy(float)
    if not np.array_equal(values, values.T):
        raise ValueError("ontology directionality requires a symmetric input matrix")
    if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError("ontology directionality received invalid probabilities")

    aligned = metadata.copy().fillna("")
    if "symbol" not in aligned or "classes" not in aligned:
        raise ValueError("ontology directionality metadata requires symbol and classes")
    aligned["symbol"] = aligned["symbol"].astype(str)
    aligned = aligned.drop_duplicates("symbol", keep="last").set_index("symbol")
    aligned = aligned.reindex(symbols).fillna("")
    class_sets = [split_classes(value) for value in aligned["classes"]]
    all_classes = sorted({item for values_set in class_sets for item in values_set})
    class_masks = {
        ontology_class: np.asarray(
            [ontology_class in values_set for values_set in class_sets], dtype=bool
        )
        for ontology_class in all_classes
    }

    left, right = np.triu_indices(len(symbols), 1)
    probabilities = values[left, right]
    audited = probabilities > audit_probability_cutoff
    left = left[audited]
    right = right[audited]
    probabilities = probabilities[audited]
    rules = catalog["rules"]
    a_to_b_bits = np.zeros(len(left), dtype=np.uint64)
    b_to_a_bits = np.zeros(len(left), dtype=np.uint64)
    empty_mask = np.zeros(len(symbols), dtype=bool)
    for index, rule in enumerate(rules):
        source = class_masks.get(rule["source_class"], empty_mask)
        target = class_masks.get(rule["target_class"], empty_mask)
        bit = np.uint64(1 << index)
        a_to_b_bits[source[left] & target[right]] |= bit
        b_to_a_bits[source[right] & target[left]] |= bit

    ontology_a_to_b = a_to_b_bits != 0
    ontology_b_to_a = b_to_a_bits != 0
    kinase_a_to_b = np.zeros(len(left), dtype=bool)
    kinase_b_to_a = np.zeros(len(left), dtype=bool)
    kinase_hit_count = np.zeros(len(left), dtype=np.int64)
    kinase_hit_count_a_to_b = np.zeros(len(left), dtype=np.int64)
    kinase_hit_count_b_to_a = np.zeros(len(left), dtype=np.int64)
    kinase_sites_a_to_b = np.full(len(left), "", dtype=object)
    kinase_sites_b_to_a = np.full(len(left), "", dtype=object)
    kinase_labels = np.full(len(left), "", dtype=object)
    kinase_pair_count = 0
    omnipath_a_to_b = np.zeros(len(left), dtype=bool)
    omnipath_b_to_a = np.zeros(len(left), dtype=bool)
    omnipath_record_count = np.zeros(len(left), dtype=np.int64)
    omnipath_consensus_count = np.zeros(len(left), dtype=np.int64)
    omnipath_interactions = np.full(len(left), "", dtype=object)
    omnipath_resources = np.full(len(left), "", dtype=object)
    omnipath_references = np.full(len(left), "", dtype=object)
    omnipath_pair_count = 0
    symbol_values = np.asarray(symbols, dtype=object)
    if kinase_predictor_directions is not None:
        required = {
            "node_a",
            "node_b",
            "kinase_predictor_supports_a_to_b",
            "kinase_predictor_supports_b_to_a",
        }
        missing = required.difference(kinase_predictor_directions.columns)
        if missing:
            raise ValueError(
                "KinasePredictor direction evidence is missing columns: "
                + ", ".join(sorted(missing))
            )
        kinase_lookup = {
            (str(row.node_a), str(row.node_b)): row
            for row in kinase_predictor_directions.itertuples(index=False)
        }
        for pair_index, (left_index, right_index) in enumerate(zip(left, right)):
            evidence = kinase_lookup.get(
                (str(symbol_values[left_index]), str(symbol_values[right_index]))
            )
            if evidence is None:
                continue
            kinase_pair_count += 1
            kinase_a_to_b[pair_index] = bool(
                evidence.kinase_predictor_supports_a_to_b
            )
            kinase_b_to_a[pair_index] = bool(
                evidence.kinase_predictor_supports_b_to_a
            )
            kinase_hit_count[pair_index] = int(
                getattr(evidence, "kinase_predictor_hit_count", 0)
            )
            kinase_hit_count_a_to_b[pair_index] = int(
                getattr(evidence, "kinase_predictor_hit_count_a_to_b", 0)
            )
            kinase_hit_count_b_to_a[pair_index] = int(
                getattr(evidence, "kinase_predictor_hit_count_b_to_a", 0)
            )
            kinase_sites_a_to_b[pair_index] = str(
                getattr(evidence, "kinase_predictor_sites_a_to_b", "")
            )
            kinase_sites_b_to_a[pair_index] = str(
                getattr(evidence, "kinase_predictor_sites_b_to_a", "")
            )
            kinase_labels[pair_index] = str(
                getattr(evidence, "kinase_predictor_labels", "")
            )
    if omnipath_directions is not None:
        required = {
            "node_a",
            "node_b",
            "omnipath_supports_a_to_b",
            "omnipath_supports_b_to_a",
        }
        missing = required.difference(omnipath_directions.columns)
        if missing:
            raise ValueError(
                "OmniPath direction evidence is missing columns: "
                + ", ".join(sorted(missing))
            )
        evidence_lookup = {
            (str(row.node_a), str(row.node_b)): row
            for row in omnipath_directions.itertuples(index=False)
        }
        for pair_index, (left_index, right_index) in enumerate(zip(left, right)):
            evidence = evidence_lookup.get(
                (str(symbol_values[left_index]), str(symbol_values[right_index]))
            )
            if evidence is None:
                continue
            omnipath_pair_count += 1
            omnipath_a_to_b[pair_index] = bool(evidence.omnipath_supports_a_to_b)
            omnipath_b_to_a[pair_index] = bool(evidence.omnipath_supports_b_to_a)
            omnipath_record_count[pair_index] = int(
                getattr(evidence, "directed_record_count", 0)
            )
            omnipath_consensus_count[pair_index] = int(
                getattr(evidence, "consensus_direction_record_count", 0)
            )
            omnipath_interactions[pair_index] = str(
                getattr(evidence, "directional_interactions", "")
            )
            omnipath_resources[pair_index] = str(getattr(evidence, "resources", ""))
            omnipath_references[pair_index] = str(getattr(evidence, "references", ""))

    ontology_unique_a_to_b = ontology_a_to_b & ~ontology_b_to_a
    ontology_unique_b_to_a = ontology_b_to_a & ~ontology_a_to_b
    ontology_unique = ontology_unique_a_to_b | ontology_unique_b_to_a
    kinase_unique_a_to_b = kinase_a_to_b & ~kinase_b_to_a
    kinase_unique_b_to_a = kinase_b_to_a & ~kinase_a_to_b
    kinase_unique = kinase_unique_a_to_b | kinase_unique_b_to_a
    omnipath_unique_a_to_b = omnipath_a_to_b & ~omnipath_b_to_a
    omnipath_unique_b_to_a = omnipath_b_to_a & ~omnipath_a_to_b
    omnipath_unique = omnipath_unique_a_to_b | omnipath_unique_b_to_a
    # Preserve the established ontology policy, then apply the explicit
    # kinase-to-substrate relationship, then use OmniPath for pairs still left
    # unresolved. Each later source may add a restriction but cannot reopen or
    # reverse a traversal removed by an earlier source.
    oriented_a_to_b = ontology_unique_a_to_b | (
        ~ontology_unique & kinase_unique_a_to_b
    ) | (
        ~ontology_unique & ~kinase_unique & omnipath_unique_a_to_b
    )
    oriented_b_to_a = ontology_unique_b_to_a | (
        ~ontology_unique & kinase_unique_b_to_a
    ) | (
        ~ontology_unique & ~kinase_unique & omnipath_unique_b_to_a
    )
    uniquely_oriented = oriented_a_to_b | oriented_b_to_a
    conflicts = ~uniquely_oriented & (
        (ontology_a_to_b & ontology_b_to_a)
        | (kinase_a_to_b & kinase_b_to_a)
        | (omnipath_a_to_b & omnipath_b_to_a)
    )

    directed_values = values.copy()
    directed_values[right[oriented_a_to_b], left[oriented_a_to_b]] = 0.0
    directed_values[left[oriented_b_to_a], right[oriented_b_to_a]] = 0.0
    np.fill_diagonal(directed_values, 0.0)
    directed = pd.DataFrame(directed_values, index=symbols, columns=symbols)
    directed.index.name = matrix.index.name or "symbol"

    status = np.full(len(left), "unresolved_no_direction_evidence", dtype=object)
    status[conflicts] = "unresolved_conflicting_directions"
    status[oriented_a_to_b] = "oriented_a_to_b"
    status[oriented_b_to_a] = "oriented_b_to_a"
    ontology_any = ontology_a_to_b | ontology_b_to_a
    kinase_any = kinase_a_to_b | kinase_b_to_a
    omnipath_any = omnipath_a_to_b | omnipath_b_to_a
    sources_agree = ontology_unique & omnipath_unique & (
        (ontology_unique_a_to_b & omnipath_unique_a_to_b)
        | (ontology_unique_b_to_a & omnipath_unique_b_to_a)
    )
    sources_oppose = ontology_unique & omnipath_unique & ~sources_agree
    ontology_kinase_agree = ontology_unique & kinase_unique & (
        (ontology_unique_a_to_b & kinase_unique_a_to_b)
        | (ontology_unique_b_to_a & kinase_unique_b_to_a)
    )
    ontology_kinase_oppose = ontology_unique & kinase_unique & ~ontology_kinase_agree
    kinase_omnipath_agree = kinase_unique & omnipath_unique & (
        (kinase_unique_a_to_b & omnipath_unique_a_to_b)
        | (kinase_unique_b_to_a & omnipath_unique_b_to_a)
    )
    kinase_omnipath_oppose = kinase_unique & omnipath_unique & ~kinase_omnipath_agree
    orientation_source = np.full(len(left), "none", dtype=object)
    orientation_source[ontology_unique] = "ontology"
    orientation_source[~ontology_unique & kinase_unique] = "kinase_predictor"
    orientation_source[~ontology_unique & ~kinase_unique & omnipath_unique] = "omnipath"
    orientation_source[sources_agree] = "ontology;omnipath_agree"
    orientation_source[sources_oppose] = (
        "ontology_precedence_over_omnipath_conflict"
    )
    orientation_source[ontology_kinase_agree] = "ontology;kinase_predictor_agree"
    orientation_source[ontology_kinase_oppose] = (
        "ontology_precedence_over_kinase_predictor_conflict"
    )
    orientation_source[
        ~ontology_unique & kinase_omnipath_agree
    ] = "kinase_predictor;omnipath_agree"
    orientation_source[
        ~ontology_unique & kinase_omnipath_oppose
    ] = "kinase_predictor_precedence_over_omnipath_conflict"
    orientation_source[
        ~uniquely_oriented & (ontology_any | kinase_any | omnipath_any)
    ] = (
        "unresolved_direction_conflict"
    )
    audit = pd.DataFrame(
        {
            "node_a": np.asarray(symbols, dtype=object)[left],
            "node_b": np.asarray(symbols, dtype=object)[right],
            "edge_probability": probabilities,
            "node_a_classes": aligned["classes"].astype(str).to_numpy()[left],
            "node_b_classes": aligned["classes"].astype(str).to_numpy()[right],
            "directionality_status": status,
            "direction_evidence_sources": orientation_source,
            "ontology_omnipath_direction_conflict": sources_oppose,
            "ontology_kinase_predictor_direction_conflict": ontology_kinase_oppose,
            "kinase_predictor_omnipath_direction_conflict": kinase_omnipath_oppose,
            "allowed_a_to_b": ~oriented_b_to_a,
            "allowed_b_to_a": ~oriented_a_to_b,
            "ontology_supports_a_to_b": ontology_a_to_b,
            "ontology_supports_b_to_a": ontology_b_to_a,
            "rule_ids_a_to_b": _rule_ids_from_bits(a_to_b_bits, rules),
            "rule_ids_b_to_a": _rule_ids_from_bits(b_to_a_bits, rules),
            "kinase_predictor_supports_a_to_b": kinase_a_to_b,
            "kinase_predictor_supports_b_to_a": kinase_b_to_a,
            "kinase_predictor_hit_count": kinase_hit_count,
            "kinase_predictor_hit_count_a_to_b": kinase_hit_count_a_to_b,
            "kinase_predictor_hit_count_b_to_a": kinase_hit_count_b_to_a,
            "kinase_predictor_sites_a_to_b": kinase_sites_a_to_b,
            "kinase_predictor_sites_b_to_a": kinase_sites_b_to_a,
            "kinase_predictor_labels": kinase_labels,
            "omnipath_supports_a_to_b": omnipath_a_to_b,
            "omnipath_supports_b_to_a": omnipath_b_to_a,
            "omnipath_directed_record_count": omnipath_record_count,
            "omnipath_consensus_direction_record_count": omnipath_consensus_count,
            "omnipath_directional_interactions": omnipath_interactions,
            "omnipath_resources": omnipath_resources,
            "omnipath_references": omnipath_references,
            "above_edge_output_cutoff": probabilities > edge_output_cutoff,
            "above_path_probability_cutoff": probabilities > path_probability_cutoff,
        }
    )
    rule_match_counts = []
    for index, rule in enumerate(rules):
        bit = np.uint64(1 << index)
        rule_match_counts.append(
            {
                "rule_id": rule["id"],
                "source_class": rule["source_class"],
                "target_class": rule["target_class"],
                "candidate_a_to_b_matches": int(((a_to_b_bits & bit) != 0).sum()),
                "candidate_b_to_a_matches": int(((b_to_a_bits & bit) != 0).sum()),
                "pairs_where_rule_contributes": int(
                    (((a_to_b_bits & bit) != 0) | ((b_to_a_bits & bit) != 0)).sum()
                ),
            }
        )
    summary = {
        "enabled": True,
        "schema_version": catalog["schema_version"],
        "rule_catalog_name": catalog["name"],
        "rule_count": len(rules),
        "audit_probability_cutoff_exclusive": float(audit_probability_cutoff),
        "audited_unique_edge_count": int(len(audit)),
        "edge_output_graph": _status_summary(
            probabilities,
            uniquely_oriented,
            conflicts,
            ontology_a_to_b,
            ontology_b_to_a,
            kinase_a_to_b,
            kinase_b_to_a,
            omnipath_a_to_b,
            omnipath_b_to_a,
            edge_output_cutoff,
        ),
        "path_graph": _status_summary(
            probabilities,
            uniquely_oriented,
            conflicts,
            ontology_a_to_b,
            ontology_b_to_a,
            kinase_a_to_b,
            kinase_b_to_a,
            omnipath_a_to_b,
            omnipath_b_to_a,
            path_probability_cutoff,
        ),
        "omnipath_directionality_enabled": omnipath_directions is not None,
        "kinase_predictor_directionality_enabled": (
            kinase_predictor_directions is not None
        ),
        "kinase_predictor_mapped_pair_count_in_graph": int(
            len(kinase_predictor_directions)
        )
        if kinase_predictor_directions is not None
        else 0,
        "kinase_predictor_mapped_pair_count_above_audit_cutoff": int(
            kinase_pair_count
        ),
        "omnipath_mapped_pair_count_in_graph": int(len(omnipath_directions))
        if omnipath_directions is not None
        else 0,
        "omnipath_mapped_pair_count_above_audit_cutoff": int(omnipath_pair_count),
        "rule_match_counts": rule_match_counts,
        "conflict_policy": (
            "Unique ontology orientations are retained as the established project "
            "policy. Supported KinasePredictor kinase-to-substrate records uniquely "
            "orient pairs that ontology left unresolved. OmniPath uniquely orients "
            "only pairs that both earlier layers left unresolved. A later source can "
            "therefore add a disallowed reverse traversal but cannot reopen or reverse "
            "an earlier constraint. Opposing source directions are recorded in the "
            "audit. Bidirectional evidence does not orient an otherwise unresolved "
            "pair."
        ),
        "sign_policy": catalog["sign_policy"],
        "matrix_semantics": (
            "The original undirected edge probability is retained for every allowed "
            "traversal. A uniquely disallowed reverse traversal is encoded as zero. "
            "Unresolved pairs remain symmetric."
        ),
        "validation": {
            "input_was_exactly_symmetric": True,
            "no_probability_was_increased": bool((directed_values <= values + 1e-15).all()),
            "all_nonzero_directed_values_equal_original_pair_probability": bool(
                np.all((directed_values == 0.0) | np.isclose(directed_values, values))
            ),
            "diagonal_is_zero": bool(np.array_equal(np.diag(directed_values), np.zeros(len(symbols)))),
        },
    }
    if not all(summary["validation"].values()):
        raise AssertionError(f"directionality validation failed: {summary['validation']}")
    return directed, audit, summary
