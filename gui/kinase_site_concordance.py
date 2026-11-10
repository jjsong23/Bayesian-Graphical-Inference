"""Condition kinase/substrate motif evidence on measured site response.

The legacy KinasePredictor stream asked only whether a kinase motif matched an
observed phosphosite.  That is biochemical compatibility, not evidence that the
site participates in the modeled vasopressin/PKA response.  This module builds
an exact-site response catalog from the Kirby dDAVP time course and the two PKA
catalytic-subunit knockout comparisons, then shrinks every motif Bayes factor
toward neutral according to the response reliability at that same site::

    BF_concordant = 1 + r_response * (BF_motif - 1)

``r_response`` is in [0, 1].  Independent response datasets are combined with
a noisy-OR.  Ambiguous multi-site PKA-KO peptides are discounted by the number
of candidate sites.  The strongest concordant site determines an undirected
edge; additional correlated sites are retained in the audit but are not
multiplied into an inflated relationship score.
"""

from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


KIRBY_WORKBOOK = Path("data/phospho_data_original.xlsx")
PKA_KO_SITE_AUDIT = Path(
    "data/node_selection/pka_subunit_ko/processed/"
    "pka_subunit_ko_site_audit.tsv.gz"
)
KIRBY_SHEETS = {
    "1 min": 1,
    "2 min": 2,
    "5 min": 3,
    "15 min": 4,
}
KIRBY_UNIPROT_COLUMN = "Protein UniProt ID"
KIRBY_POSITION_COLUMN = "Position in Protein (A)"
KIRBY_GENE_COLUMN = "Gene Symbol"


def _symbols(value: object) -> list[str]:
    text = "" if pd.isna(value) else str(value).strip()
    return [
        cleaned
        for token in re.split(r"[;,/|]+", text)
        if (cleaned := token.strip())
    ]


def _positions(value: object) -> list[int]:
    if pd.isna(value):
        return []
    return sorted({int(token) for token in re.findall(r"\d+", str(value))})


def _complement_bayes_factor(value: float, threshold: float) -> float:
    if not (math.isfinite(value) and math.isfinite(threshold) and threshold > 0):
        return 1.0
    likelihood = max(0.5, 1.0 - math.exp(-0.5 * (abs(value) / threshold) ** 2))
    return likelihood / 0.5


def _kirby_site_rows(project: Path) -> list[dict[str, Any]]:
    workbook = project / KIRBY_WORKBOOK
    if not workbook.is_file():
        return []
    site_observations: dict[tuple[str, int], dict[str, Any]] = {}
    for sheet, index in KIRBY_SHEETS.items():
        frame = pd.read_excel(workbook, sheet_name=sheet)
        control_columns = [f"C{index}_Replicate{replicate}_1" for replicate in (1, 2, 3)]
        treated_columns = [f"V{index}_Replicate{replicate}_1" for replicate in (1, 2, 3)]
        required = {
            KIRBY_GENE_COLUMN,
            KIRBY_UNIPROT_COLUMN,
            KIRBY_POSITION_COLUMN,
            *control_columns,
            *treated_columns,
        }
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(
                f"Kirby sheet {sheet!r} lacks required columns: {sorted(missing)}"
            )
        controls = frame[control_columns].apply(pd.to_numeric, errors="coerce").to_numpy(float)
        treated = frame[treated_columns].apply(pd.to_numeric, errors="coerce").to_numpy(float)
        observed = (controls > 0) & (treated > 0)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios = np.where(observed, np.log2(treated / controls), np.nan)
        for row_index, row in frame.iterrows():
            positions = _positions(row[KIRBY_POSITION_COLUMN])
            genes = _symbols(row[KIRBY_GENE_COLUMN])
            if not positions or not genes:
                continue
            finite = ratios[row_index, np.isfinite(ratios[row_index])]
            if not len(finite):
                continue
            mean_lfc = float(np.mean(finite))
            for gene in genes:
                for position in positions:
                    key = (gene.casefold(), position)
                    record = site_observations.setdefault(
                        key,
                        {
                            "symbol": gene,
                            "position": position,
                            "uniprot": str(row[KIRBY_UNIPROT_COLUMN]).strip(),
                            "timepoints": [],
                        },
                    )
                    record["timepoints"].append(
                        {
                            "time": sheet,
                            "mean_log2_ddavp_over_control": mean_lfc,
                            "replicate_count": int(len(finite)),
                        }
                    )
    if not site_observations:
        return []
    scores = np.asarray(
        [
            max(abs(item["mean_log2_ddavp_over_control"]) for item in record["timepoints"])
            for record in site_observations.values()
        ],
        dtype=float,
    )
    threshold = float(np.quantile(scores[np.isfinite(scores)], 0.75))
    rows: list[dict[str, Any]] = []
    for record in site_observations.values():
        score = max(
            abs(item["mean_log2_ddavp_over_control"])
            for item in record["timepoints"]
        )
        bayes_factor = _complement_bayes_factor(score, threshold)
        rows.append(
            {
                "symbol": record["symbol"],
                "position": int(record["position"]),
                "dataset": "kirby_ddavp_timecourse",
                "response_value": float(score),
                "response_threshold": threshold,
                "response_bayes_factor": bayes_factor,
                "response_reliability": min(1.0, max(0.0, bayes_factor - 1.0)),
                "site_resolution": "exact_position",
                "detail": record["timepoints"],
            }
        )
    return rows


def _pka_ko_site_rows(project: Path) -> list[dict[str, Any]]:
    path = project / PKA_KO_SITE_AUDIT
    if not path.is_file():
        return []
    frame = pd.read_csv(path, sep="\t", compression="gzip")
    rows: list[dict[str, Any]] = []
    comparisons = (
        ("pka_ca_ko", "PKA-Cα knockout"),
        ("pka_cb_ko", "PKA-Cβ knockout"),
    )
    for record in frame.to_dict(orient="records"):
        symbol = str(record.get("mapped_gene_symbol") or "").strip()
        positions = _positions(record.get("site_label"))
        if not symbol or symbol.casefold() == "nan" or not positions:
            continue
        ambiguity_discount = 1.0 / len(positions)
        resolution = "exact_position" if len(positions) == 1 else "ambiguous_site_group"
        for prefix, label in comparisons:
            factor = pd.to_numeric(
                pd.Series([record.get(f"{prefix}_site_bayes_factor")]),
                errors="coerce",
            ).iloc[0]
            value = pd.to_numeric(
                pd.Series([record.get(f"{prefix}_signed_lfc")]), errors="coerce"
            ).iloc[0]
            if pd.isna(factor) or pd.isna(value):
                continue
            raw_reliability = min(1.0, max(0.0, float(factor) - 1.0))
            for position in positions:
                rows.append(
                    {
                        "symbol": symbol,
                        "position": position,
                        "dataset": prefix,
                        "response_value": abs(float(value)),
                        "response_threshold": None,
                        "response_bayes_factor": float(factor),
                        "response_reliability": raw_reliability * ambiguity_discount,
                        "site_resolution": resolution,
                        "detail": {
                            "comparison": label,
                            "signed_log2_ko_over_intact": float(value),
                            "site_label": str(record.get("site_label") or ""),
                            "site_group_size": len(positions),
                            "ambiguity_discount": ambiguity_discount,
                            "modified_p_value": record.get(f"{prefix}_pmod"),
                        },
                    }
                )
    return rows


@lru_cache(maxsize=4)
def _cached_site_response_catalog(project_text: str) -> tuple[dict[str, Any], ...]:
    project = Path(project_text)
    rows = [*_kirby_site_rows(project), *_pka_ko_site_rows(project)]
    return tuple(rows)


def site_response_catalog(project: Path | str) -> pd.DataFrame:
    """Return one auditable response row per dataset and exact candidate site."""
    rows = _cached_site_response_catalog(str(Path(project).resolve()))
    return pd.DataFrame.from_records(rows)


def response_lookup(project: Path | str) -> dict[tuple[str, int], dict[str, Any]]:
    catalog = site_response_catalog(project)
    lookup: dict[tuple[str, int], dict[str, Any]] = {}
    if catalog.empty:
        return lookup
    for (symbol, position), group in catalog.groupby(
        [catalog["symbol"].astype(str).str.casefold(), "position"], sort=False
    ):
        reliabilities = np.clip(
            pd.to_numeric(group["response_reliability"], errors="coerce")
            .fillna(0.0)
            .to_numpy(float),
            0.0,
            1.0,
        )
        combined = float(1.0 - np.prod(1.0 - reliabilities))
        records = []
        for row in group.to_dict(orient="records"):
            row = dict(row)
            row["detail"] = row.get("detail") or {}
            records.append(row)
        lookup[(str(symbol), int(position))] = {
            "combined_response_reliability": combined,
            "datasets": records,
        }
    return lookup


def kirby_node_factor_catalog(project: Path | str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build Kirby phosphoprotein and inferred-kinase node evidence.

    The phosphoprotein branch uses the strongest dDAVP-responsive site per
    protein.  The kinase branch asks whether a responsive Kirby site has a
    top-ten motif match to the kinase, then retains the strongest exact-site
    conjunction per kinase.  Both branches use one source-wide q75 and never
    multiply multiple sites on the same node.
    """
    project = Path(project).resolve()
    catalog = site_response_catalog(project)
    kirby = catalog.loc[catalog["dataset"].eq("kirby_ddavp_timecourse")].copy()
    if kirby.empty:
        return pd.DataFrame(columns=["gene_symbol"]), pd.DataFrame()
    kirby["response_value"] = pd.to_numeric(kirby["response_value"], errors="raise")
    threshold = float(pd.to_numeric(kirby["response_threshold"], errors="raise").iloc[0])
    selected_indices = kirby.groupby("symbol", sort=False)["response_value"].idxmax()
    selected = kirby.loc[selected_indices].copy()
    proteins = pd.DataFrame(
        {
            "gene_symbol": selected["symbol"].astype(str),
            "kirby_ddavp_observed": True,
            "kirby_ddavp_max_absolute_lfc": selected["response_value"].to_numpy(float),
            "kirby_ddavp_selected_site": selected["position"].astype(int).to_numpy(),
            "kirby_ddavp_site_tq": threshold,
            "kirby_ddavp_phosphoprotein_factor": selected[
                "response_bayes_factor"
            ].to_numpy(float),
        }
    )

    prediction_path = project / (
        "results/edge_characterization/localization_kinase_predictor/"
        "observed_phosphosite_top10_predictions.tsv.gz"
    )
    predictions = pd.read_csv(prediction_path, sep="\t")
    used = predictions["used_for_undirected_edge"]
    if used.dtype != bool:
        used = used.astype(str).str.casefold().eq("true")
    predictions = predictions.loc[used].copy()
    raw = pd.to_numeric(predictions["raw_score"], errors="raise").to_numpy(float)
    tq = pd.to_numeric(predictions["site_Tq_q75"], errors="raise").to_numpy(float)
    motif_likelihood = np.maximum(0.5, 1.0 - np.exp(-0.5 * np.square(raw / tq)))
    motif_bf = motif_likelihood / 0.5
    gated = apply_site_response_gate(predictions, project, motif_bf)
    gated["kinase_activity_score"] = np.maximum(
        0.0,
        gated["site_response_reliability"].to_numpy(float)
        * (gated["motif_bayes_factor_before_response_gate"].to_numpy(float) - 1.0),
    )
    kinase_selected_indices = gated.groupby("kinase_node", sort=False)[
        "kinase_activity_score"
    ].idxmax()
    kinase_selected = gated.loc[kinase_selected_indices].copy()
    positive_scores = kinase_selected["kinase_activity_score"].to_numpy(float)
    finite_positive = positive_scores[np.isfinite(positive_scores) & (positive_scores > 0)]
    kinase_threshold = (
        float(np.quantile(finite_positive, 0.75)) if len(finite_positive) else 1.0
    )
    kinase_factor = np.asarray(
        [
            _complement_bayes_factor(value, kinase_threshold)
            for value in kinase_selected["kinase_activity_score"].to_numpy(float)
        ],
        dtype=float,
    )
    kinases = pd.DataFrame(
        {
            "gene_symbol": kinase_selected["kinase_node"].astype(str),
            "kirby_kinase_activity_observed": kinase_selected[
                "kinase_activity_score"
            ].gt(0).to_numpy(bool),
            "kirby_kinase_activity_score": kinase_selected[
                "kinase_activity_score"
            ].to_numpy(float),
            "kirby_kinase_activity_target": kinase_selected[
                "target_protein"
            ].astype(str).to_numpy(),
            "kirby_kinase_activity_target_site": kinase_selected[
                "target_site"
            ].astype(str).to_numpy(),
            "kirby_kinase_activity_tq": kinase_threshold,
            "kirby_kinase_activity_factor": kinase_factor,
        }
    )
    factors = proteins.merge(kinases, on="gene_symbol", how="outer", validate="one_to_one")
    factors["kirby_ddavp_observed"] = factors["kirby_ddavp_observed"].fillna(False).astype(bool)
    factors["kirby_ddavp_phosphoprotein_factor"] = factors[
        "kirby_ddavp_phosphoprotein_factor"
    ].fillna(1.0)
    factors["kirby_kinase_activity_observed"] = factors[
        "kirby_kinase_activity_observed"
    ].fillna(False).astype(bool)
    factors["kirby_kinase_activity_factor"] = factors[
        "kirby_kinase_activity_factor"
    ].fillna(1.0)
    audit = gated.sort_values(
        ["kinase_node", "kinase_activity_score"],
        ascending=[True, False],
        kind="stable",
    ).reset_index(drop=True)
    return factors, audit


def apply_site_response_gate(
    predictions: pd.DataFrame,
    project: Path | str,
    motif_bayes_factors: np.ndarray,
) -> pd.DataFrame:
    """Attach exact-site response evidence and gated BFs to prediction rows."""
    if len(predictions) != len(motif_bayes_factors):
        raise ValueError("motif Bayes factors must align with prediction rows")
    lookup = response_lookup(project)
    result = predictions.copy().reset_index(drop=True)
    reliabilities: list[float] = []
    source_records: list[str] = []
    statuses: list[str] = []
    for row in result.itertuples(index=False):
        position_values = _positions(getattr(row, "position", None))
        if not position_values:
            position_values = _positions(getattr(row, "target_site", None))
        position = position_values[0] if position_values else -1
        response = lookup.get((str(getattr(row, "target_protein")).casefold(), position))
        if response is None:
            reliabilities.append(0.0)
            source_records.append("[]")
            statuses.append("site_not_measured_in_response_datasets")
            continue
        reliability = float(response["combined_response_reliability"])
        reliabilities.append(reliability)
        source_records.append(
            json.dumps(response["datasets"], ensure_ascii=False, default=str)
        )
        statuses.append(
            "responsive_exact_site" if reliability > 0 else "measured_but_not_responsive"
        )
    motif = np.asarray(motif_bayes_factors, dtype=float)
    reliability_array = np.asarray(reliabilities, dtype=float)
    gated = 1.0 + reliability_array * (motif - 1.0)
    result["motif_bayes_factor_before_response_gate"] = motif
    result["site_response_reliability"] = reliability_array
    result["response_gated_bayes_factor"] = gated
    result["site_response_status"] = statuses
    result["site_response_evidence_json"] = source_records
    result["response_gate_formula"] = "1 + r_response * (BF_motif - 1)"
    return result

