#!/usr/bin/env python3
"""Validate and install a portable V1 AlphaPulldown evidence bundle.

The importer copies only the combined pair-factor table into the catalog used
by the GUI.  Manifests, audit tables, and collection metadata are retained in
the data archive for provenance.  Missing predictions never enter the factor
table and therefore remain neutral in Bayesian integration.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CATALOG_RELATIVE = Path(
    "results/backend_bayes_factor_catalogs/edge_factors_891/"
    "alphapulldown_structural_bf.tsv.gz"
)
PROVENANCE_RELATIVE = Path(
    "data/edge_characterization/alphapulldown/"
    "v1_colocalized_model1_cycles3_predictions1_compact"
)
REQUIRED_FACTOR_COLUMNS = {
    "node_a",
    "node_b",
    "bayes_factor",
    "iptm",
    "phase",
    "pair_id",
    "selection_basis",
    "reference_score",
    "bayes_factor_floor",
    "bayes_factor_ceiling",
}


def canonical_pair(left: str, right: str) -> tuple[str, str]:
    return tuple(
        sorted((str(left), str(right)), key=lambda value: (value.casefold(), value))
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_bundle_root(path: Path) -> Path:
    supplied = path.expanduser().resolve()
    candidates: list[Path] = []
    possible_roots = [supplied]
    possible_roots.extend(item.parent for item in supplied.rglob("collection_summary.json"))
    for candidate in possible_roots:
        if (
            (candidate / "collection_summary.json").is_file()
            and (candidate / "screen_summary.json").is_file()
            and (candidate / "alphapulldown_structural_bf.tsv.gz").is_file()
        ):
            candidates.append(candidate)
    roots = sorted(
        {candidate.resolve() for candidate in candidates}
    )
    if len(roots) != 1:
        raise ValueError(
            f"expected exactly one extracted AlphaPulldown bundle under {supplied}; "
            f"found {len(roots)}"
        )
    return roots[0]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_factor_table(path: Path) -> dict[str, Any]:
    seen: set[tuple[str, str]] = set()
    phase_counts: dict[str, int] = {}
    factors: list[float] = []
    scores: list[float] = []
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        missing = REQUIRED_FACTOR_COLUMNS.difference(reader.fieldnames or [])
        if missing:
            raise ValueError(f"factor table is missing columns: {sorted(missing)}")
        for row_number, row in enumerate(reader, start=2):
            key = canonical_pair(row["node_a"], row["node_b"])
            if not all(key) or key[0] == key[1]:
                raise ValueError(f"invalid endpoints on factor row {row_number}: {key}")
            if key in seen:
                raise ValueError(f"duplicate unordered pair on factor row {row_number}: {key}")
            seen.add(key)
            try:
                factor = float(row["bayes_factor"])
                score = float(row["iptm"])
            except ValueError as exc:
                raise ValueError(f"non-numeric score on factor row {row_number}") from exc
            if not math.isfinite(factor) or factor <= 0:
                raise ValueError(f"invalid Bayes factor on row {row_number}: {factor}")
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError(f"invalid ipTM on row {row_number}: {score}")
            phase = str(row["phase"])
            phase_counts[phase] = phase_counts.get(phase, 0) + 1
            factors.append(factor)
            scores.append(score)
    return {
        "observed_unique_pair_count": len(seen),
        "phase_counts": phase_counts,
        "bayes_factor_minimum": min(factors),
        "bayes_factor_maximum": max(factors),
        "iptm_minimum": min(scores),
        "iptm_maximum": max(scores),
        "supporting_factor_count": sum(value > 1 for value in factors),
        "neutral_factor_count": sum(math.isclose(value, 1.0, abs_tol=1e-12) for value in factors),
        "refuting_factor_count": sum(value < 1 for value in factors),
    }


def copy_required(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def import_bundle(bundle: Path, project: Path) -> dict[str, Any]:
    root = find_bundle_root(bundle)
    collection = read_json(root / "collection_summary.json")
    screen = read_json(root / "screen_summary.json")
    if collection.get("project") != "graphical_bayesian_inference_v1":
        raise ValueError("bundle is not a Version 1 AlphaPulldown collection")
    if screen.get("project") != "graphical_bayesian_inference_v1":
        raise ValueError("screen metadata is not owned by Version 1")

    source_factor = root / "alphapulldown_structural_bf.tsv.gz"
    table_summary = validate_factor_table(source_factor)
    expected = int(collection.get("observed_unique_pair_count", -1))
    if table_summary["observed_unique_pair_count"] != expected:
        raise ValueError(
            "combined factor row count does not match collection_summary.json: "
            f"{table_summary['observed_unique_pair_count']} != {expected}"
        )

    factor_destination = project / CATALOG_RELATIVE
    provenance_destination = project / PROVENANCE_RELATIVE
    factor_destination.parent.mkdir(parents=True, exist_ok=True)
    provenance_destination.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_factor, factor_destination)
    for filename in ("collection_summary.json", "screen_summary.json"):
        copy_required(root / filename, provenance_destination / filename)
    for phase in ("phase1_cocomplex", "phase2_other"):
        for filename in (
            "candidate_manifest.tsv",
            "collection_summary.json",
            "structural_prediction_audit.tsv.gz",
            "alphapulldown_structural_bf.tsv.gz",
        ):
            copy_required(
                root / phase / filename,
                provenance_destination / phase / filename,
            )

    missing = sum(
        int(stage.get("candidate_count", 0)) - int(stage.get("observed_count", 0))
        for stage in collection.get("stage_summaries", [])
    )
    summary = {
        "schema_version": 1,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "source_bundle": str(root),
        "source_factor_sha256": sha256(source_factor),
        "installed_factor_file": str(factor_destination.resolve()),
        "provenance_directory": str(provenance_destination.resolve()),
        "protocol_id": screen.get("phases", [{}])[0].get("protocol_id"),
        "reference_score": collection.get("factor_mapping", {}).get("reference_score"),
        "bayes_factor_floor": collection.get("factor_mapping", {}).get("bayes_factor_floor"),
        "bayes_factor_ceiling": collection.get("factor_mapping", {}).get("bayes_factor_ceiling"),
        "missing_prediction_count": missing,
        "missing_predictions_are_neutral": True,
        **table_summary,
    }
    (provenance_destination / "import_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path, help="Extracted portable bundle directory")
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = import_bundle(args.bundle, args.project_root.expanduser().resolve())
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
