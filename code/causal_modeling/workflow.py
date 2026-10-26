"""End-to-end executable workflow for the compact V1 causal pilot."""

from __future__ import annotations

import json
import math
import platform
import sys
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .candidates import CandidateGraph, build_candidate_graphs, write_candidate_tables
from .data import PhosphoDataset, audit_inputs, load_phospho_dataset
from .model import (
    DynamicFit,
    clamp_intervention,
    fit_dynamic_model,
    held_out_log_predictive_density,
    local_identifiability,
    predict_dynamic_model,
)


@dataclass(frozen=True)
class PilotConfiguration:
    phospho_workbook: Path
    ranked_paths: Path
    ko_workbooks: tuple[Path, ...]
    output_directory: Path
    maximum_states: int = 6
    path_cutoffs: tuple[int, ...] = (1, 5, 20, 100)
    ridge: float = 0.1
    starts: int = 2
    max_nfev: int = 600
    seed: int = 20261015
    maximum_edge_ablations: int = 10
    maximum_repairs: int = 8


def _score_candidate(
    candidate: CandidateGraph,
    dataset: PhosphoDataset,
    configuration: PilotConfiguration,
    *,
    write_sites: list[dict[str, object]] | None = None,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    fold_rows: list[dict[str, object]] = []
    for held_out in range(len(dataset.times)):
        training = np.asarray([index for index in range(len(dataset.times)) if index != held_out])
        values, selected_sites = dataset.matrix_for_genes(candidate.states, training)
        if write_sites is not None:
            for gene, feature_id in selected_sites.items():
                write_sites.append(
                    {
                        "graph_id": candidate.graph_id,
                        "fold": f"holdout_{dataset.times[held_out]:g}_min",
                        "gene": gene,
                        "feature_id": feature_id,
                        "selection_data": "training_timepoints_only",
                    }
                )
        fit = fit_dynamic_model(
            candidate,
            dataset.times,
            values,
            training_time_indices=training,
            ridge=configuration.ridge,
            starts=configuration.starts,
            seed=configuration.seed + held_out,
            max_nfev=configuration.max_nfev,
        )
        log_density, rmse, observation_count = held_out_log_predictive_density(
            fit,
            dataset.times,
            values,
            np.asarray([held_out]),
        )
        fold_rows.append(
            {
                "graph_id": candidate.graph_id,
                "held_out_time_min": float(dataset.times[held_out]),
                "held_out_log_predictive_density": log_density,
                "held_out_rmse": rmse,
                "held_out_observation_count": observation_count,
                "optimizer_converged": fit.converged,
                "optimizer_evaluations": fit.nfev,
            }
        )
    densities = np.asarray([row["held_out_log_predictive_density"] for row in fold_rows], dtype=float)
    rmses = np.asarray([row["held_out_rmse"] for row in fold_rows], dtype=float)
    converged_count = sum(bool(row["optimizer_converged"]) for row in fold_rows)
    summary = {
        "graph_id": candidate.graph_id,
        "signature": candidate.signature,
        "state_count": len(candidate.states),
        "edge_count": len(candidate.edges),
        "input_target_count": len(candidate.input_targets),
        "held_out_log_predictive_density": float(densities.sum()),
        "mean_fold_log_predictive_density": float(densities.mean()),
        "se_fold_log_predictive_density": float(densities.std(ddof=1) / math.sqrt(len(densities))),
        "mean_held_out_rmse": float(np.nanmean(rmses)),
        "fold_count": len(fold_rows),
        "converged_fold_count": converged_count,
        "all_folds_converged": converged_count == len(fold_rows),
        "maximum_optimizer_evaluations": max(int(row["optimizer_evaluations"]) for row in fold_rows),
        "numerical_status": "ranking_eligible" if converged_count == len(fold_rows) else "optimization_incomplete",
        "validation_scheme": "leave-one-timepoint-out; site chosen within training fold",
        "evidence_reuse_status": "exploratory_conditional",
        "score_type": "grouped held-out Gaussian log predictive density; not a graph posterior",
    }
    return summary, fold_rows


def _fit_final(
    candidate: CandidateGraph,
    dataset: PhosphoDataset,
    configuration: PilotConfiguration,
) -> tuple[DynamicFit, np.ndarray, dict[str, str]]:
    values, sites = dataset.matrix_for_genes(candidate.states)
    fit = fit_dynamic_model(
        candidate,
        dataset.times,
        values,
        ridge=configuration.ridge,
        starts=max(configuration.starts, 3),
        seed=configuration.seed,
        max_nfev=configuration.max_nfev,
    )
    return fit, values, sites


def _ablate_edge(candidate: CandidateGraph, edge: tuple[str, str]) -> CandidateGraph:
    return replace(
        candidate,
        graph_id=f"{candidate.graph_id}__without__{edge[0]}_to_{edge[1]}",
        edges=tuple(item for item in candidate.edges if item != edge),
        construction=f"refit ablation of {edge[0]}->{edge[1]} from {candidate.graph_id}",
    )


def _ablate_node(candidate: CandidateGraph, node: str) -> CandidateGraph:
    return replace(
        candidate,
        graph_id=f"{candidate.graph_id}__node_ablation__{node}",
        edges=tuple(edge for edge in candidate.edges if node not in edge),
        input_targets=tuple(target for target in candidate.input_targets if target != node),
        construction=f"mechanistic node ablation of {node}; readout retained and refit",
    )


def _edge_importance(
    best_candidate: CandidateGraph,
    best_score: float,
    dataset: PhosphoDataset,
    configuration: PilotConfiguration,
) -> pd.DataFrame:
    ranked_edges = sorted(
        best_candidate.edges,
        key=lambda edge: best_candidate.edge_prior_support.get(f"{edge[0]}->{edge[1]}", 0.0),
        reverse=True,
    )[: configuration.maximum_edge_ablations]
    rows: list[dict[str, object]] = []
    for edge in ranked_edges:
        candidate = _ablate_edge(best_candidate, edge)
        score, _ = _score_candidate(candidate, dataset, configuration)
        rows.append(
            {
                "source": edge[0],
                "target": edge[1],
                "base_graph_id": best_candidate.graph_id,
                "base_held_out_lpd": best_score,
                "ablated_held_out_lpd": score["held_out_log_predictive_density"],
                "held_out_lpd_deterioration_after_refit": best_score - score["held_out_log_predictive_density"],
                "v1_prior_support": best_candidate.edge_prior_support.get(f"{edge[0]}->{edge[1]}", np.nan),
                "importance_type": "refit predictive dependence",
            }
        )
    return pd.DataFrame(rows)


def _node_importance(
    best_candidate: CandidateGraph,
    best_score: float,
    best_fit: DynamicFit,
    dataset: PhosphoDataset,
    configuration: PilotConfiguration,
) -> pd.DataFrame:
    baseline = predict_dynamic_model(best_fit, dataset.times)
    target_index = best_candidate.states.index(best_candidate.target_symbol)
    baseline_target_auc = float(np.trapz(baseline[:, target_index], dataset.times))
    rows: list[dict[str, object]] = []
    for node in best_candidate.states:
        candidate = _ablate_node(best_candidate, node)
        score, _ = _score_candidate(candidate, dataset, configuration)
        clamped = clamp_intervention(best_fit, node, dataset.times)
        clamped_target_auc = float(np.trapz(clamped[:, target_index], dataset.times))
        rows.append(
            {
                "node": node,
                "base_graph_id": best_candidate.graph_id,
                "held_out_lpd_deterioration_after_refit": best_score - score["held_out_log_predictive_density"],
                "baseline_target_auc": baseline_target_auc,
                "idealized_clamp_target_auc": clamped_target_auc,
                "predicted_target_auc_change": clamped_target_auc - baseline_target_auc,
                "influence_interpretation": "idealized acute clamp-to-baseline in fitted linear ODE; not chronic genetic KO",
            }
        )
    return pd.DataFrame(rows)


def _repair_proposals(
    best_candidate: CandidateGraph,
    best_score: float,
    all_candidates: list[CandidateGraph],
    dataset: PhosphoDataset,
    configuration: PilotConfiguration,
) -> pd.DataFrame:
    full_candidate = max(all_candidates, key=lambda candidate: (len(candidate.edges), len(candidate.input_targets)))
    missing = [edge for edge in full_candidate.edges if edge not in best_candidate.edges]
    missing.sort(
        key=lambda edge: full_candidate.edge_prior_support.get(f"{edge[0]}->{edge[1]}", 0.0),
        reverse=True,
    )
    rows: list[dict[str, object]] = []
    for edge in missing[: configuration.maximum_repairs]:
        repaired = replace(
            best_candidate,
            graph_id=f"{best_candidate.graph_id}__repair__{edge[0]}_to_{edge[1]}",
            edges=tuple(sorted((*best_candidate.edges, edge))),
            construction=f"single-edge effective-mechanism repair {edge[0]}->{edge[1]}",
        )
        score, _ = _score_candidate(repaired, dataset, configuration)
        improvement = score["held_out_log_predictive_density"] - best_score
        rows.append(
            {
                "repair_type": "add_effective_directed_mechanism",
                "source": edge[0],
                "target": edge[1],
                "v1_prior_support": full_candidate.edge_prior_support.get(f"{edge[0]}->{edge[1]}", np.nan),
                "held_out_lpd_change_after_refit": improvement,
                "passes_default_predictive_threshold": bool(improvement > 2.0),
                "status": "supported_for_followup" if improvement > 2.0 else "not_supported_or_equivalent",
                "caveat": "contracted edge may represent one or more omitted mediators; biological identity is not established",
            }
        )
    if not rows:
        rows.append(
            {
                "repair_type": "none_available_from_prespecified_V1_path_union",
                "source": "",
                "target": "",
                "v1_prior_support": np.nan,
                "held_out_lpd_change_after_refit": np.nan,
                "passes_default_predictive_threshold": False,
                "status": "no_restricted_repair_tested",
                "caveat": "best graph already contains the effective edges exposed by the largest candidate path union",
            }
        )
    return pd.DataFrame(rows)


def _ambiguity_classes(scores: pd.DataFrame) -> pd.DataFrame:
    ordered = scores.sort_values("held_out_log_predictive_density", ascending=False).reset_index(drop=True)
    eligible = ordered.loc[ordered["all_folds_converged"].astype(bool)]
    if eligible.empty:
        raise RuntimeError("no candidate converged in every validation fold")
    best = float(eligible.iloc[0]["held_out_log_predictive_density"])
    best_se = float(eligible.iloc[0]["se_fold_log_predictive_density"])
    threshold = max(2.0, 2.0 * best_se)
    rows = []
    for row in ordered.itertuples(index=False):
        difference = best - float(row.held_out_log_predictive_density)
        ranking_eligible = bool(row.all_folds_converged)
        rows.append(
            {
                "graph_id": row.graph_id,
                "signature": row.signature,
                "lpd_below_best": difference,
                "ranking_eligible": ranking_eligible,
                "predictively_competitive": ranking_eligible and difference <= threshold,
                "ambiguity_class": (
                    "optimization_incomplete"
                    if not ranking_eligible
                    else "competitive_under_current_data"
                    if difference <= threshold
                    else "less_predictive"
                ),
                "threshold_rule": f"within max(2 LPD, 2 x best fold SE) = {threshold:.6g}",
                "interpretation": "finite-data predictive grouping; not Markov equivalence or structural-identifiability certificate",
            }
        )
    return pd.DataFrame(rows)


def _rank_designs(
    fits: list[DynamicFit],
    states: tuple[str, ...],
    times: np.ndarray,
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    target = fits[0].candidate.target_symbol
    target_index = states.index(target)
    scale = np.median(np.vstack([fit.training_sigma for fit in fits]), axis=0)
    scale = np.maximum(scale, 0.08)
    for node in states:
        predictions = np.stack([clamp_intervention(fit, node, times) for fit in fits])
        normalized_variance = np.var(predictions / scale[None, None, :], axis=0)
        utility = float(np.mean(normalized_variance))
        target_utility = float(np.mean(normalized_variance[:, target_index]))
        rows.append(
            {
                "design_id": f"acute_clamp_{node}",
                "design_type": "idealized_acute_protein_clamp",
                "target": node,
                "times_min": ";".join(f"{value:g}" for value in times),
                "readouts": ";".join(states),
                "replicates": 3,
                "predictive_discrimination_surrogate": utility,
                "target_specific_discrimination": target_utility,
                "cost": 1.0,
                "feasibility": "requires biological review",
                "interpretation": "equal-weight model-prediction dispersion; not expected information gain",
            }
        )
    extra_times = np.asarray([0.5, 3.0, 10.0, 30.0])
    for time in extra_times:
        predictions = np.stack([predict_dynamic_model(fit, np.asarray([time]))[0] for fit in fits])
        normalized_variance = np.var(predictions / scale[None, :], axis=0)
        rows.append(
            {
                "design_id": f"measurement_only_{time:g}_min",
                "design_type": "additional_timepoint",
                "target": "none",
                "times_min": f"{time:g}",
                "readouts": ";".join(states),
                "replicates": 3,
                "predictive_discrimination_surrogate": float(np.mean(normalized_variance)),
                "target_specific_discrimination": float(normalized_variance[target_index]),
                "cost": 0.5,
                "feasibility": "assay panel assumed detectable",
                "interpretation": "equal-weight model-prediction dispersion; not expected information gain",
            }
        )
    frame = pd.DataFrame(rows).sort_values(
        ["predictive_discrimination_surrogate", "target_specific_discrimination"], ascending=False
    )
    frame.insert(0, "rank", np.arange(1, len(frame) + 1))
    return frame


def _write_prediction_outputs(
    fits: list[DynamicFit],
    dataset: PhosphoDataset,
    output: Path,
) -> None:
    rows: list[dict[str, object]] = []
    for fit in fits:
        prediction = predict_dynamic_model(fit, dataset.times)
        for time_index, time in enumerate(dataset.times):
            for state_index, state in enumerate(fit.candidate.states):
                rows.append(
                    {
                        "graph_id": fit.candidate.graph_id,
                        "time_min": time,
                        "state": state,
                        "predicted_log2_response": prediction[time_index, state_index],
                    }
                )
    frame = pd.DataFrame(rows)
    frame.to_csv(output / "predicted_readouts.tsv", sep="\t", index=False)
    _write_svg(frame, output / "predicted_trajectories.svg")


def _write_svg(frame: pd.DataFrame, path: Path) -> None:
    width, height = 1100, 650
    margin_left, margin_right, margin_top, margin_bottom = 85, 30, 55, 75
    plot_width = width - margin_left - margin_right
    plot_height = height - margin_top - margin_bottom
    x_values = sorted(frame["time_min"].unique())
    y_values = frame["predicted_log2_response"].to_numpy(float)
    y_min, y_max = float(np.nanmin(y_values)), float(np.nanmax(y_values))
    if math.isclose(y_min, y_max):
        y_min -= 1.0
        y_max += 1.0
    pad = 0.08 * (y_max - y_min)
    y_min -= pad
    y_max += pad
    colors = ["#006d5b", "#1666ba", "#b14f1f", "#8d4aaa", "#6d7f1f", "#b02a5b", "#5e5e5e"]
    graph_ids = list(dict.fromkeys(frame["graph_id"]))
    states = list(dict.fromkeys(frame["state"]))

    def x_position(value: float) -> float:
        return margin_left + (value - min(x_values)) / (max(x_values) - min(x_values)) * plot_width

    def y_position(value: float) -> float:
        return margin_top + (y_max - value) / (y_max - y_min) * plot_height

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fbfcfa"/>',
        '<text x="30" y="30" font-family="Arial" font-size="20" font-weight="bold">Candidate-model predicted phosphosite-proxy responses</text>',
        f'<line x1="{margin_left}" y1="{height-margin_bottom}" x2="{width-margin_right}" y2="{height-margin_bottom}" stroke="#222"/>',
        f'<line x1="{margin_left}" y1="{margin_top}" x2="{margin_left}" y2="{height-margin_bottom}" stroke="#222"/>',
    ]
    for time in x_values:
        x = x_position(float(time))
        lines.append(f'<line x1="{x:.2f}" y1="{height-margin_bottom}" x2="{x:.2f}" y2="{height-margin_bottom+6}" stroke="#222"/>')
        lines.append(f'<text x="{x:.2f}" y="{height-margin_bottom+24}" text-anchor="middle" font-family="Arial" font-size="12">{time:g}</text>')
    for fraction in np.linspace(0, 1, 6):
        value = y_min + fraction * (y_max - y_min)
        y = y_position(value)
        lines.append(f'<line x1="{margin_left-6}" y1="{y:.2f}" x2="{width-margin_right}" y2="{y:.2f}" stroke="#dfe5df"/>')
        lines.append(f'<text x="{margin_left-10}" y="{y+4:.2f}" text-anchor="end" font-family="Arial" font-size="11">{value:.2f}</text>')
    for graph_index, graph_id in enumerate(graph_ids):
        dash = "" if graph_index == 0 else f' stroke-dasharray="{3 + graph_index},{2 + graph_index}"'
        for state_index, state in enumerate(states):
            subset = frame[(frame["graph_id"] == graph_id) & (frame["state"] == state)].sort_values("time_min")
            points = " ".join(
                f'{x_position(float(row.time_min)):.2f},{y_position(float(row.predicted_log2_response)):.2f}'
                for row in subset.itertuples(index=False)
            )
            color = colors[state_index % len(colors)]
            lines.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="1.8" opacity="0.78"{dash}/>')
    legend_y = height - 34
    for state_index, state in enumerate(states):
        x = margin_left + state_index * 135
        color = colors[state_index % len(colors)]
        lines.append(f'<line x1="{x}" y1="{legend_y}" x2="{x+24}" y2="{legend_y}" stroke="{color}" stroke-width="3"/>')
        lines.append(f'<text x="{x+30}" y="{legend_y+4}" font-family="Arial" font-size="11">{state}</text>')
    lines.append(f'<text x="{margin_left + plot_width/2}" y="{height-8}" text-anchor="middle" font-family="Arial" font-size="13">Time after dDAVP (min)</text>')
    lines.append(f'<text x="18" y="{margin_top + plot_height/2}" transform="rotate(-90 18 {margin_top + plot_height/2})" text-anchor="middle" font-family="Arial" font-size="13">Predicted log2 response</text>')
    lines.append('</svg>')
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_report(
    output: Path,
    configuration: PilotConfiguration,
    audit: dict[str, object],
    scores: pd.DataFrame,
    ambiguity: pd.DataFrame,
    edge_importance: pd.DataFrame,
    node_importance: pd.DataFrame,
    designs: pd.DataFrame,
    identifiability: dict[str, object],
) -> None:
    eligible_scores = scores.loc[scores["all_folds_converged"].astype(bool)].sort_values(
        "held_out_log_predictive_density", ascending=False
    )
    best = eligible_scores.iloc[0]
    competitive = ambiguity.loc[ambiguity["predictively_competitive"], "graph_id"].tolist()
    def table(frame: pd.DataFrame, columns: list[str], rows: int = 8) -> str:
        if frame.empty:
            return "_No rows._"
        selected = frame.loc[:, [column for column in columns if column in frame.columns]].head(rows)
        headers = [str(column) for column in selected.columns]
        output_rows = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
        ]
        for values in selected.itertuples(index=False, name=None):
            rendered = []
            for value in values:
                if isinstance(value, float):
                    text = f"{value:.6g}" if math.isfinite(value) else str(value)
                else:
                    text = str(value)
                rendered.append(text.replace("|", "\\|"))
            output_rows.append("| " + " | ".join(rendered) + " |")
        return "\n".join(output_rows)

    report = f"""# Vasopressin causal-modeling pilot

Generated: {datetime.now(timezone.utc).isoformat()}

## Bottom line

The best **predictive** candidate in this compact pilot is `{best['graph_id']}` with leave-one-timepoint-out log predictive density {best['held_out_log_predictive_density']:.3f}. This is an exploratory conditional comparison, **not** a calibrated causal-graph posterior. Competitive candidates under the prespecified finite-data rule: {', '.join(competitive)}.

The score differences among the fully converged competitive candidates are small relative to fold-to-fold uncertainty. Candidates marked `optimization_incomplete` are retained for diagnosis but are not used to claim inferior biology. The best candidate's tested GSK3B→AQP2 effective edge has no positive refit dependence if its deterioration value below is zero or negative; in that case the current data support the candidate's added input coverage, not that edge itself.

The state variables are selected phosphosite-response proxies. They are not assumed to be protein abundance or kinase activity. PRKACA is an exogenous step input because it is not directly measured in the supplied time course. Edges created by contracting omitted path nodes are effective mechanisms, not claims of direct binding.

## Candidate scores

{table(scores.sort_values('held_out_log_predictive_density', ascending=False), ['graph_id', 'held_out_log_predictive_density', 'se_fold_log_predictive_density', 'mean_held_out_rmse', 'edge_count', 'converged_fold_count', 'numerical_status'])}

## Edge dependence after refitting

{table(edge_importance.sort_values('held_out_lpd_deterioration_after_refit', ascending=False) if not edge_importance.empty else edge_importance, ['source', 'target', 'held_out_lpd_deterioration_after_refit', 'v1_prior_support'])}

## Node dependence and idealized influence

{table(node_importance.sort_values('held_out_lpd_deterioration_after_refit', ascending=False), ['node', 'held_out_lpd_deterioration_after_refit', 'predicted_target_auc_change'])}

## Identifiability

- Numerical local sensitivity rank: {identifiability['local_sensitivity_rank']} / {identifiability['parameter_count']}
- Full local rank: {identifiability['full_local_rank']}
- Condition number: {identifiability['condition_number']}
- Interpretation: {identifiability['interpretation']}

## Follow-up designs

{table(designs, ['rank', 'design_id', 'predictive_discrimination_surrogate', 'target_specific_discrimination', 'feasibility'])}

These design scores are equal-weight cross-model prediction dispersion. They are a screening surrogate, not expected information gain. Acute clamping is not the same as chronic CRISPR deletion.

The target-specific discrimination values should be checked separately: a high overall score can arise because models disagree about other measured states while making nearly identical AQP2 predictions.

## Validation and leakage boundary

- Status: `{audit['status']}`.
- The local KO workbooks expose summary contrasts, not the raw replicate matrices needed for honest leave-one-target-out fitting.
- PKA-KO-derived features contributed to V1 node evidence. The dDAVP time series is also available to V1 temporal diagnostics.
- Site choice is repeated inside every training fold, but the full V1 prior graph is not rebuilt inside folds.
- Therefore this report must not be described as clean prospective validation or graph identification.

## Files

All tables, selected sites, predictions, provenance, and the configuration are in this directory. `predicted_trajectories.svg` is the readout plot. `data_audit/data_audit.json` is the authoritative limitation record.
"""
    (output / "scientific_report.md").write_text(report, encoding="utf-8")


def run_pilot(configuration: PilotConfiguration) -> Path:
    output = configuration.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    audit = audit_inputs(
        configuration.phospho_workbook,
        configuration.ko_workbooks,
        output / "data_audit",
    )
    dataset = load_phospho_dataset(configuration.phospho_workbook)
    candidates = build_candidate_graphs(
        configuration.ranked_paths,
        dataset.genes,
        maximum_states=configuration.maximum_states,
        path_cutoffs=configuration.path_cutoffs,
    )
    write_candidate_tables(candidates, output)
    states = candidates[0].states
    pd.DataFrame(
        [
            {
                "node_id": state,
                "biological_entity_type": "protein_phosphosite_response_proxy",
                "observed_eligibility": True,
                "intervention_eligibility": "requires_biological_review",
                "context": "rat_inner_medullary_collecting_duct_dDAVP",
                "evidence_provenance": "V1_ranked_path_membership_and_replicate_level_phosphoproteomics",
            }
            for state in states
        ]
    ).to_csv(output / "nodes.tsv", sep="\t", index=False)
    pd.DataFrame(
        [
            {
                "query_id": "acute_clamp_effect_on_AQP2_trajectory",
                "intervention_definition": "idealized acute clamp of one modeled state to its baseline response",
                "outcome": candidates[0].target_symbol,
                "trajectory_summary": "1-15 minute area under predicted phosphosite-proxy response",
                "population_context": "rat_inner_medullary_collecting_duct_dDAVP",
                "required_identification_assumptions": "linear time-invariant ODE; perfect acute clamp; selected phosphosite is a valid response proxy; no omitted intervention-specific adaptation",
                "identification_status": "model_based_prediction_only_not_symbolically_identified",
            }
        ]
    ).to_csv(output / "queries.tsv", sep="\t", index=False)

    score_rows: list[dict[str, object]] = []
    fold_rows: list[dict[str, object]] = []
    selected_site_rows: list[dict[str, object]] = []
    for candidate in candidates:
        score, folds = _score_candidate(
            candidate,
            dataset,
            configuration,
            write_sites=selected_site_rows,
        )
        score_rows.append(score)
        fold_rows.extend(folds)
    scores = pd.DataFrame(score_rows).sort_values("held_out_log_predictive_density", ascending=False)
    scores.to_csv(output / "candidate_scores.tsv", sep="\t", index=False)
    pd.DataFrame(fold_rows).to_csv(output / "candidate_fold_scores.tsv", sep="\t", index=False)
    pd.DataFrame(selected_site_rows).to_csv(output / "selected_sites_by_fold.tsv", sep="\t", index=False)

    candidate_by_id = {candidate.graph_id: candidate for candidate in candidates}
    eligible_scores = scores.loc[scores["all_folds_converged"].astype(bool)].sort_values(
        "held_out_log_predictive_density", ascending=False
    )
    if eligible_scores.empty:
        raise RuntimeError("no candidate converged in every validation fold")
    best_candidate = candidate_by_id[str(eligible_scores.iloc[0]["graph_id"])]
    best_score = float(eligible_scores.iloc[0]["held_out_log_predictive_density"])
    final_fits: list[DynamicFit] = []
    final_site_rows: list[dict[str, object]] = []
    parameter_rows: list[dict[str, object]] = []
    for candidate in candidates:
        fit, _, sites = _fit_final(candidate, dataset, configuration)
        final_fits.append(fit)
        for gene, feature in sites.items():
            final_site_rows.append({"graph_id": candidate.graph_id, "gene": gene, "feature_id": feature})
        for name, value in zip(fit.parameter_names, fit.parameters):
            parameter_rows.append({"graph_id": candidate.graph_id, "parameter": name, "estimate": value})
    pd.DataFrame(final_site_rows).to_csv(output / "selected_sites_final.tsv", sep="\t", index=False)
    pd.DataFrame(parameter_rows).to_csv(output / "fitted_parameters.tsv", sep="\t", index=False)
    best_fit = next(fit for fit in final_fits if fit.candidate.graph_id == best_candidate.graph_id)

    ambiguity = _ambiguity_classes(scores)
    ambiguity.to_csv(output / "graph_ambiguity_classes.tsv", sep="\t", index=False)
    edge_importance = _edge_importance(best_candidate, best_score, dataset, configuration)
    edge_importance.to_csv(output / "edge_importance.tsv", sep="\t", index=False)
    node_importance = _node_importance(best_candidate, best_score, best_fit, dataset, configuration)
    node_importance.to_csv(output / "node_importance.tsv", sep="\t", index=False)
    repairs = _repair_proposals(best_candidate, best_score, candidates, dataset, configuration)
    repairs.to_csv(output / "missing_mechanism_proposals.tsv", sep="\t", index=False)
    identifiability = local_identifiability(best_fit, dataset.times)
    pd.DataFrame([identifiability]).to_csv(output / "identifiability_audit.tsv", sep="\t", index=False)

    competitive_ids = set(ambiguity.loc[ambiguity["predictively_competitive"], "graph_id"])
    ensemble_fits = [fit for fit in final_fits if fit.candidate.graph_id in competitive_ids]
    if len(ensemble_fits) < 2:
        ensemble_fits = final_fits[:]
    designs = _rank_designs(ensemble_fits, best_candidate.states, dataset.times)
    designs.to_csv(output / "experiment_designs.tsv", sep="\t", index=False)
    _write_prediction_outputs(final_fits, dataset, output)
    _write_report(
        output,
        configuration,
        audit,
        scores,
        ambiguity,
        edge_importance,
        node_importance,
        designs,
        identifiability,
    )

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        "model_status": "exploratory_conditional",
        "best_predictive_graph": best_candidate.graph_id,
        "source_paths": {
            "phospho_workbook": str(configuration.phospho_workbook.resolve()),
            "ranked_paths": str(configuration.ranked_paths.resolve()),
            "ko_workbooks": [str(path.resolve()) for path in configuration.ko_workbooks],
        },
        "configuration": {
            **asdict(configuration),
            "phospho_workbook": str(configuration.phospho_workbook),
            "ranked_paths": str(configuration.ranked_paths),
            "ko_workbooks": [str(path) for path in configuration.ko_workbooks],
            "output_directory": str(configuration.output_directory),
        },
        "runtime": {
            "python": sys.version,
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "random_seed": configuration.seed,
        "important_limitations": audit["leakage_findings"],
    }
    (output / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return output


def default_output_directory(project_root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return project_root / "results" / "causal_modeling" / f"prkaca_to_aqp2_pilot_{timestamp}"
