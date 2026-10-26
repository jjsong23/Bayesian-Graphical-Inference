#!/usr/bin/env python3
"""Collect compact AlphaPulldown outputs into an auditable V1 evidence table.

Only predictions with a finite ipTM score in [0, 1] and a nonempty ranked_0.pdb
are observed. Missing or failed tasks are retained in the audit but omitted from
the factor table, so a failed computation cannot become negative evidence.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_REFERENCE = 0.6
DEFAULT_FLOOR = 0.1
DEFAULT_CEILING = 10.0


def open_text(path: Path):
    if path.suffix.casefold() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return path.open("r", encoding="utf-8-sig", newline="")


def structural_bayes_factor(score: float, reference: float, floor: float, ceiling: float) -> float:
    if not 0.0 <= score <= 1.0:
        raise ValueError("structural score must lie in [0, 1]")
    if not 0.0 < reference < 1.0:
        raise ValueError("reference must lie strictly between 0 and 1")
    if not 0.0 < floor <= 1.0 <= ceiling:
        raise ValueError("bounds must satisfy 0 < floor <= 1 <= ceiling")
    epsilon = 1e-6
    bounded = min(max(score, epsilon), 1.0 - epsilon)
    score_odds = bounded / (1.0 - bounded)
    reference_odds = reference / (1.0 - reference)
    return min(max(score_odds / reference_odds, floor), ceiling)


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    required = {"pair_id", "node_a", "node_b", "phase", "selection_basis"}
    if not rows or not required.issubset(rows[0]):
        raise ValueError(f"invalid candidate manifest: {path}")
    return rows


def iptm_scores(path: Path) -> list[float]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = {str(name).strip().casefold(): name for name in (reader.fieldnames or [])}
        column = columns.get("iptm")
        if column is None:
            return []
        values: list[float] = []
        for row in reader:
            try:
                value = float(row[column])
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and 0.0 <= value <= 1.0:
                values.append(value)
        return values


def write_gzip_tsv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def collect_stage(
    stage: Path,
    reference: float,
    floor: float,
    ceiling: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    manifest = read_manifest(stage / "candidate_manifest.tsv")
    audit: list[dict[str, Any]] = []
    observed: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for candidate in manifest:
        model_dir = stage / "models" / candidate["pair_id"]
        score_path = model_dir / "predictions_with_good_interpae.csv"
        structure_path = model_dir / "ranked_0.pdb"
        status = "observed"
        values: list[float] = []
        if not score_path.is_file() or score_path.stat().st_size == 0:
            status = "missing_score_file"
        else:
            values = iptm_scores(score_path)
            if not values:
                status = "no_valid_iptm"
        if status == "observed" and (
            not structure_path.is_file() or structure_path.stat().st_size == 0
        ):
            status = "missing_ranked_structure"
        score = max(values) if status == "observed" else None
        factor = (
            structural_bayes_factor(float(score), reference, floor, ceiling)
            if score is not None else None
        )
        row: dict[str, Any] = {
            **candidate,
            "collection_status": status,
            "iptm": "" if score is None else score,
            "bayes_factor": "" if factor is None else factor,
            "reference_score": reference,
            "bayes_factor_floor": floor,
            "bayes_factor_ceiling": ceiling,
            "score_file": str(score_path.resolve()) if score_path.exists() else "",
            "structure_file": str(structure_path.resolve()) if structure_path.exists() else "",
        }
        audit.append(row)
        counts[status] = counts.get(status, 0) + 1
        if status == "observed":
            observed.append(row)
    audit_columns = [
        "pair_id", "node_a", "node_b", "phase", "selection_basis",
        "collection_status", "iptm", "bayes_factor", "reference_score",
        "bayes_factor_floor", "bayes_factor_ceiling", "score_file", "structure_file",
    ]
    write_gzip_tsv(stage / "structural_prediction_audit.tsv.gz", audit, audit_columns)
    factor_columns = [
        "node_a", "node_b", "bayes_factor", "iptm", "phase", "pair_id",
        "selection_basis", "reference_score", "bayes_factor_floor",
        "bayes_factor_ceiling", "score_file", "structure_file",
    ]
    write_gzip_tsv(stage / "alphapulldown_structural_bf.tsv.gz", observed, factor_columns)
    summary = {
        "phase": stage.name,
        "candidate_count": len(manifest),
        "observed_count": len(observed),
        "status_counts": counts,
        "missing_results_are_negative_evidence": False,
        "factor_file": str((stage / "alphapulldown_structural_bf.tsv.gz").resolve()),
        "audit_file": str((stage / "structural_prediction_audit.tsv.gz").resolve()),
    }
    (stage / "collection_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return observed, summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen-dir", type=Path, required=True)
    parser.add_argument("--reference-score", type=float, default=DEFAULT_REFERENCE)
    parser.add_argument("--bayes-factor-floor", type=float, default=DEFAULT_FLOOR)
    parser.add_argument("--bayes-factor-ceiling", type=float, default=DEFAULT_CEILING)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    screen = args.screen_dir.expanduser().resolve()
    if not (screen / "screen_summary.json").is_file():
        raise FileNotFoundError(screen / "screen_summary.json")
    all_observed: dict[tuple[str, str], dict[str, Any]] = {}
    stage_summaries: list[dict[str, Any]] = []
    for name in ("phase1_cocomplex", "phase2_other"):
        stage = screen / name
        if not (stage / "candidate_manifest.tsv").is_file():
            continue
        observed, summary = collect_stage(
            stage,
            args.reference_score,
            args.bayes_factor_floor,
            args.bayes_factor_ceiling,
        )
        stage_summaries.append(summary)
        for row in observed:
            key = tuple(sorted((str(row["node_a"]), str(row["node_b"])), key=str.casefold))
            previous = all_observed.get(key)
            if previous is None or float(row["iptm"]) > float(previous["iptm"]):
                all_observed[key] = row
    rows = sorted(all_observed.values(), key=lambda row: (str(row["node_a"]).casefold(), str(row["node_b"]).casefold()))
    factor_columns = [
        "node_a", "node_b", "bayes_factor", "iptm", "phase", "pair_id",
        "selection_basis", "reference_score", "bayes_factor_floor",
        "bayes_factor_ceiling", "score_file", "structure_file",
    ]
    factor_path = screen / "alphapulldown_structural_bf.tsv.gz"
    write_gzip_tsv(factor_path, rows, factor_columns)
    summary = {
        "schema_version": 1,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "project": "graphical_bayesian_inference_v1",
        "observed_unique_pair_count": len(rows),
        "stage_summaries": stage_summaries,
        "factor_mapping": {
            "metric": "iptm",
            "formula": "bounded score odds divided by reference-score odds",
            "reference_score": args.reference_score,
            "bayes_factor_floor": args.bayes_factor_floor,
            "bayes_factor_ceiling": args.bayes_factor_ceiling,
            "status": "heuristic pending empirical V1 calibration",
        },
        "missing_results_rule": "omitted from factor table; unobserved and neutral",
        "v1_factor_file": str(factor_path.resolve()),
    }
    (screen / "collection_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
