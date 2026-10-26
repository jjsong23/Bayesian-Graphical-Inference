"""Input adapters and leakage-aware audit for the causal-modeling pilot."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


TIME_SHEETS: tuple[tuple[str, float, int], ...] = (
    ("1 min", 1.0, 1),
    ("2 min", 2.0, 2),
    ("5 min", 5.0, 3),
    ("15 min", 15.0, 4),
)
GENE_COLUMN = "Gene Symbol"
UNIPROT_COLUMN = "Protein UniProt ID"
SITE_COLUMN = "Position in Protein (A)"


def normalize_symbol(value: object) -> str:
    text = str(value).strip()
    if not text or text.casefold() in {"nan", "none", "null", "na"}:
        return ""
    return text.upper()


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(block_size):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class SiteObservation:
    gene: str
    uniprot: str
    site: str
    values: np.ndarray  # time x biological replicate, log2(treated/control)
    intensities: np.ndarray

    @property
    def feature_id(self) -> str:
        return f"{self.uniprot}|{self.site}"


@dataclass
class PhosphoDataset:
    times: np.ndarray
    sites_by_gene: dict[str, list[SiteObservation]]
    source_path: Path

    @property
    def genes(self) -> set[str]:
        return set(self.sites_by_gene)

    @property
    def feature_count(self) -> int:
        return sum(len(records) for records in self.sites_by_gene.values())

    def choose_site(
        self,
        gene: str,
        training_time_indices: Iterable[int] | None = None,
    ) -> SiteObservation:
        symbol = normalize_symbol(gene)
        records = self.sites_by_gene.get(symbol, [])
        if not records:
            raise KeyError(f"no phosphosite trajectory for {gene}")
        indices = (
            np.asarray(list(training_time_indices), dtype=int)
            if training_time_indices is not None
            else np.arange(len(self.times), dtype=int)
        )

        def score(record: SiteObservation) -> tuple[float, int, str]:
            selected = record.values[indices]
            observed = np.isfinite(selected)
            count = int(observed.sum())
            magnitude = float(np.nanmean(np.abs(selected))) if count else -math.inf
            return magnitude, count, record.feature_id

        return max(records, key=score)

    def matrix_for_genes(
        self,
        genes: Iterable[str],
        training_time_indices: Iterable[int] | None = None,
    ) -> tuple[np.ndarray, dict[str, str]]:
        symbols = [normalize_symbol(gene) for gene in genes]
        matrix = np.full((len(self.times), 3, len(symbols)), np.nan, dtype=float)
        selected_sites: dict[str, str] = {}
        for column, symbol in enumerate(symbols):
            record = self.choose_site(symbol, training_time_indices)
            matrix[:, :, column] = record.values
            selected_sites[symbol] = record.feature_id
        return matrix, selected_sites


def load_phospho_dataset(path: str | Path) -> PhosphoDataset:
    """Read replicate-level dDAVP/vehicle ratios without mutating the source."""

    source = Path(path).resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    records: dict[tuple[str, str], dict[str, object]] = {}
    for time_index, (sheet, minutes, sheet_index) in enumerate(TIME_SHEETS):
        frame = pd.read_excel(source, sheet_name=sheet)
        control_columns = [f"C{sheet_index}_Replicate{replicate}_1" for replicate in (1, 2, 3)]
        treated_columns = [f"V{sheet_index}_Replicate{replicate}_1" for replicate in (1, 2, 3)]
        required = {GENE_COLUMN, UNIPROT_COLUMN, SITE_COLUMN, *control_columns, *treated_columns}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"{sheet!r} is missing columns: {', '.join(sorted(missing))}")
        controls = frame[control_columns].apply(pd.to_numeric, errors="coerce").to_numpy(float)
        treated = frame[treated_columns].apply(pd.to_numeric, errors="coerce").to_numpy(float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratios = np.where((controls > 0) & (treated > 0), np.log2(treated / controls), np.nan)
        reporters = np.where(np.concatenate([controls, treated], axis=1) > 0,
                             np.concatenate([controls, treated], axis=1), np.nan)
        intensities = np.nanmedian(reporters, axis=1)
        for row_number, row in frame.iterrows():
            gene = normalize_symbol(row[GENE_COLUMN])
            uniprot = str(row[UNIPROT_COLUMN]).strip()
            site = str(row[SITE_COLUMN]).strip()
            if not gene or not uniprot or uniprot.casefold() == "nan" or not site:
                continue
            key = (uniprot, site)
            item = records.setdefault(
                key,
                {
                    "gene": gene,
                    "uniprot": uniprot,
                    "site": site,
                    "values": np.full((len(TIME_SHEETS), 3), np.nan),
                    "intensities": np.full(len(TIME_SHEETS), np.nan),
                },
            )
            item["values"][time_index, :] = ratios[row_number, :]
            item["intensities"][time_index] = intensities[row_number]

    sites_by_gene: dict[str, list[SiteObservation]] = {}
    for item in records.values():
        observation = SiteObservation(
            gene=str(item["gene"]),
            uniprot=str(item["uniprot"]),
            site=str(item["site"]),
            values=np.asarray(item["values"], dtype=float),
            intensities=np.asarray(item["intensities"], dtype=float),
        )
        sites_by_gene.setdefault(observation.gene, []).append(observation)
    return PhosphoDataset(
        times=np.asarray([minutes for _, minutes, _ in TIME_SHEETS], dtype=float),
        sites_by_gene=sites_by_gene,
        source_path=source,
    )


def _workbook_inventory(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    workbook = pd.ExcelFile(path)
    sheets: list[dict[str, object]] = []
    for sheet in workbook.sheet_names:
        frame = pd.read_excel(path, sheet_name=sheet)
        sheets.append(
            {
                "sheet": sheet,
                "rows": int(len(frame)),
                "columns": int(len(frame.columns)),
                "column_names": [str(column) for column in frame.columns],
            }
        )
    return {
        "path": str(path.resolve()),
        "exists": True,
        "sha256": sha256_file(path),
        "sheets": sheets,
    }


def audit_inputs(
    phospho_path: str | Path,
    ko_paths: Iterable[str | Path],
    output_directory: str | Path,
) -> dict[str, object]:
    """Write a machine-readable audit and logical sample/feature contracts."""

    phospho = Path(phospho_path).resolve()
    output = Path(output_directory).resolve()
    output.mkdir(parents=True, exist_ok=True)
    dataset = load_phospho_dataset(phospho)

    sample_rows: list[dict[str, object]] = []
    for sheet, minutes, sheet_index in TIME_SHEETS:
        for biological_replicate in (1, 2, 3):
            for treatment, prefix in (("vehicle", "C"), ("dDAVP", "V")):
                sample_rows.append(
                    {
                        "sample_id": f"{prefix}{sheet_index}_Replicate{biological_replicate}_1",
                        "experiment_id": "Kirby_Leo_dDAVP_time_series",
                        "cell_context": "rat_inner_medullary_collecting_duct",
                        "genotype": "intact",
                        "intervention_id": "none",
                        "treatment": treatment,
                        "dose": "not_encoded_in_matrix",
                        "time": minutes,
                        "time_unit": "minute",
                        "biological_replicate": biological_replicate,
                        "clone_or_donor": "not_reported_in_matrix",
                        "batch": sheet,
                        "reference_id": f"C{sheet_index}_Replicate{biological_replicate}_1",
                    }
                )
    pd.DataFrame(sample_rows).to_csv(output / "samples.tsv", sep="\t", index=False)

    feature_rows: list[dict[str, object]] = []
    measurement_rows: list[dict[str, object]] = []
    for gene, observations in sorted(dataset.sites_by_gene.items()):
        for observation in observations:
            feature_rows.append(
                {
                    "feature_id": observation.feature_id,
                    "species": "Rattus norvegicus",
                    "protein_symbol": gene,
                    "uniprot": observation.uniprot,
                    "residue_position": observation.site,
                    "localization_confidence": "not_encoded_in_adapter",
                    "total_protein_link": "available_separately_not_joined",
                    "compartment": "not_encoded_in_measurement_matrix",
                    "assay_type": "TMT_phosphoproteomics",
                    "measurement_scale": "log2_treated_over_time_matched_vehicle",
                }
            )
            for time_index, (_, _, sheet_index) in enumerate(TIME_SHEETS):
                for replicate_index in range(3):
                    value = float(observation.values[time_index, replicate_index])
                    measured = math.isfinite(value)
                    measurement_rows.append(
                        {
                            "sample_id": f"V{sheet_index}_Replicate{replicate_index + 1}_1",
                            "feature_id": observation.feature_id,
                            "value": value if measured else np.nan,
                            "measurement_scale": "log2_dDAVP_over_time_matched_vehicle",
                            "uncertainty": "estimated_across_biological_replicates_during_model_fit",
                            "measured": measured,
                            "missingness_reason": "" if measured else "nonpositive_or_missing_matched_reporter_value",
                        }
                    )
    pd.DataFrame(feature_rows).to_csv(output / "features.tsv", sep="\t", index=False)
    pd.DataFrame(measurement_rows).to_csv(
        output / "measurements.tsv.gz", sep="\t", index=False, compression="gzip"
    )

    inventories = [_workbook_inventory(Path(path).resolve()) for path in ko_paths]
    audit: dict[str, object] = {
        "schema_version": 1,
        "status": "exploratory_conditional_validation_only",
        "time_series": {
            "path": str(phospho),
            "sha256": sha256_file(phospho),
            "time_minutes": dataset.times.tolist(),
            "biological_replicates_per_condition_time": 3,
            "sample_count": len(sample_rows),
            "feature_count": dataset.feature_count,
            "gene_count": len(dataset.genes),
            "contrast_measurement_count": len(measurement_rows),
            "observed_contrast_measurement_count": int(sum(bool(row["measured"]) for row in measurement_rows)),
            "measurement_scale": "log2(dDAVP/time-matched vehicle) from positive _1 reporter values",
            "sampling_model": "destructive independent population samples; replicate labels are not longitudinal lineages",
            "dose": "not encoded in the matrix adapter",
        },
        "ko_workbooks": inventories,
        "validation_eligibility": {
            "time_series": "replicate_level_available_but_already_used_for_V1_temporal_diagnostics",
            "ko_workbooks": "summary_contrasts_only_in_local_files; not zero-shot replicate-level validation",
            "clean_split_available": False,
        },
        "leakage_findings": [
            "PKA-KO phosphoprotein and kinase-response summaries contributed to V1 node evidence.",
            "The dDAVP time series is already available to V1 temporal diagnostics; site selection must occur inside each training fold.",
            "No fold-aware rerun of the complete V1 evidence integration is currently exposed by this pilot.",
        ],
        "interpretation": (
            "Scores from this pilot compare V1-supplied hypotheses conditionally. They are not calibrated graph "
            "posteriors and are not clean prospective validation."
        ),
    }
    (output / "data_audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
    return audit
