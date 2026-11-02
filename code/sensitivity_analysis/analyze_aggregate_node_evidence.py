#!/usr/bin/env python3
"""Aggregate all default V1 node streams into one auditable node BF catalog.

The script reproduces the GUI's node integration exactly, then constructs one
disjoint pooled reference set from the leave-one-stream-out labels already used
by the empirical node calibration:

* H1: a node is H1 for at least one stream because another biologically
  compatible stream supports it.
* H0: a node is covered by at least one compatible peer stream but is never H1.
* Unlabeled: neither condition is met.

H1 takes precedence over H0 so every node appears at most once in the pooled
reference distribution. These H0 nodes are comparison controls, not curated
experimental node-absence controls.
"""

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
    NODE_LIKELIHOOD_PROFILES,
    default_configuration,
    load_registry,
    select_nodes,
)


DEFAULT_OUTPUT = (
    PROJECT_ROOT
    / "results"
    / "likelihood_calibration"
    / "aggregate_node_evidence_20261030"
)


def _compatible(left: str, right: str) -> bool:
    return left == right or {left, right} <= {
        "kinase_activity",
        "transient_phosphoregulation",
    }


def _information(p1: np.ndarray, p0: np.ndarray) -> dict[str, float]:
    midpoint = (p1 + p0) / 2.0
    kl_10 = float(np.sum(p1 * np.log2(p1 / p0)))
    kl_01 = float(np.sum(p0 * np.log2(p0 / p1)))
    js = float(
        0.5 * np.sum(p1 * np.log2(p1 / midpoint))
        + 0.5 * np.sum(p0 * np.log2(p0 / midpoint))
    )
    tv = float(0.5 * np.sum(np.abs(p1 - p0)))
    return {
        "jensen_shannon_bits": js,
        "kl_h1_h0_bits": kl_10,
        "kl_h0_h1_bits": kl_01,
        "total_variation": tv,
    }


def _histogram_model(
    h1_values: np.ndarray,
    h0_values: np.ndarray,
    *,
    h0_weight: float,
    smoothing: float = 0.5,
    bins: int = 20,
) -> dict[str, object]:
    pooled = np.concatenate([h1_values, h0_values])
    finite = pooled[np.isfinite(pooled)]
    if not len(finite):
        raise ValueError("aggregate node references contain no finite total BF values")
    lower, upper = np.quantile(finite, [0.01, 0.99])
    if math.isclose(float(lower), float(upper)):
        lower, upper = float(finite.min()) - 0.5, float(finite.max()) + 0.5
    interior = np.linspace(float(lower), float(upper), bins - 1)
    edges = np.concatenate(([-np.inf], interior, [np.inf]))
    h1_counts, _ = np.histogram(h1_values, bins=edges)
    h0_counts, _ = np.histogram(h0_values, bins=edges)
    h1_bin_weights = h1_counts.astype(float)
    h0_bin_weights = h0_counts.astype(float) * float(h0_weight)
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
        "p_data_given_h1": p1.tolist(),
        "p_data_given_h0": p0.tolist(),
        **_information(p1, p0),
    }


def build(output_dir: Path) -> dict[str, object]:
    registry = load_registry(PROJECT_ROOT)
    config = default_configuration(registry)
    factors, _, summary = select_nodes(PROJECT_ROOT, registry, config)
    tolerance = float(config["node_integration"]["non_neutral_tolerance"])
    active_ids = [stream["id"] for stream in summary["active_streams"]]
    profiles = {
        stream_id: NODE_LIKELIHOOD_PROFILES.get(
            stream_id, "general_node_support"
        )
        for stream_id in active_ids
    }
    classes = factors.get(
        "node_classes", pd.Series("", index=factors.index)
    ).fillna("").astype(str)
    kinase_candidates = classes.map(
        lambda value: "kinase"
        in {token.strip() for token in value.split(";") if token.strip()}
    ).to_numpy(bool)

    h1_votes = np.zeros(len(factors), dtype=int)
    h0_votes = np.zeros(len(factors), dtype=int)
    for stream_id in active_ids:
        profile = profiles[stream_id]
        peer_support = np.zeros(len(factors), dtype=bool)
        peer_coverage = np.zeros(len(factors), dtype=bool)
        for peer_id in active_ids:
            if peer_id == stream_id or not _compatible(profile, profiles[peer_id]):
                continue
            peer_support |= (
                factors[
                    f"gui_{peer_id}_pre_likelihood_calibration_bayes_factor"
                ].to_numpy(float)
                > 1.0 + tolerance
            )
            peer_coverage |= factors[
                f"gui_{peer_id}_negative_evidence_eligible"
            ].to_numpy(bool)
        scope = (
            kinase_candidates
            if profile == "kinase_activity"
            else np.ones(len(factors), dtype=bool)
        )
        h1_votes += (peer_support & scope).astype(int)
        h0_votes += (peer_coverage & ~peer_support & scope).astype(int)

    pooled_class = np.full(len(factors), "unlabeled", dtype=object)
    pooled_class[(h1_votes == 0) & (h0_votes > 0)] = "H0"
    pooled_class[h1_votes > 0] = "H1"
    weighted_columns = [
        f"gui_{stream_id}_weighted_log_bayes_factor"
        for stream_id in active_ids
    ]
    total_log_bf = factors[weighted_columns].sum(axis=1).to_numpy(float)
    total_log2_bf = total_log_bf / math.log(2.0)
    total_bf = np.exp(total_log_bf)

    catalog = pd.DataFrame(
        {
            "gene_symbol": factors["gene_symbol"].astype(str),
            "node_name": factors.get("node_name", ""),
            "node_classes": classes,
            "pooled_reference_class": pooled_class,
            "h1_stream_votes": h1_votes,
            "h0_stream_votes": h0_votes,
            "total_log2_bayes_factor": total_log2_bf,
            "total_bayes_factor": total_bf,
            "posterior_probability": factors["gui_posterior"].to_numpy(float),
            "posterior_rank": factors["gui_rank"].to_numpy(int),
        }
    )
    for stream_id in active_ids:
        catalog[f"{stream_id}_bayes_factor"] = factors[
            f"gui_{stream_id}_bayes_factor"
        ].to_numpy(float)
    catalog = catalog.sort_values(
        ["posterior_rank", "gene_symbol"], kind="stable"
    ).reset_index(drop=True)

    h1_values = catalog.loc[
        catalog["pooled_reference_class"].eq("H1"), "total_log2_bayes_factor"
    ].to_numpy(float)
    h0_values = catalog.loc[
        catalog["pooled_reference_class"].eq("H0"), "total_log2_bayes_factor"
    ].to_numpy(float)
    gamma_grid = [0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0]
    sensitivity = []
    default_model = None
    for gamma in gamma_grid:
        model = _histogram_model(h1_values, h0_values, h0_weight=gamma)
        sensitivity.append(
            {
                "gamma": gamma,
                "effective_h0_weight": float(len(h0_values) * gamma),
                "jensen_shannon_bits": model["jensen_shannon_bits"],
                "kl_h1_h0_bits": model["kl_h1_h0_bits"],
                "kl_h0_h1_bits": model["kl_h0_h1_bits"],
                "total_variation": model["total_variation"],
            }
        )
        if gamma == 4.0:
            default_model = model
    assert default_model is not None

    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = output_dir / "aggregate_node_total_bayes_factors.tsv"
    compressed_catalog_path = output_dir / "aggregate_node_total_bayes_factors.tsv.gz"
    catalog.to_csv(catalog_path, sep="\t", index=False)
    catalog.to_csv(
        compressed_catalog_path, sep="\t", index=False, compression="gzip"
    )
    result = {
        "schema_version": 1,
        "generated_at": "2026-10-30",
        "candidate_node_count": int(len(catalog)),
        "active_node_stream_count": int(len(active_ids)),
        "active_node_streams": active_ids,
        "pooled_h1_node_count": int(len(h1_values)),
        "pooled_h0_node_count": int(len(h0_values)),
        "unlabeled_node_count": int((pooled_class == "unlabeled").sum()),
        "class_policy": (
            "H1 if any leave-one-stream-out compatible peer support exists; "
            "otherwise H0 if compatible peer coverage exists; H1 takes precedence"
        ),
        "h0_caveat": (
            "Pooled node H0 is covered-but-unsupported comparison background, "
            "not curated experimental node absence"
        ),
        "total_bf_formula": (
            "product over active streams of calibrated stream BF raised to its "
            "configured stream weight"
        ),
        "default_h0_diagnostic_weight": 4.0,
        "default_histogram": default_model,
        "weight_sensitivity": sensitivity,
        "top_nodes": catalog.head(100)[
            [
                "gene_symbol",
                "pooled_reference_class",
                "total_log2_bayes_factor",
                "total_bayes_factor",
                "posterior_probability",
                "posterior_rank",
                "h1_stream_votes",
                "h0_stream_votes",
            ]
        ].to_dict(orient="records"),
        "catalog_file": str(catalog_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
        "compressed_catalog_file": str(
            compressed_catalog_path.relative_to(PROJECT_ROOT)
        ).replace("\\", "/"),
    }
    summary_path = output_dir / "aggregate_node_likelihood_summary.json"
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build(args.output_dir.resolve())
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
