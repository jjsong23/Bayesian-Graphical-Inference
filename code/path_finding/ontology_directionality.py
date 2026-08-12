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


def split_classes(value: object) -> set[str]:
    return {token.strip() for token in str(value).split(";") if token.strip()}


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
    cutoff: float,
) -> dict[str, Any]:
    retained = probabilities > cutoff
    total = int(retained.sum())
    oriented = int((retained & uniquely_oriented).sum())
    conflict_count = int((retained & conflicts).sum())
    no_rule = total - oriented - conflict_count
    return {
        "probability_cutoff_exclusive": float(cutoff),
        "retained_unique_edge_count": total,
        "uniquely_oriented_edge_count": oriented,
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
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Return a partially directed propagation matrix and pair-level audit.

    A uniquely matched class rule removes only the disallowed reverse
    traversal. No-rule and contradictory multi-role cases retain both
    directions. Edge probabilities are never increased or re-estimated.
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

    a_to_b = a_to_b_bits != 0
    b_to_a = b_to_a_bits != 0
    oriented_a_to_b = a_to_b & ~b_to_a
    oriented_b_to_a = b_to_a & ~a_to_b
    uniquely_oriented = oriented_a_to_b | oriented_b_to_a
    conflicts = a_to_b & b_to_a

    directed_values = values.copy()
    directed_values[right[oriented_a_to_b], left[oriented_a_to_b]] = 0.0
    directed_values[left[oriented_b_to_a], right[oriented_b_to_a]] = 0.0
    np.fill_diagonal(directed_values, 0.0)
    directed = pd.DataFrame(directed_values, index=symbols, columns=symbols)
    directed.index.name = matrix.index.name or "symbol"

    status = np.full(len(left), "unresolved_no_matching_rule", dtype=object)
    status[conflicts] = "unresolved_conflicting_rules"
    status[oriented_a_to_b] = "oriented_a_to_b"
    status[oriented_b_to_a] = "oriented_b_to_a"
    audit = pd.DataFrame(
        {
            "node_a": np.asarray(symbols, dtype=object)[left],
            "node_b": np.asarray(symbols, dtype=object)[right],
            "edge_probability": probabilities,
            "node_a_classes": aligned["classes"].astype(str).to_numpy()[left],
            "node_b_classes": aligned["classes"].astype(str).to_numpy()[right],
            "directionality_status": status,
            "allowed_a_to_b": ~oriented_b_to_a,
            "allowed_b_to_a": ~oriented_a_to_b,
            "rule_ids_a_to_b": _rule_ids_from_bits(a_to_b_bits, rules),
            "rule_ids_b_to_a": _rule_ids_from_bits(b_to_a_bits, rules),
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
            edge_output_cutoff,
        ),
        "path_graph": _status_summary(
            probabilities,
            uniquely_oriented,
            conflicts,
            path_probability_cutoff,
        ),
        "rule_match_counts": rule_match_counts,
        "conflict_policy": catalog["conflict_policy"],
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
        raise AssertionError(f"ontology directionality validation failed: {summary['validation']}")
    return directed, audit, summary
