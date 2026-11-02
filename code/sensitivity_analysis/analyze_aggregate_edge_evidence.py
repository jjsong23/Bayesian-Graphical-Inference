#!/usr/bin/env python3
"""Pool curated edge references and calculate an all-stream total BF per pair."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUI_ROOT = PROJECT_ROOT / "gui"
if str(GUI_ROOT) not in sys.path:
    sys.path.insert(0, str(GUI_ROOT))

from workflow_engine import (  # noqa: E402
    combine_edge_factors,
    default_configuration,
    load_registry,
)


TRAINING_FILE = (
    PROJECT_ROOT
    / "gui"
    / "calibration"
    / "positive_cases_20261028"
    / "curated_edge_likelihood_training.tsv"
)
DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "results"
    / "likelihood_calibration"
    / "aggregate_edge_evidence_20261030"
)


def _information(p1: np.ndarray, p0: np.ndarray) -> dict[str, float]:
    midpoint = (p1 + p0) / 2.0
    return {
        "jensen_shannon_bits": float(
            0.5 * np.sum(p1 * np.log2(p1 / midpoint))
            + 0.5 * np.sum(p0 * np.log2(p0 / midpoint))
        ),
        "kl_h1_h0_bits": float(np.sum(p1 * np.log2(p1 / p0))),
        "kl_h0_h1_bits": float(np.sum(p0 * np.log2(p0 / p1))),
        "total_variation": float(0.5 * np.sum(np.abs(p1 - p0))),
    }


def _histogram_model(
    h1_values: np.ndarray,
    confirmed_h0_values: np.ndarray,
    background_h0_values: np.ndarray,
    *,
    confirmed_mixture_weight: float,
    smoothing: float = 0.5,
    bins: int = 20,
) -> dict[str, object]:
    confirmed_mixture_weight = float(confirmed_mixture_weight)
    if not 0.0 <= confirmed_mixture_weight <= 1.0:
        raise ValueError("confirmed_mixture_weight must be between 0 and 1")
    h0_parts = []
    h0_weight_parts = []
    confirmed_count = len(confirmed_h0_values)
    background_count = len(background_h0_values)
    total_count = confirmed_count + background_count
    confirmed_effective_weight = 0.0
    background_effective_weight = 0.0
    if confirmed_count and background_count:
        if confirmed_mixture_weight > 0.0:
            confirmed_effective_weight = confirmed_mixture_weight * total_count
            h0_parts.append(confirmed_h0_values)
            h0_weight_parts.append(
                np.full(
                    confirmed_count,
                    confirmed_effective_weight / confirmed_count,
                )
            )
        if confirmed_mixture_weight < 1.0:
            background_effective_weight = (
                1.0 - confirmed_mixture_weight
            ) * total_count
            h0_parts.append(background_h0_values)
            h0_weight_parts.append(
                np.full(
                    background_count,
                    background_effective_weight / background_count,
                )
            )
    elif confirmed_count:
        confirmed_effective_weight = float(confirmed_count)
        h0_parts.append(confirmed_h0_values)
        h0_weight_parts.append(np.ones(confirmed_count))
    elif background_count:
        background_effective_weight = float(background_count)
        h0_parts.append(background_h0_values)
        h0_weight_parts.append(np.ones(background_count))
    else:
        raise ValueError("At least one H0 reference group is required")
    h0_values = np.concatenate(h0_parts)
    h0_weights = np.concatenate(h0_weight_parts)
    pooled = np.concatenate([h1_values, h0_values])
    lower, upper = np.quantile(pooled[np.isfinite(pooled)], [0.01, 0.99])
    if math.isclose(float(lower), float(upper)):
        lower, upper = float(pooled.min()) - 0.5, float(pooled.max()) + 0.5
    interior = np.linspace(float(lower), float(upper), bins - 1)
    edges = np.concatenate(([-np.inf], interior, [np.inf]))
    h1_counts, _ = np.histogram(h1_values, bins=edges)
    h0_counts, _ = np.histogram(h0_values, bins=edges)
    h1_bin_weights = h1_counts.astype(float)
    h0_bin_weights, _ = np.histogram(
        h0_values, bins=edges, weights=h0_weights
    )
    p1 = (h1_bin_weights + smoothing) / (
        h1_bin_weights.sum() + smoothing * bins
    )
    p0 = (h0_bin_weights + smoothing) / (
        h0_bin_weights.sum() + smoothing * bins
    )
    return {
        "edges_log2_total_bf": [
            None if not math.isfinite(float(value)) else float(value)
            for value in edges
        ],
        "h1_counts": h1_counts.astype(int).tolist(),
        "h0_counts": h0_counts.astype(int).tolist(),
        "h1_bin_weights": h1_bin_weights.tolist(),
        "h0_bin_weights": h0_bin_weights.tolist(),
        "p_data_given_h1": p1.tolist(),
        "p_data_given_h0": p0.tolist(),
        "effective_h0_weight": float(h0_bin_weights.sum()),
        "confirmed_h0_count": int(confirmed_count),
        "background_h0_count": int(background_count),
        "confirmed_h0_effective_weight": confirmed_effective_weight,
        "background_h0_effective_weight": background_effective_weight,
        **_information(p1, p0),
    }


def _pooled_reference_pairs(training: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for (node_a, node_b), group in training.groupby(
        ["node_a", "node_b"], sort=True
    ):
        labels = set(group["class_label"].astype(str))
        has_h1 = any(label.startswith("H1") for label in labels)
        h0_rows = group[group["class_label"].astype(str).str.startswith("H0")]
        explicit_rows = h0_rows[
            h0_rows["negative_kind"].astype(str).eq("explicit_tested_null")
        ]
        pooled_class = "H1" if has_h1 else "H0"
        rows.append(
            {
                "node_a": str(node_a),
                "node_b": str(node_b),
                "pooled_reference_class": pooled_class,
                "profiles": ";".join(sorted(set(group["profile"].astype(str)))),
                "source_labels": ";".join(sorted(labels)),
                "h1_h0_cross_profile_conflict": bool(
                    has_h1 and not h0_rows.empty
                ),
                "explicit_h0": bool(
                    pooled_class == "H0" and not explicit_rows.empty
                ),
                "confirmed_h0": bool(
                    pooled_class == "H0"
                    and not explicit_rows.empty
                ),
                "outside_cd_explicit_h0": bool(
                    pooled_class == "H0"
                    and not explicit_rows.empty
                    and explicit_rows["context_policy"]
                    .astype(str)
                    .eq("outside_collecting_duct_assay")
                    .all()
                ),
            }
        )
    return pd.DataFrame(rows)


def build(output_dir: Path) -> dict[str, object]:
    training = pd.read_csv(TRAINING_FILE, sep="\t")
    pairs = _pooled_reference_pairs(training)
    graph_symbols = sorted(
        set(pairs["node_a"]).union(pairs["node_b"]),
        key=lambda value: (value.casefold(), value),
    )
    registry = load_registry(PROJECT_ROOT)
    config = default_configuration(registry)
    contributions: dict[str, np.ndarray] = {}
    matrix, integration_summary = combine_edge_factors(
        PROJECT_ROOT,
        registry,
        config,
        graph_symbols,
        contribution_collector=contributions,
    )
    stream_defs = {stream["id"]: stream for stream in registry["edge_streams"]}
    active_ids = [stream["id"] for stream in integration_summary["active_streams"]]
    source_ids = [
        stream_id
        for stream_id in active_ids
        if not bool(stream_defs[stream_id].get("derived"))
    ]
    derived_ids = [stream_id for stream_id in active_ids if stream_id not in source_ids]
    index = {symbol: position for position, symbol in enumerate(graph_symbols)}
    left = pairs["node_a"].map(index).to_numpy(int)
    right = pairs["node_b"].map(index).to_numpy(int)
    total_logs = np.zeros(len(pairs), dtype=float)
    source_logs = np.zeros(len(pairs), dtype=float)
    for stream_id in active_ids:
        values = contributions[stream_id][left, right]
        pairs[f"{stream_id}_bayes_factor"] = np.exp(values)
        total_logs += values
        if stream_id in source_ids:
            source_logs += values
    prior = float(config["edge_integration"]["prior_probability"])
    prior_odds = prior / (1.0 - prior)
    posterior = matrix.to_numpy(float)[left, right]
    posterior_odds = posterior / (1.0 - posterior)
    pairs["total_log2_bayes_factor"] = total_logs / math.log(2.0)
    pairs["total_bayes_factor"] = np.exp(total_logs)
    pairs["source_only_log2_bayes_factor"] = source_logs / math.log(2.0)
    pairs["source_only_bayes_factor"] = np.exp(source_logs)
    pairs["posterior_probability"] = posterior
    pairs["posterior_reconstructed_total_bayes_factor"] = posterior_odds / prior_odds
    pairs["rank"] = (
        pairs["total_bayes_factor"]
        .rank(method="min", ascending=False)
        .astype(int)
    )
    pairs = pairs.sort_values(
        ["rank", "node_a", "node_b"], kind="stable"
    ).reset_index(drop=True)

    h1 = pairs["pooled_reference_class"].eq("H1")
    h0 = pairs["pooled_reference_class"].eq("H0")
    confirmed_h0 = h0 & pairs["confirmed_h0"]
    outside_cd_h0 = h0 & pairs["outside_cd_explicit_h0"]
    background_h0 = h0 & ~pairs["explicit_h0"]
    h1_values = pairs.loc[h1, "total_log2_bayes_factor"].to_numpy(float)
    confirmed_h0_values = pairs.loc[
        confirmed_h0, "total_log2_bayes_factor"
    ].to_numpy(float)
    background_h0_values = pairs.loc[
        background_h0, "total_log2_bayes_factor"
    ].to_numpy(float)
    mixture_grid = [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0]
    sensitivity: list[dict[str, float]] = []
    default_model: dict[str, object] | None = None
    for mixture_weight in mixture_grid:
        model = _histogram_model(
            h1_values,
            confirmed_h0_values,
            background_h0_values,
            confirmed_mixture_weight=mixture_weight,
        )
        sensitivity.append(
            {
                "confirmed_h0_mixture_weight": mixture_weight,
                "effective_h0_weight": float(model["effective_h0_weight"]),
                "confirmed_h0_effective_weight": float(
                    model["confirmed_h0_effective_weight"]
                ),
                "background_h0_effective_weight": float(
                    model["background_h0_effective_weight"]
                ),
                "jensen_shannon_bits": float(model["jensen_shannon_bits"]),
                "kl_h1_h0_bits": float(model["kl_h1_h0_bits"]),
                "kl_h0_h1_bits": float(model["kl_h0_h1_bits"]),
                "total_variation": float(model["total_variation"]),
            }
        )
        if mixture_weight == 0.5:
            default_model = model
    assert default_model is not None

    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = output_dir / "aggregate_edge_total_bayes_factors.tsv"
    compressed_path = output_dir / "aggregate_edge_total_bayes_factors.tsv.gz"
    pairs.to_csv(catalog_path, sep="\t", index=False)
    pairs.to_csv(compressed_path, sep="\t", index=False, compression="gzip")
    result = {
        "schema_version": 1,
        "generated_at": "2026-10-30",
        "curated_training_row_count": int(len(training)),
        "unique_curated_pair_count": int(len(pairs)),
        "curated_node_count": int(len(graph_symbols)),
        "pooled_h1_pair_count": int(h1.sum()),
        "pooled_h0_pair_count": int(h0.sum()),
        "h1_h0_cross_profile_conflict_count": int(
            pairs["h1_h0_cross_profile_conflict"].sum()
        ),
        "pooled_explicit_h0_pair_count": int(
            (h0 & pairs["explicit_h0"]).sum()
        ),
        "pooled_background_h0_pair_count": int(
            (h0 & ~pairs["explicit_h0"]).sum()
        ),
        "pooled_confirmed_h0_pair_count": int(confirmed_h0.sum()),
        "pooled_outside_cd_h0_pair_count": int(outside_cd_h0.sum()),
        "active_edge_stream_count": int(len(active_ids)),
        "active_edge_streams": active_ids,
        "source_streams": source_ids,
        "derived_streams": derived_ids,
        "pair_class_policy": (
            "H1 if any curated profile labels the pair H1; otherwise H0; "
            "H1 takes precedence over cross-profile H0"
        ),
        "total_bf_formula": (
            "product over all default-enabled edge streams of applied stream BF "
            "raised to its configured weight"
        ),
        "closure_context": (
            "derived closure was evaluated on the induced graph of 212 curated nodes"
        ),
        "default_confirmed_h0_mixture_weight": 0.5,
        "h0_mixture_formula": (
            "all confirmed experimental negatives, including outside-CD controls, "
            "receive total H0 mass w and matched background receives total H0 mass "
            "1-w; weights are normalized within each group"
        ),
        "default_histogram": default_model,
        "weight_sensitivity": sensitivity,
        "top_edges": pairs.head(100)[
            [
                "node_a",
                "node_b",
                "pooled_reference_class",
                "profiles",
                "explicit_h0",
                "total_log2_bayes_factor",
                "total_bayes_factor",
                "source_only_bayes_factor",
                "posterior_probability",
                "rank",
            ]
        ].to_dict(orient="records"),
        "catalog_file": str(catalog_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "compressed_catalog_file": str(compressed_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
    }
    summary_path = output_dir / "aggregate_edge_likelihood_summary.json"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.output_dir.resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
