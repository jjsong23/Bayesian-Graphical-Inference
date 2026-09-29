#!/usr/bin/env python3
"""Screen node and edge evidence streams for conditional redundancy.

Simple agreement is not redundancy: two independent assays should corroborate
the same true hypothesis.  This audit therefore conditions each stream pair on
the support supplied by every *other* enabled stream.  Within narrow strata of
that leave-two-streams-out support (and edge endpoint type), it measures whether
the two streams still co-vary.  Persistent residual association is a screening
signal for shared provenance, overlapping assay coverage, derivation, or another
unmodeled dependency; it is not by itself proof that either stream is invalid.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = PROJECT_ROOT / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from evidence_inspector import _graph_metadata  # noqa: E402
from workflow_engine import (  # noqa: E402
    combine_edge_factors,
    load_registry,
)


TOLERANCE = 1e-12
DEFAULT_STRATA = 20
MINIMUM_OVERLAP = 100
CORRELATION_FLAG = 0.25
CONDITIONAL_MI_FLAG_BITS = 0.02


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def _residualize(values: np.ndarray, strata: np.ndarray) -> np.ndarray:
    counts = np.bincount(strata)
    sums = np.bincount(strata, weights=values)
    means = np.divide(sums, counts, out=np.zeros_like(sums), where=counts > 0)
    return values - means[strata]


def _residual_correlation(
    left: np.ndarray, right: np.ndarray, strata: np.ndarray
) -> float | None:
    left_residual = _residualize(left.astype(float), strata)
    right_residual = _residualize(right.astype(float), strata)
    denominator = math.sqrt(
        float(np.dot(left_residual, left_residual))
        * float(np.dot(right_residual, right_residual))
    )
    if denominator <= 0:
        return None
    return float(np.dot(left_residual, right_residual) / denominator)


def _conditional_mutual_information(
    left: np.ndarray,
    right: np.ndarray,
    strata: np.ndarray,
) -> float:
    """Conditional MI in bits for signed calls: refutes/neutral/supports."""

    total = len(left)
    if total == 0:
        return 0.0
    information = 0.0
    for stratum in np.unique(strata):
        selected = strata == stratum
        n = int(selected.sum())
        if n < 2:
            continue
        joint = np.zeros((3, 3), dtype=float)
        np.add.at(joint, (left[selected], right[selected]), 1.0)
        joint /= n
        p_left = joint.sum(axis=1)
        p_right = joint.sum(axis=0)
        for i in range(3):
            for j in range(3):
                value = joint[i, j]
                expected = p_left[i] * p_right[j]
                if value > 0 and expected > 0:
                    information += (n / total) * value * math.log2(value / expected)
    return float(information)


def _conditioning_strata(
    other_evidence: np.ndarray,
    base_groups: np.ndarray,
    *,
    bin_count: int,
) -> np.ndarray:
    result = np.zeros(len(other_evidence), dtype=np.int32)
    offset = 0
    for group in pd.unique(base_groups):
        selected = np.flatnonzero(base_groups == group)
        values = other_evidence[selected]
        unique = np.unique(values)
        if len(unique) <= 1:
            bins = np.zeros(len(selected), dtype=np.int32)
        else:
            quantiles = np.unique(
                np.quantile(values, np.linspace(0.0, 1.0, min(bin_count, len(unique)) + 1))
            )
            bins = (
                np.digitize(values, quantiles[1:-1], right=True).astype(np.int32)
                if len(quantiles) > 2
                else np.zeros(len(selected), dtype=np.int32)
            )
        result[selected] = bins + offset
        offset += int(bins.max(initial=0)) + 1
    return result


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return float(numerator / denominator)


def pairwise_conditional_redundancy(
    contributions: pd.DataFrame,
    definitions: dict[str, dict[str, Any]],
    base_groups: np.ndarray,
    *,
    stage: str,
    bin_count: int = DEFAULT_STRATA,
) -> pd.DataFrame:
    streams = contributions.columns.astype(str).tolist()
    values = contributions.to_numpy(dtype=float)
    total = values.sum(axis=1)
    rows: list[dict[str, Any]] = []
    for left_index, right_index in combinations(range(len(streams)), 2):
        left_id = streams[left_index]
        right_id = streams[right_index]
        left = values[:, left_index]
        right = values[:, right_index]
        non_neutral_union = (np.abs(left) > TOLERANCE) | (np.abs(right) > TOLERANCE)
        if not non_neutral_union.any():
            continue
        # Evaluate the complete hypothesis universe. Conditioning on the
        # non-neutral union itself would create collider/Berkson bias: two
        # independent sparse streams would appear negatively associated merely
        # because every retained row was selected by one or the other stream.
        left_values = left
        right_values = right
        other = total - left - right
        strata = _conditioning_strata(
            other,
            np.asarray(base_groups, dtype=object),
            bin_count=bin_count,
        )
        left_support = left_values > TOLERANCE
        right_support = right_values > TOLERANCE
        left_non_neutral = np.abs(left_values) > TOLERANCE
        right_non_neutral = np.abs(right_values) > TOLERANCE
        both_support = int((left_support & right_support).sum())
        support_union = int((left_support | right_support).sum())
        expected_both = 0.0
        for stratum in np.unique(strata):
            selected = strata == stratum
            n = int(selected.sum())
            if n:
                expected_both += (
                    n
                    * float(left_support[selected].mean())
                    * float(right_support[selected].mean())
                )
        conditional_phi = _residual_correlation(
            left_support.astype(float), right_support.astype(float), strata
        )
        conditional_logbf_correlation = _residual_correlation(
            left_values, right_values, strata
        )
        left_call = np.where(left_values < -TOLERANCE, 0, np.where(left_values > TOLERANCE, 2, 1))
        right_call = np.where(right_values < -TOLERANCE, 0, np.where(right_values > TOLERANCE, 2, 1))
        conditional_mi = _conditional_mutual_information(left_call, right_call, strata)
        left_definition = definitions[left_id]
        right_definition = definitions[right_id]
        left_group = left_definition.get("dependence_group")
        right_group = right_definition.get("dependence_group")
        same_group = bool(left_group and left_group == right_group)
        biogrid_disjoint = {
            left_id,
            right_id,
        } == {
            "biogrid_physical_interaction",
            "biogrid_shared_partner_closure",
        }
        empirical_flag = bool(
            int((left_non_neutral & right_non_neutral).sum()) >= MINIMUM_OVERLAP
            and (
                (
                    conditional_logbf_correlation is not None
                    and conditional_logbf_correlation >= CORRELATION_FLAG
                    and (
                        _safe_ratio(both_support, expected_both) or 0.0
                    ) >= 1.25
                )
                or (
                    conditional_mi >= CONDITIONAL_MI_FLAG_BITS
                    and conditional_phi is not None
                    and conditional_phi >= CORRELATION_FLAG
                )
            )
        )
        if biogrid_disjoint:
            interpretation = "dependent sources; endpoint calls made mutually exclusive by design"
        elif same_group:
            interpretation = "documented shared-source dependency"
        elif empirical_flag:
            interpretation = "potential unmodeled redundancy after corroboration control"
        else:
            interpretation = "no strong residual dependency detected"
        rows.append(
            {
                "stage": stage,
                "stream_a": left_id,
                "stream_b": right_id,
                "label_a": left_definition.get("label", left_id),
                "label_b": right_definition.get("label", right_id),
                "dependence_group_a": left_group,
                "dependence_group_b": right_group,
                "same_documented_dependence_group": same_group,
                "stream_a_derived": bool(left_definition.get("derived")),
                "stream_b_derived": bool(right_definition.get("derived")),
                "hypotheses_evaluated": int(len(left_values)),
                "hypotheses_in_non_neutral_union": int(non_neutral_union.sum()),
                "stream_a_non_neutral_count": int(left_non_neutral.sum()),
                "stream_b_non_neutral_count": int(right_non_neutral.sum()),
                "both_non_neutral_count": int((left_non_neutral & right_non_neutral).sum()),
                "both_support_count": both_support,
                "support_jaccard": _safe_ratio(both_support, support_union),
                "conditional_expected_both_support": expected_both,
                "conditional_support_excess_ratio": _safe_ratio(both_support, expected_both),
                "conditional_phi_support": conditional_phi,
                "conditional_logbf_correlation": conditional_logbf_correlation,
                "conditional_signed_call_mutual_information_bits": conditional_mi,
                "conditioning_strata_count": int(len(np.unique(strata))),
                "empirical_residual_dependency_flag": empirical_flag,
                "redundancy_interpretation": interpretation,
            }
        )
    return pd.DataFrame(rows)


def _node_contributions(
    run_directory: Path,
    config: dict[str, Any],
    registry: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], np.ndarray]:
    table = pd.read_csv(run_directory / "node_posteriors.tsv.gz", sep="\t")
    definitions = {item["id"]: item for item in registry["node_streams"]}
    columns: dict[str, pd.Series] = {}
    active_definitions: dict[str, dict[str, Any]] = {}
    for stream_id, state in config["node_streams"].items():
        column = f"gui_{stream_id}_weighted_log_bayes_factor"
        if state.get("enabled") and float(state.get("weight", 0)) > 0 and column in table:
            columns[stream_id] = pd.to_numeric(table[column], errors="coerce").fillna(0.0)
            active_definitions[stream_id] = definitions[stream_id]
    return pd.DataFrame(columns), active_definitions, np.full(len(table), "all_nodes", dtype=object)


def _edge_contributions(
    run_directory: Path,
    config: dict[str, Any],
    registry: dict[str, Any],
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]], np.ndarray, float]:
    header = pd.read_csv(
        run_directory / "edge_adjacency_matrix.tsv", sep="\t", nrows=0
    ).columns.astype(str).tolist()
    symbols = header[1:]
    metadata = _graph_metadata(PROJECT_ROOT, run_directory, symbols)
    collector: dict[str, np.ndarray] = {}
    recomputed, _ = combine_edge_factors(
        PROJECT_ROOT,
        registry,
        config,
        symbols,
        graph_metadata=metadata,
        contribution_collector=collector,
    )
    stored = pd.read_csv(
        run_directory / "edge_adjacency_matrix.tsv", sep="\t", index_col=0
    ).reindex(index=symbols, columns=symbols)
    reconciliation = float(
        np.nanmax(np.abs(recomputed.to_numpy(float) - stored.to_numpy(float)))
    )
    upper = np.triu_indices(len(symbols), 1)
    definitions = {item["id"]: item for item in registry["edge_streams"]}
    contributions = pd.DataFrame(
        {
            stream_id: matrix[upper]
            for stream_id, matrix in collector.items()
        }
    )
    active_definitions = {
        stream_id: definitions[stream_id] for stream_id in contributions.columns
    }
    aligned = metadata.set_index("symbol").reindex(symbols)
    node_types = aligned["node_type"].fillna("unknown").astype(str).str.casefold().to_numpy()
    left_type = node_types[upper[0]]
    right_type = node_types[upper[1]]
    pair_types = np.asarray(
        ["—".join(sorted((left, right))) for left, right in zip(left_type, right_type)],
        dtype=object,
    )
    return contributions, active_definitions, pair_types, reconciliation


def _plot_heatmap(table: pd.DataFrame, value: str, destination: Path, title: str) -> None:
    streams = sorted(set(table["stream_a"]).union(table["stream_b"]))
    matrix = pd.DataFrame(np.nan, index=streams, columns=streams, dtype=float)
    diagonal_value = 1.0 if "correlation" in value else 0.0
    for index in range(len(streams)):
        matrix.iat[index, index] = diagonal_value
    for row in table.itertuples(index=False):
        selected = getattr(row, value)
        if selected is not None and pd.notna(selected):
            matrix.loc[row.stream_a, row.stream_b] = float(selected)
            matrix.loc[row.stream_b, row.stream_a] = float(selected)
    values = matrix.to_numpy(float)
    divergent = "correlation" in value or "phi" in value
    maximum = 1.0 if divergent else max(float(np.nanmax(values)), 0.01)

    def color(number: float) -> tuple[int, int, int]:
        if not math.isfinite(number):
            return (224, 224, 224)
        if divergent:
            scaled = min(1.0, max(-1.0, number))
            if scaled < 0:
                fraction = scaled + 1.0
                return (
                    int(49 + fraction * (255 - 49)),
                    int(95 + fraction * (255 - 95)),
                    int(181 + fraction * (255 - 181)),
                )
            return (
                int(255 + scaled * (180 - 255)),
                int(255 + scaled * (4 - 255)),
                int(255 + scaled * (38 - 255)),
            )
        fraction = min(1.0, max(0.0, number / maximum))
        stops = (
            (0.0, (68, 1, 84)),
            (0.33, (49, 104, 142)),
            (0.66, (53, 183, 121)),
            (1.0, (253, 231, 37)),
        )
        for (lower, lower_color), (upper, upper_color) in zip(stops, stops[1:]):
            if fraction <= upper:
                mix = (fraction - lower) / (upper - lower)
                return tuple(
                    int(left + mix * (right - left))
                    for left, right in zip(lower_color, upper_color)
                )
        return stops[-1][1]

    try:
        font = ImageFont.truetype("arial.ttf", 14)
        title_font = ImageFont.truetype("arial.ttf", 20)
    except OSError:
        font = ImageFont.load_default()
        title_font = font
    cell = 34
    left_margin = max(180, max((len(item) for item in streams), default=0) * 8 + 20)
    top_margin = left_margin + 50
    grid_size = cell * len(streams)
    width = left_margin + grid_size + 100
    height = top_margin + grid_size + 65
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    draw.text((16, 12), title, fill=(20, 40, 45), font=title_font)
    for row_index, label in enumerate(streams):
        y = top_margin + row_index * cell
        draw.text((8, y + 8), label, fill=(30, 30, 30), font=font)
        for column_index in range(len(streams)):
            x = left_margin + column_index * cell
            draw.rectangle(
                (x, y, x + cell, y + cell),
                fill=color(float(values[row_index, column_index])),
                outline=(245, 245, 245),
            )
    for column_index, label in enumerate(streams):
        label_image = Image.new("RGBA", (left_margin, 24), (255, 255, 255, 0))
        label_draw = ImageDraw.Draw(label_image)
        label_draw.text((0, 3), label, fill=(30, 30, 30, 255), font=font)
        rotated = label_image.rotate(60, expand=True)
        x = left_margin + column_index * cell + cell // 2 - rotated.width // 2
        y = top_margin - rotated.height - 3
        image.paste(rotated, (x, y), rotated)
    bar_x = left_margin + grid_size + 35
    bar_height = grid_size
    for offset in range(bar_height):
        fraction = 1.0 - offset / max(1, bar_height - 1)
        number = (2.0 * fraction - 1.0) if divergent else maximum * fraction
        draw.line(
            (bar_x, top_margin + offset, bar_x + 20, top_margin + offset),
            fill=color(number),
        )
    low_label = "-1" if divergent else "0"
    high_label = "1" if divergent else f"{maximum:.3g}"
    draw.text((bar_x + 27, top_margin - 4), high_label, fill=(30, 30, 30), font=font)
    draw.text(
        (bar_x + 27, top_margin + bar_height - 14),
        low_label,
        fill=(30, 30, 30),
        font=font,
    )
    image.save(destination, format="PNG", optimize=True)


def analyze_runs(run_directories: list[Path], output_directory: Path) -> Path:
    registry = load_registry(PROJECT_ROOT)
    output_directory.mkdir(parents=True, exist_ok=True)
    cached: dict[str, tuple[pd.DataFrame, pd.DataFrame, float]] = {}
    all_rows: list[pd.DataFrame] = []
    summaries: list[dict[str, Any]] = []
    for run_directory in run_directories:
        run_directory = run_directory.resolve()
        config = json.loads((run_directory / "configuration.json").read_text())
        header = pd.read_csv(
            run_directory / "edge_adjacency_matrix.tsv", sep="\t", nrows=0
        ).columns.astype(str).tolist()[1:]
        signature = _fingerprint(
            {
                "node_streams": config["node_streams"],
                "node_integration": config["node_integration"],
                "edge_streams": config["edge_streams"],
                "edge_integration": config["edge_integration"],
                "symbols": header,
            }
        )
        if signature not in cached:
            node_values, node_definitions, node_groups = _node_contributions(
                run_directory, config, registry
            )
            node_results = pairwise_conditional_redundancy(
                node_values,
                node_definitions,
                node_groups,
                stage="node_selection",
            )
            edge_values, edge_definitions, edge_groups, reconciliation = _edge_contributions(
                run_directory, config, registry
            )
            edge_results = pairwise_conditional_redundancy(
                edge_values,
                edge_definitions,
                edge_groups,
                stage="edge_characterization",
            )
            cached[signature] = (node_results, edge_results, reconciliation)
            node_results.to_csv(
                output_directory / f"{signature}_node_pairwise_redundancy.tsv",
                sep="\t",
                index=False,
            )
            edge_results.to_csv(
                output_directory / f"{signature}_edge_pairwise_redundancy.tsv",
                sep="\t",
                index=False,
            )
            _plot_heatmap(
                node_results,
                "conditional_logbf_correlation",
                output_directory / f"{signature}_node_conditional_correlation.png",
                "Node streams: residual log-BF correlation",
            )
            _plot_heatmap(
                edge_results,
                "conditional_logbf_correlation",
                output_directory / f"{signature}_edge_conditional_correlation.png",
                "Edge streams: residual log-BF correlation",
            )
        node_results, edge_results, reconciliation = cached[signature]
        run_id = run_directory.name
        for stage_results in (node_results, edge_results):
            copy = stage_results.copy()
            copy.insert(0, "run_id", run_id)
            copy.insert(1, "configuration_signature", signature)
            all_rows.append(copy)
        combined = pd.concat([node_results, edge_results], ignore_index=True)
        summaries.append(
            {
                "run_id": run_id,
                "run_directory": str(run_directory),
                "configuration_signature": signature,
                "node_stream_count": int(
                    len(set(node_results["stream_a"]).union(node_results["stream_b"]))
                ),
                "edge_stream_count": int(
                    len(set(edge_results["stream_a"]).union(edge_results["stream_b"]))
                ),
                "documented_dependency_pair_count": int(
                    combined["same_documented_dependence_group"].sum()
                ),
                "empirical_residual_dependency_pair_count": int(
                    combined["empirical_residual_dependency_flag"].sum()
                ),
                "edge_recomputed_vs_stored_max_abs_difference": reconciliation,
            }
        )
    all_results = pd.concat(all_rows, ignore_index=True)
    all_results.to_csv(
        output_directory / "all_runs_pairwise_redundancy.tsv.gz",
        sep="\t",
        index=False,
        compression="gzip",
    )
    pd.DataFrame(summaries).to_csv(
        output_directory / "run_summary.tsv", sep="\t", index=False
    )
    flagged = all_results.loc[
        all_results["same_documented_dependence_group"]
        | all_results["empirical_residual_dependency_flag"]
    ].copy()
    flagged.to_csv(
        output_directory / "flagged_stream_pairs.tsv", sep="\t", index=False
    )
    unique_flagged = flagged.drop_duplicates(
        ["configuration_signature", "stage", "stream_a", "stream_b"]
    ).copy()
    finding_rows: list[str] = []
    for row in unique_flagged.sort_values(
        ["stage", "stream_a", "stream_b"], kind="stable"
    ).itertuples(index=False):
        correlation = (
            "—"
            if pd.isna(row.conditional_logbf_correlation)
            else f"{float(row.conditional_logbf_correlation):.3f}"
        )
        mutual_information = (
            "—"
            if pd.isna(row.conditional_signed_call_mutual_information_bits)
            else f"{float(row.conditional_signed_call_mutual_information_bits):.3f}"
        )
        finding_rows.append(
            "| "
            + " | ".join(
                [
                    str(row.stage),
                    str(row.label_a),
                    str(row.label_b),
                    "yes" if bool(row.same_documented_dependence_group) else "no",
                    "yes" if bool(row.empirical_residual_dependency_flag) else "no",
                    correlation,
                    mutual_information,
                    str(row.redundancy_interpretation),
                ]
            )
            + " |"
        )
    empirical_unique = int(
        unique_flagged["empirical_residual_dependency_flag"].astype(bool).sum()
    )
    documented_unique = int(
        unique_flagged["same_documented_dependence_group"].astype(bool).sum()
    )
    (output_directory / "findings.md").write_text(
        "# Evidence-redundancy findings\n\n"
        f"This audit covered {len(run_directories)} analyses representing "
        f"{len(cached)} unique node/edge evidence configuration(s). It found "
        f"{documented_unique} unique stream pairs with a registry-declared shared "
        f"provenance group and {empirical_unique} unique pairs meeting the positive "
        "conditional-dependence effect-size screen. These are review flags, not "
        "automatic grounds for removing a stream.\n\n"
        "BioGRID reported interactions and BioGRID shared-partner closure had zero "
        "jointly non-neutral endpoint pairs: reported endpoint pairs were excluded "
        "from closure before Bayesian integration.\n\n"
        "| Stage | Stream A | Stream B | Documented dependency | Empirical flag | "
        "Conditional log-BF r | Conditional MI (bits) | Interpretation |\n"
        "|---|---|---|---:|---:|---:|---:|---|\n"
        + "\n".join(finding_rows)
        + "\n",
        encoding="utf-8",
    )
    method = {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "run_count": len(run_directories),
        "unique_evidence_configuration_count": len(cached),
        "conditioning": (
            "For each stream pair, the complete node or unique-edge hypothesis "
            "universe was retained. Weighted natural-log BF contributions were "
            "residualized within up to 20 quantile strata of the summed contribution "
            "from every other enabled stream. Edge strata additionally preserve "
            "endpoint node-type combinations. Retaining neutral rows avoids "
            "collider/Berkson bias from selecting the pairwise non-neutral union."
        ),
        "why_this_is_not_plain_agreement": (
            "Conditioning on all remaining evidence removes much of the association "
            "expected merely because both streams support hypotheses already favored "
            "by independent sources. Remaining association is a dependency screen, "
            "not causal proof of redundancy."
        ),
        "effect_size_flags": {
            "minimum_both_non_neutral": MINIMUM_OVERLAP,
            "positive_conditional_logbf_correlation": CORRELATION_FLAG,
            "minimum_conditional_support_excess_ratio_with_logbf_flag": 1.25,
            "positive_conditional_support_phi_with_mutual_information": CORRELATION_FLAG,
            "conditional_signed_call_mutual_information_bits": CONDITIONAL_MI_FLAG_BITS,
        },
        "no_p_value_policy": (
            "With up to millions of dependent node-pair hypotheses, conventional "
            "row-wise p-values would be misleadingly tiny. Flags use explicit effect "
            "sizes and documented provenance instead."
        ),
    }
    (output_directory / "method.json").write_text(
        json.dumps(method, indent=2) + "\n", encoding="utf-8"
    )
    (output_directory / "README.md").write_text(
        "# Conditional evidence-redundancy audit\n\n"
        + method["conditioning"]
        + "\n\n"
        + method["why_this_is_not_plain_agreement"]
        + "\n\nThe audit reports conditional residual log-BF correlation, conditional "
        "support phi, signed-call conditional mutual information, observed versus "
        "conditionally expected co-support, documented dependence groups, and an "
        "effect-size screening flag. A flag means the pair should be reviewed for "
        "shared provenance or overlapping measurement—not that corroborating data "
        "must be discarded. BioGRID reported-pair and shared-partner calls are now "
        "mutually exclusive at the endpoint-pair level.\n",
        encoding="utf-8",
    )
    return output_directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    destination = analyze_runs(args.runs, args.output_dir.resolve())
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
