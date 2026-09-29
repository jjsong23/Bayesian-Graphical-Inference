#!/usr/bin/env python3
"""Run the requested Prkaca multi-target Version 1 comparison.

The six runs share one evidence configuration.  They differ only in target and
whether internal-node posterior probabilities enter the primary path score.
Each run requests the maximum supported 2,000 exact paths. Standalone HTML
archives are generated afterward through the GUI's session-report pathway.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
GUI_DIR = PROJECT_ROOT / "gui"
if str(GUI_DIR) not in sys.path:
    sys.path.insert(0, str(GUI_DIR))

from workflow_engine import (  # noqa: E402
    default_configuration,
    load_registry,
    run_workflow,
    top_node_statistics,
)
from full_graph_statistics import (  # noqa: E402
    FULL_GRAPH_PRESENTATION_METRICS,
    GUI_RANKING_LIMIT,
    PATH_UNION_PRESENTATION_METRICS,
)
from server import _materialize_export_interpretations  # noqa: E402
from session_report import build_session_report  # noqa: E402


TARGETS = ("Dyrk1a", "Gsk3b", "Cdk12")
MAX_PATHS = 2000


def build_configuration(target: str, include_nodes: bool) -> dict[str, Any]:
    registry = load_registry(PROJECT_ROOT)
    config = default_configuration(registry)

    # Run every compatible primary/derived stream introduced or active in the
    # current Version 1 profile. HPA high-confidence remains off because it is
    # mutually exclusive with the enabled HPA primary stream.
    for stream_id, state in config["node_streams"].items():
        state["enabled"] = True
    for stream_id, state in config["edge_streams"].items():
        state["enabled"] = stream_id != "hpa_high_confidence"
    config["edge_streams"]["hpa_primary"]["enabled"] = True
    config["edge_streams"]["imcd_basal_compartment_presence"]["enabled"] = True
    config["edge_streams"]["imcd_ddavp_compartment_presence"]["enabled"] = True
    config["edge_streams"]["biogrid_physical_interaction"]["enabled"] = True
    config["edge_streams"]["biogrid_shared_partner_closure"]["enabled"] = True
    config["edge_streams"]["scaffold_triadic_closure"]["enabled"] = True

    # Compute both the thresholded full-graph statistics and the exact union of
    # all returned paths. Large-graph triangle/distance measures use the
    # deterministic approximations documented in their output summaries. The
    # live/report preview intentionally exposes only the compact canonical set.
    config["graph_statistics"]["enabled"] = True
    config["path"].update(
        {
            "enabled": True,
            "start": "Prkaca",
            "target": target,
            "top_k": MAX_PATHS,
            "include_node_probabilities": include_nodes,
            "ontology_directionality_enabled": True,
            "omnipath_directionality_enabled": True,
        }
    )
    return config


def progress_for(label: str):
    last = {"fraction": -1.0, "message": ""}

    def report(message: str, fraction: float) -> None:
        fraction = float(fraction)
        if message != last["message"] or fraction - last["fraction"] >= 0.02:
            print(
                f"[{datetime.now().isoformat(timespec='seconds')}] "
                f"{label}: {fraction:6.1%} {message}",
                flush=True,
            )
            last.update(fraction=fraction, message=message)

    return report


def top_path_record(run_directory: Path) -> dict[str, Any]:
    path = run_directory / "ranked_paths.tsv"
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return next(reader, {})


def completed_record(
    run_directory: Path,
    *,
    target: str,
    include_nodes: bool,
    elapsed_seconds: float,
    report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = json.loads(
        (run_directory / "analysis_summary.json").read_text(encoding="utf-8")
    )
    top_path = top_path_record(run_directory)
    return {
        "target": target,
        "path_scoring": "node_aware" if include_nodes else "edge_only",
        "include_node_probabilities": include_nodes,
        "requested_paths": MAX_PATHS,
        "returned_paths": int(summary["path_finding"]["paths_found"]),
        "selected_nodes": int(summary["node_selection"]["selected_node_count"]),
        "supported_edges": int(
            summary["edge_characterization"]["reported_supported_edge_count"]
        ),
        "top_path": top_path.get("path_symbols"),
        "top_primary_score": top_path.get("primary_path_score"),
        "top_edge_only_score": top_path.get(
            "geometric_mean_edge_probability"
        ),
        "run_id": summary["run_id"],
        "run_directory": str(run_directory),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "html_report": str(report["path"]) if report else None,
        "html_embedded_file_count": (
            int(report["embedded_file_count"]) if report else None
        ),
        "html_interpretation_count": (
            int(report["interpretation_hypothesis_count"]) if report else None
        ),
    }


def export_gui_equivalent_session(
    *,
    run_id: str,
    run_directory: Path,
    configuration: dict[str, Any],
    summary: dict[str, Any],
    preview: dict[str, Any],
) -> dict[str, Any]:
    """Use the same report builder and interpretation scope as the live GUI."""

    now = datetime.now().astimezone().isoformat()
    job_payload = {
        "job_id": run_id,
        "status": "complete",
        "message": "Complete",
        "progress": 1.0,
        "preview": preview,
        "summary": summary,
        "files": preview.get("files", []),
        "error": None,
        "cancel_requested": False,
        "created_at": now,
        "started_at": now,
        "finished_at": now,
    }
    client_state = {"visible_path_rank_limit": 50}
    catalog, scope, errors = _materialize_export_interpretations(
        job_payload,
        run_directory,
        [],
        client_state,
    )
    session = {
        "schema_version": 2,
        "application": "Graphical Bayesian Inference Version 1",
        "job": job_payload,
        "configuration": configuration,
        "registry": load_registry(PROJECT_ROOT),
        "client_state": client_state,
        "inspection_history": [],
        "interpretation_catalog": catalog,
        "interpretation_scope": scope,
        "interpretation_errors": errors,
    }
    return build_session_report(
        session,
        run_directory,
        report_name="complete_session_full.html",
    )


def load_completed_preview(run_directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild the GUI preview from immutable artifacts after a resumed run."""

    summary = json.loads(
        (run_directory / "analysis_summary.json").read_text(encoding="utf-8")
    )
    nodes = pd.read_csv(run_directory / "node_posteriors.tsv.gz", sep="\t")
    paths = pd.read_csv(run_directory / "ranked_paths.tsv", sep="\t")
    network = json.loads(
        (run_directory / "top_path_network.json").read_text(encoding="utf-8")
    )
    path_columns = [
        "rank",
        "hop_count",
        "primary_path_score",
        "geometric_mean_edge_probability",
        "geometric_mean_node_edge_probability",
        "path_probability_product",
        "path_symbols",
    ]
    path_columns = [column for column in path_columns if column in paths.columns]
    full_graph_preview = None
    full_graph_summary_path = run_directory / "full_graph_statistics_summary.json"
    full_graph_nodes_path = run_directory / "full_graph_node_statistics.tsv.gz"
    full_graph_robustness_path = run_directory / "full_graph_robustness.tsv"
    if full_graph_summary_path.is_file():
        full_graph_preview = {
            "summary": json.loads(
                full_graph_summary_path.read_text(encoding="utf-8")
            ),
            "available_metrics": [],
            "top_nodes_by_metric": {},
            "display_limit": GUI_RANKING_LIMIT,
            "robustness": [],
        }
        if full_graph_nodes_path.is_file():
            full_graph_nodes = pd.read_csv(full_graph_nodes_path, sep="\t")
            if not full_graph_nodes.empty:
                full_graph_preview.update(
                    top_node_statistics(
                        full_graph_nodes,
                        limit=GUI_RANKING_LIMIT,
                        metrics=FULL_GRAPH_PRESENTATION_METRICS,
                    )
                )
        if full_graph_robustness_path.is_file():
            robustness = pd.read_csv(full_graph_robustness_path, sep="\t")
            full_graph_preview["robustness"] = json.loads(
                robustness.to_json(orient="records")
            )
    path_union_preview = None
    path_union_summary_path = run_directory / "found_path_union_statistics_summary.json"
    path_union_nodes_path = run_directory / "found_path_union_node_statistics.tsv.gz"
    if path_union_summary_path.is_file():
        path_union_preview = {
            "summary": json.loads(path_union_summary_path.read_text(encoding="utf-8")),
            "available_metrics": [],
            "top_nodes_by_metric": {},
            "display_limit": GUI_RANKING_LIMIT,
        }
        if path_union_nodes_path.is_file():
            path_union_nodes = pd.read_csv(path_union_nodes_path, sep="\t")
            if not path_union_nodes.empty:
                path_union_preview.update(
                    top_node_statistics(
                        path_union_nodes,
                        limit=GUI_RANKING_LIMIT,
                        metrics=PATH_UNION_PRESENTATION_METRICS,
                    )
                )
    preview = {
        "metrics": {
            "selected_nodes": int(summary["node_selection"]["selected_node_count"]),
            "supported_edges": int(
                summary["edge_characterization"]["reported_supported_edge_count"]
            ),
            "ranked_paths": int(summary["path_finding"]["paths_found"]),
        },
        "top_nodes": json.loads(
            nodes.sort_values("gui_posterior", ascending=False)
            .loc[:, ["gene_symbol", "gui_posterior", "gui_rank"]]
            .head(10)
            .to_json(orient="records")
        ),
        "top_paths": json.loads(
            paths.loc[:, path_columns].head(MAX_PATHS).to_json(orient="records")
        ),
        "path_network": network,
        "full_graph_statistics": full_graph_preview,
        "found_path_union_statistics": path_union_preview,
        "probability_distributions": {
            "nodes": summary["node_selection"]["probability_distribution"],
            "edges": summary["edge_characterization"]["probability_distribution"],
        },
        "directionality": summary.get("ontology_directionality"),
        "temporal_validation": summary.get("temporal_validation"),
        "calibration": summary.get("calibration"),
        "warnings": summary.get("warnings", []),
        "files": [*summary.get("outputs", []), "analysis_summary.json"],
    }
    return summary, preview


def ranked_path_map(run_directory: Path) -> dict[str, dict[str, Any]]:
    with (run_directory / "ranked_paths.tsv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return {str(row["path_symbols"]): row for row in rows}


def rank_correlation(
    left: dict[str, dict[str, Any]], right: dict[str, dict[str, Any]]
) -> float | None:
    common = sorted(set(left).intersection(right))
    if len(common) < 2:
        return None
    x = [float(left[path]["rank"]) for path in common]
    y = [float(right[path]["rank"]) for path in common]
    mean_x = sum(x) / len(x)
    mean_y = sum(y) / len(y)
    numerator = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y))
    denominator = math.sqrt(
        sum((a - mean_x) ** 2 for a in x) * sum((b - mean_y) ** 2 for b in y)
    )
    return numerator / denominator if denominator else None


def write_mode_comparison(batch_directory: Path, records: list[dict[str, Any]]) -> None:
    by_key = {(row["target"], row["path_scoring"]): row for row in records}
    comparisons: list[dict[str, Any]] = []
    for target in TARGETS:
        edge = by_key[(target, "edge_only")]
        node = by_key[(target, "node_aware")]
        edge_paths = ranked_path_map(Path(edge["run_directory"]))
        node_paths = ranked_path_map(Path(node["run_directory"]))
        common = set(edge_paths).intersection(node_paths)
        union = set(edge_paths).union(node_paths)

        def top_set(rows: dict[str, dict[str, Any]], limit: int) -> set[str]:
            return {
                path
                for path, record in rows.items()
                if int(float(record["rank"])) <= limit
            }

        comparison: dict[str, Any] = {
            "target": target,
            "edge_only_top_path": edge["top_path"],
            "node_aware_top_path": node["top_path"],
            "same_top_path": edge["top_path"] == node["top_path"],
            f"common_paths_among_{MAX_PATHS}": len(common),
            f"path_set_jaccard_{MAX_PATHS}": len(common) / len(union) if union else 1.0,
            "common_path_rank_correlation": rank_correlation(edge_paths, node_paths),
            "edge_only_winner_rank_when_node_aware": (
                node_paths.get(str(edge["top_path"]), {}).get("rank")
            ),
            "node_aware_winner_rank_when_edge_only": (
                edge_paths.get(str(node["top_path"]), {}).get("rank")
            ),
        }
        for limit in (10, 50, 100):
            left = top_set(edge_paths, limit)
            right = top_set(node_paths, limit)
            comparison[f"common_top_{limit}"] = len(left.intersection(right))
            comparison[f"top_{limit}_jaccard"] = (
                len(left.intersection(right)) / len(left.union(right))
                if left or right
                else 1.0
            )
        comparisons.append(comparison)
    with (batch_directory / "node_aware_rank_comparison.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(comparisons[0]), delimiter="\t"
        )
        writer.writeheader()
        writer.writerows(comparisons)


def execute(batch_label: str) -> Path:
    batch_directory = PROJECT_ROOT / "results" / "prkaca_multitarget" / batch_label
    batch_directory.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    manifest_path = batch_directory / "run_manifest.json"

    for target in TARGETS:
        for include_nodes in (False, True):
            mode = "node_aware" if include_nodes else "edge_only"
            run_id = f"{batch_label}_prkaca_to_{target.casefold()}_{mode}"
            run_directory = PROJECT_ROOT / "results" / "gui_runs" / run_id
            label = f"Prkaca -> {target} ({mode})"
            config = build_configuration(target, include_nodes)
            started = time.perf_counter()
            result = None
            if (run_directory / "analysis_summary.json").is_file():
                print(f"\n=== Reusing completed {label} ===", flush=True)
            else:
                if run_directory.exists():
                    raise RuntimeError(
                        f"Incomplete run directory must be reviewed before retry: {run_directory}"
                    )
                print(f"\n=== Starting {label} ===", flush=True)
                result = run_workflow(
                    config,
                    project_root=PROJECT_ROOT,
                    run_id=run_id,
                    progress=progress_for(label),
                )
            report = None
            existing_reports = sorted(run_directory.glob("complete_session_*.html"))
            if existing_reports:
                report = {
                    "path": str(existing_reports[-1].resolve()),
                    "embedded_file_count": 0,
                    "interpretation_hypothesis_count": 0,
                }
            else:
                print(f"=== Exporting full GUI session for {label} ===", flush=True)
                if result is None:
                    resumed_summary, resumed_preview = load_completed_preview(run_directory)
                else:
                    resumed_summary, resumed_preview = result.summary, result.preview
                report = export_gui_equivalent_session(
                    run_id=run_id,
                    run_directory=run_directory,
                    configuration=config,
                    summary=resumed_summary,
                    preview=resumed_preview,
                )
            elapsed = time.perf_counter() - started
            record = completed_record(
                run_directory,
                target=target,
                include_nodes=include_nodes,
                elapsed_seconds=elapsed,
                report=report,
            )
            records.append(record)
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "batch_label": batch_label,
                        "description": (
                            f"Prkaca to Dyrk1a/Gsk3b/Cdk12, {MAX_PATHS} paths, all "
                            "compatible recent V1 evidence/features, paired "
                            "edge-only and node-aware path scoring"
                        ),
                        "runs": records,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print(
                f"=== Completed {label}: {record['returned_paths']} paths in "
                f"{elapsed / 60:.1f} min ===",
                flush=True,
            )

    columns = list(records[0])
    with (batch_directory / "run_summary.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t")
        writer.writeheader()
        writer.writerows(records)
    write_mode_comparison(batch_directory, records)
    (batch_directory / "README.md").write_text(
        "# Prkaca multi-target comparison\n\n"
        "Six immutable Version 1 runs compare edge-only and node-aware path "
        "ranking for Prkaca to Dyrk1a, Gsk3b, and Cdk12. Every run requested "
        f"{MAX_PATHS:,} paths with six hops, current ontology/KinasePredictor/OmniPath "
        "directionality, all 12 node streams, every compatible primary edge "
        "stream, both IMCD co-detection streams, scaffold closure, BioGRID "
        "reported interactions, and novel-pair-only BioGRID shared-partner "
        "closure. Both full-graph and returned-path-union statistics were "
        "computed automatically. For large dense graphs, the reports explicitly "
        "label deterministic landmark/sampling approximations for clustering, "
        "betweenness, distance centralities, path length, and diameter. See "
        "`run_manifest.json` and `run_summary.tsv` for run paths and top-result "
        "comparisons.\n",
        encoding="utf-8",
    )
    report_rows = [
        {
            "target": record["target"],
            "path_scoring": record["path_scoring"],
            "html_report": record["html_report"],
        }
        for record in records
    ]
    with (batch_directory / "html_report_manifest.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["target", "path_scoring", "html_report"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(report_rows)
    return batch_directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch-label",
        default=datetime.now().strftime("%Y%m%d_%H%M%S_recent_features"),
    )
    args = parser.parse_args()
    output = execute(args.batch_label)
    print(f"\nBatch complete: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
