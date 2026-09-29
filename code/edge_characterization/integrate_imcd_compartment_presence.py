#!/usr/bin/env python3
"""Build basal and dDAVP IMCD compartment co-detection edge evidence.

The two NHLBI workbooks describe rat IMCD cytoplasmic and nuclear fractions.
Spectral counts are converted only to presence/absence.  Count magnitude,
ratios, fold changes, and p-values never enter the edge factor.  Rat symbols
are mapped to mouse through the same Ensembl release 116 orthology table used
by collecting-duct node selection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = PROJECT_ROOT / "data/edge_characterization/imcd_compartment_presence/raw"
PROCESSED_DIR = (
    PROJECT_ROOT / "data/edge_characterization/imcd_compartment_presence/processed"
)
FACTOR_DIR = PROJECT_ROOT / "results/backend_bayes_factor_catalogs/edge_factors_891"
ORTHOLOGY_PATH = (
    PROJECT_ROOT
    / "data/node_selection/collecting_duct/raw/ensembl116_rat_mouse_orthologs.tsv"
)
UNIVERSE_PATH = (
    PROJECT_ROOT / "data/node_selection/node_universe_combined_nonzero.tsv"
)
SOURCE_URLS = {
    "cytoplasm": (
        "https://esbl.nhlbi.nih.gov/Databases/IMCDCytoplasm/"
        "IMCD%20cytoplasm%20proteome.xlsx"
    ),
    "nucleus": (
        "https://esbl.nhlbi.nih.gov/Databases/IMCD_Nucleus/"
        "IMCD_Nuclei_Database.xlsx"
    ),
}
DEFAULT_SUPPORT_LIKELIHOOD = 0.75
NEUTRAL_LIKELIHOOD = 0.5


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, default=RAW_DIR)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--factor-dir", type=Path, default=FACTOR_DIR)
    parser.add_argument("--orthology", type=Path, default=ORTHOLOGY_PATH)
    parser.add_argument("--universe", type=Path, default=UNIVERSE_PATH)
    parser.add_argument(
        "--support-likelihood",
        type=float,
        default=DEFAULT_SUPPORT_LIKELIHOOD,
        help="Likelihood for a co-detected pair; BF = likelihood / 0.5.",
    )
    args = parser.parse_args(argv)
    if not NEUTRAL_LIKELIHOOD <= args.support_likelihood < 1.0:
        parser.error("--support-likelihood must be in [0.5, 1)")
    return args


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def presence(value: object) -> bool:
    number = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return bool(pd.notna(number) and float(number) > 0)


def load_cytoplasm(path: Path) -> pd.DataFrame:
    frame = pd.read_excel(path, sheet_name="Summary for Web", header=2)
    frame.columns = [str(column).replace("\u00a0", " ").strip() for column in frame.columns]
    required = {"Gene Symbol", "Control Spectral Counts", "dDAVP Spectral Counts"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"cytoplasm workbook columns missing: {missing}")
    frame = frame.loc[:, list(required)].copy()
    frame["rat_symbol"] = frame["Gene Symbol"].fillna("").astype(str).str.strip()
    frame = frame.loc[frame["rat_symbol"].ne("")].copy()
    frame["cytoplasm_basal"] = frame["Control Spectral Counts"].map(presence)
    frame["cytoplasm_ddavp"] = frame["dDAVP Spectral Counts"].map(presence)
    return frame[
        [
            "rat_symbol",
            "Control Spectral Counts",
            "dDAVP Spectral Counts",
            "cytoplasm_basal",
            "cytoplasm_ddavp",
        ]
    ]


def load_nucleus(path: Path) -> pd.DataFrame:
    # This sheet, like the cytoplasmic resource, requires at least two distinct
    # peptides and therefore avoids mixing different identification standards.
    frame = pd.read_excel(path, sheet_name="IMCD Two Peptides", header=1)
    frame.columns = [str(column).replace("\u00a0", " ").strip() for column in frame.columns]
    required = {
        "Gene Symbol",
        "Extract CT Count",
        "Extract dDAVP Count",
        "Pellet CT Count",
        "Pellet dDAVP Count",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"nuclear workbook columns missing: {missing}")
    frame = frame.loc[:, list(required)].copy()
    frame["rat_symbol"] = frame["Gene Symbol"].fillna("").astype(str).str.strip()
    frame = frame.loc[frame["rat_symbol"].ne("")].copy()
    frame["nucleus_basal"] = frame.apply(
        lambda row: presence(row["Extract CT Count"])
        or presence(row["Pellet CT Count"]),
        axis=1,
    )
    frame["nucleus_ddavp"] = frame.apply(
        lambda row: presence(row["Extract dDAVP Count"])
        or presence(row["Pellet dDAVP Count"]),
        axis=1,
    )
    return frame[
        [
            "rat_symbol",
            "Extract CT Count",
            "Extract dDAVP Count",
            "Pellet CT Count",
            "Pellet dDAVP Count",
            "nucleus_basal",
            "nucleus_ddavp",
        ]
    ]


def load_orthology(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep="\t", dtype=str).fillna("")
    frame.columns = [
        "rat_ensembl_gene_id",
        "rat_gene_symbol",
        "mouse_ensembl_gene_id",
        "mouse_gene_symbol",
        "orthology_type",
        "mouse_percent_identity",
        "rat_percent_identity",
        "orthology_confidence",
    ]
    frame["rat_symbol_key"] = (
        frame["rat_gene_symbol"].astype(str).str.strip().str.casefold()
    )
    frame["mouse_gene_symbol"] = frame["mouse_gene_symbol"].astype(str).str.strip()
    return frame


def map_rat_symbols(
    source_symbols: set[str],
    orthology: pd.DataFrame,
    canonical_mouse: dict[str, str],
) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    grouped = {
        key: rows.copy()
        for key, rows in orthology.groupby("rat_symbol_key", sort=False)
        if key
    }
    for rat_symbol in sorted(source_symbols, key=str.casefold):
        rows = grouped.get(rat_symbol.casefold(), orthology.iloc[0:0]).copy()
        rows = rows.loc[rows["mouse_gene_symbol"].ne("")]
        if not rows.empty and rows["orthology_confidence"].eq("1").any():
            rows = rows.loc[rows["orthology_confidence"].eq("1")]
            selection = "high_confidence_preferred"
        elif not rows.empty:
            selection = "low_confidence_fallback"
        else:
            selection = "no_named_mouse_ortholog"
        if rows.empty:
            records.append(
                {
                    "rat_symbol": rat_symbol,
                    "mouse_symbol_ensembl": "",
                    "mouse_symbol": "",
                    "mapping_status": selection,
                    "orthology_type": "",
                    "orthology_confidence": "",
                    "in_seed_universe": False,
                }
            )
            continue
        rows = rows.drop_duplicates(["mouse_ensembl_gene_id", "mouse_gene_symbol"])
        for row in rows.itertuples(index=False):
            mouse_symbol = canonical_mouse.get(str(row.mouse_gene_symbol).casefold(), "")
            records.append(
                {
                    "rat_symbol": rat_symbol,
                    "mouse_symbol_ensembl": row.mouse_gene_symbol,
                    "mouse_symbol": mouse_symbol or row.mouse_gene_symbol,
                    "mapping_status": selection,
                    "orthology_type": row.orthology_type,
                    "orthology_confidence": row.orthology_confidence,
                    "in_seed_universe": bool(mouse_symbol),
                }
            )
    return pd.DataFrame.from_records(records)


def build_profiles(
    cytoplasm: pd.DataFrame,
    nucleus: pd.DataFrame,
    mapping: pd.DataFrame,
) -> pd.DataFrame:
    cyt = cytoplasm.merge(mapping, on="rat_symbol", how="left")
    nuc = nucleus.merge(mapping, on="rat_symbol", how="left")
    fields = [
        "cytoplasm_basal",
        "cytoplasm_ddavp",
        "nucleus_basal",
        "nucleus_ddavp",
    ]
    long = cyt[["rat_symbol", "mouse_symbol", "in_seed_universe", *fields[:2]]].merge(
        nuc[["rat_symbol", "mouse_symbol", "in_seed_universe", *fields[2:]]],
        on=["rat_symbol", "mouse_symbol"],
        how="outer",
        suffixes=("_cytoplasm", "_nucleus"),
    )
    long = long.loc[long["mouse_symbol"].fillna("").astype(str).str.strip().ne("")].copy()
    for field in fields:
        long[field] = long[field].fillna(False).astype(bool)
    long["in_seed_universe"] = (
        long["in_seed_universe_cytoplasm"].fillna(False).astype(bool)
        | long["in_seed_universe_nucleus"].fillna(False).astype(bool)
    )
    profiles = (
        long.groupby("mouse_symbol", as_index=False)
        .agg(
            rat_symbols=("rat_symbol", lambda values: ";".join(sorted(set(map(str, values))))),
            in_seed_universe=("in_seed_universe", "max"),
            cytoplasm_basal=("cytoplasm_basal", "max"),
            cytoplasm_ddavp=("cytoplasm_ddavp", "max"),
            nucleus_basal=("nucleus_basal", "max"),
            nucleus_ddavp=("nucleus_ddavp", "max"),
        )
        .rename(columns={"mouse_symbol": "symbol"})
    )
    profiles["basal_profile_observed"] = profiles[
        ["cytoplasm_basal", "nucleus_basal"]
    ].any(axis=1)
    profiles["ddavp_profile_observed"] = profiles[
        ["cytoplasm_ddavp", "nucleus_ddavp"]
    ].any(axis=1)
    profiles["basal_compartments"] = profiles.apply(
        lambda row: ";".join(
            compartment
            for compartment, column in (
                ("cytoplasm", "cytoplasm_basal"),
                ("nucleus", "nucleus_basal"),
            )
            if bool(row[column])
        ),
        axis=1,
    )
    profiles["ddavp_compartments"] = profiles.apply(
        lambda row: ";".join(
            compartment
            for compartment, column in (
                ("cytoplasm", "cytoplasm_ddavp"),
                ("nucleus", "nucleus_ddavp"),
            )
            if bool(row[column])
        ),
        axis=1,
    )
    return profiles.sort_values("symbol", key=lambda values: values.str.casefold())


def build_pair_catalog(
    profiles: pd.DataFrame,
    universe_symbols: list[str],
    support_likelihood: float,
) -> pd.DataFrame:
    by_symbol = profiles.set_index("symbol")
    available = [symbol for symbol in universe_symbols if symbol in by_symbol.index]
    bayes_factor = support_likelihood / NEUTRAL_LIKELIHOOD
    records: list[dict[str, Any]] = []
    for left, right in combinations(available, 2):
        a = by_symbol.loc[left]
        b = by_symbol.loc[right]
        basal_shared = [
            compartment
            for compartment, column in (
                ("cytoplasm", "cytoplasm_basal"),
                ("nucleus", "nucleus_basal"),
            )
            if bool(a[column]) and bool(b[column])
        ]
        ddavp_shared = [
            compartment
            for compartment, column in (
                ("cytoplasm", "cytoplasm_ddavp"),
                ("nucleus", "nucleus_ddavp"),
            )
            if bool(a[column]) and bool(b[column])
        ]
        if not basal_shared and not ddavp_shared:
            continue
        records.append(
            {
                "node_a": left,
                "node_b": right,
                "basal_colocalized": bool(basal_shared),
                "basal_shared_compartments": ";".join(basal_shared),
                "ddavp_colocalized": bool(ddavp_shared),
                "ddavp_shared_compartments": ";".join(ddavp_shared),
                "support_likelihood": support_likelihood,
                "bayes_factor": bayes_factor,
            }
        )
    return pd.DataFrame.from_records(records)


def build(args: argparse.Namespace) -> dict[str, Any]:
    raw_dir = args.raw_dir.resolve()
    processed_dir = args.processed_dir.resolve()
    factor_dir = args.factor_dir.resolve()
    processed_dir.mkdir(parents=True, exist_ok=True)
    factor_dir.mkdir(parents=True, exist_ok=True)
    cytoplasm_path = raw_dir / "IMCD_cytoplasm_proteome.xlsx"
    nucleus_path = raw_dir / "IMCD_Nuclei_Database.xlsx"
    for path in (cytoplasm_path, nucleus_path, args.orthology, args.universe):
        if not path.is_file():
            raise FileNotFoundError(path)

    universe = pd.read_csv(args.universe, sep="\t", dtype=str).fillna("")
    universe_symbols = universe["symbol"].drop_duplicates().astype(str).tolist()
    canonical_mouse = {symbol.casefold(): symbol for symbol in universe_symbols}
    cytoplasm = load_cytoplasm(cytoplasm_path)
    nucleus = load_nucleus(nucleus_path)
    orthology = load_orthology(args.orthology)
    source_symbols = set(cytoplasm["rat_symbol"]).union(nucleus["rat_symbol"])
    mapping = map_rat_symbols(source_symbols, orthology, canonical_mouse)
    profiles = build_profiles(cytoplasm, nucleus, mapping)
    pairs = build_pair_catalog(profiles, universe_symbols, args.support_likelihood)

    cytoplasm.to_csv(processed_dir / "cytoplasm_presence_audit.tsv", sep="\t", index=False)
    nucleus.to_csv(processed_dir / "nucleus_presence_audit.tsv", sep="\t", index=False)
    mapping.to_csv(processed_dir / "rat_mouse_mapping_audit.tsv", sep="\t", index=False)
    profiles.to_csv(processed_dir / "imcd_compartment_node_profiles.tsv", sep="\t", index=False)
    pairs.to_csv(
        processed_dir / "imcd_compartment_pair_catalog.tsv.gz",
        sep="\t",
        index=False,
        compression="gzip",
    )
    basal = pairs.loc[pairs["basal_colocalized"], ["node_a", "node_b", "bayes_factor"]]
    ddavp = pairs.loc[pairs["ddavp_colocalized"], ["node_a", "node_b", "bayes_factor"]]
    basal.to_csv(
        factor_dir / "imcd_basal_compartment_presence_bf_gt1.tsv.gz",
        sep="\t",
        index=False,
        compression="gzip",
    )
    ddavp.to_csv(
        factor_dir / "imcd_ddavp_compartment_presence_bf_gt1.tsv.gz",
        sep="\t",
        index=False,
        compression="gzip",
    )

    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "presence-only co-detection in cytoplasm or nucleus",
        "nuclear_sheet": "IMCD Two Peptides",
        "nuclear_fraction_rule": "nuclear extract OR nuclear pellet",
        "condition_rule": "basal and 30-minute dDAVP scored separately",
        "support_likelihood": float(args.support_likelihood),
        "support_bayes_factor": float(args.support_likelihood / NEUTRAL_LIKELIHOOD),
        "cytoplasm_rat_proteins": int(len(cytoplasm)),
        "nucleus_rat_proteins": int(len(nucleus)),
        "distinct_rat_symbols": int(len(source_symbols)),
        "mapped_mouse_symbols": int(len(profiles)),
        "mapped_seed_universe_symbols": int(profiles["in_seed_universe"].sum()),
        "basal_profiled_seed_nodes": int(
            profiles.loc[profiles["in_seed_universe"], "basal_profile_observed"].sum()
        ),
        "ddavp_profiled_seed_nodes": int(
            profiles.loc[profiles["in_seed_universe"], "ddavp_profile_observed"].sum()
        ),
        "basal_supported_seed_pairs": int(len(basal)),
        "ddavp_supported_seed_pairs": int(len(ddavp)),
        "raw_sources": {
            "cytoplasm": {
                "path": str(cytoplasm_path),
                "url": SOURCE_URLS["cytoplasm"],
                "sha256": sha256_file(cytoplasm_path),
            },
            "nucleus": {
                "path": str(nucleus_path),
                "url": SOURCE_URLS["nucleus"],
                "sha256": sha256_file(nucleus_path),
            },
        },
        "orthology": str(args.orthology.resolve()),
        "universe": str(args.universe.resolve()),
    }
    (processed_dir / "processing_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    summary = build(parse_args(argv))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
