#!/usr/bin/env python3
"""Build auditable BioGRID direct-binding and co-complex PPI catalogs.

The implementation follows ``docs/biogrid_ppi_evidence.md``.  It processes
the large Tab3 release in chunks and stages normalized rows in SQLite, keeping
peak Python memory independent of the number of raw BioGRID records.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import re
import sqlite3
import sys
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, TextIO

import numpy as np
import pandas as pd


LATEST_URL = (
    "https://downloads.thebiogrid.org/Download/BioGRID/Latest-Release/"
    "BIOGRID-ALL-LATEST.tab3.zip"
)
ARCHIVE_INDEX_URL = "https://downloads.thebiogrid.org/BioGRID/Release-Archive/"
SPECIES = {"9606": "human", "10090": "mouse", "10116": "rat"}
SPECIES_ORDER = ["human", "mouse", "rat", "cross-species"]
HURI_PUBMED_ID = "32296183"
PUBLISHED_5_0_261 = {"human": 1_136_336, "mouse": 102_434, "rat": 10_770}
PUBLISHED_RAW_5_0_261 = {
    "human": 1_488_630,
    "mouse": 112_684,
    "rat": 12_267,
}

# Edit this one mapping to revise assay classification. Keys are normalized by
# stripping whitespace and applying casefold(). Both Cross-linking-MS spellings
# are admitted so the exact release spelling remains visible in the audit.
SYSTEM_TIER = {
    "two-hybrid": "direct",
    "co-crystal structure": "direct",
    "pca": "direct",
    "far western": "direct",
    "protein-peptide": "direct",
    "cross-linking-ms": "direct",
    "cross-linking-ms (xl-ms)": "direct",
    "reconstituted complex": "direct",  # configurable below
    "affinity capture-ms": "cocomplex",
    "affinity capture-western": "cocomplex",
    "affinity capture-luminescence": "cocomplex",
    "co-purification": "cocomplex",
    "co-fractionation": "cocomplex",
}

REQUIRED_COLUMNS = [
    "BioGRID ID Interactor A",
    "BioGRID ID Interactor B",
    "Entrez Gene Interactor A",
    "Entrez Gene Interactor B",
    "Official Symbol Interactor A",
    "Official Symbol Interactor B",
    "Experimental System",
    "Experimental System Type",
    "Publication Source",
    "Organism ID Interactor A",
    "Organism ID Interactor B",
    "Throughput",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="BioGRID Tab3 .zip or uncompressed .txt file.",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download the latest Tab3 archive before processing.",
    )
    parser.add_argument(
        "--reconstituted",
        choices=("direct", "cocomplex"),
        default="direct",
        help="Tier for Reconstituted Complex (default: direct).",
    )
    parser.add_argument(
        "--drop-self",
        action="store_true",
        help="Exclude BioGRID-ID self interactions; retained by default.",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=Path("biogrid_ppi_output"),
        help="Output directory (default: biogrid_ppi_output).",
    )
    parser.add_argument(
        "--chunksize",
        type=int,
        default=250_000,
        help="Tab3 rows per pandas chunk (default: 250000).",
    )
    parser.add_argument(
        "--skip-parquet",
        action="store_true",
        help="Development fallback only; omit the required Parquet copy.",
    )
    parser.add_argument(
        "--keep-work-db",
        action="store_true",
        help="Retain the intermediate SQLite database for debugging.",
    )
    args = parser.parse_args(argv)
    if args.download and args.path is not None:
        parser.error("provide either PATH or --download, not both")
    if not args.download and args.path is None:
        parser.error("PATH is required unless --download is used")
    if args.chunksize < 1:
        parser.error("--chunksize must be positive")
    return args


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download_file(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Graphical-Bayesian-Inference/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        with destination.open("wb") as output:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                output.write(block)


def _newest_archive_url() -> str:
    request = urllib.request.Request(
        ARCHIVE_INDEX_URL,
        headers={"User-Agent": "Graphical-Bayesian-Inference/1.0"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        listing = response.read().decode("utf-8", errors="replace")
    versions = set(re.findall(r"BIOGRID-([0-9]+(?:\.[0-9]+)+)", listing))
    if not versions:
        raise RuntimeError("no BioGRID releases were found in the archive index")
    newest = max(versions, key=lambda value: tuple(map(int, value.split("."))))
    return (
        "https://downloads.thebiogrid.org/Download/BioGRID/Release-Archive/"
        f"BIOGRID-{newest}/BIOGRID-ALL-{newest}.tab3.zip"
    )


def download_latest(outdir: Path) -> tuple[Path, str]:
    destination = outdir / "BIOGRID-ALL-LATEST.tab3.zip"
    selected_url = LATEST_URL
    try:
        _download_file(selected_url, destination)
    except (urllib.error.URLError, TimeoutError, OSError):
        destination.unlink(missing_ok=True)
        try:
            selected_url = _newest_archive_url()
            _download_file(selected_url, destination)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError(
                "BioGRID Latest-Release and release-archive downloads failed. "
                "Download the newest BIOGRID-ALL-*.tab3.zip manually from "
                f"{ARCHIVE_INDEX_URL} and pass its path to this script."
            ) from exc
    if not zipfile.is_zipfile(destination):
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"BioGRID download was not a valid zip archive: {selected_url}")
    return destination, selected_url


@contextlib.contextmanager
def open_tab3(path: Path) -> Iterator[tuple[str, TextIO]]:
    if path.suffix.casefold() == ".zip":
        with zipfile.ZipFile(path) as archive:
            members = [
                name for name in archive.namelist() if name.casefold().endswith(".txt")
            ]
            if not members:
                raise ValueError(f"no .txt member found in {path}")
            member = sorted(members)[0]
            with archive.open(member) as binary:
                import io

                with io.TextIOWrapper(
                    binary, encoding="utf-8-sig", errors="replace", newline=""
                ) as handle:
                    yield member, handle
    else:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            yield path.name, handle


def build_from_member(member: str) -> str:
    match = re.search(r"BIOGRID-ALL-([0-9]+(?:\.[0-9]+)+)\.tab3", member)
    return match.group(1) if match else "unknown"


def header_columns(path: Path) -> tuple[str, list[str]]:
    with open_tab3(path) as (member, handle):
        header = handle.readline().rstrip("\r\n").split("\t")
    if header:
        header[0] = header[0].lstrip("#\ufeff")
    return member, header


def canonical_ids(chunk: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
    left = pd.to_numeric(chunk["BioGRID ID Interactor A"], errors="coerce")
    right = pd.to_numeric(chunk["BioGRID ID Interactor B"], errors="coerce")
    valid = left.notna() & right.notna()
    left_int = left.fillna(-1).astype("int64")
    right_int = right.fillna(-1).astype("int64")
    pair_a = pd.Series(np.minimum(left_int, right_int), index=chunk.index).astype(str)
    pair_b = pd.Series(np.maximum(left_int, right_int), index=chunk.index).astype(str)
    return pair_a, pair_b, valid


def oriented_values(
    chunk: pd.DataFrame,
    pair_a: pd.Series,
    column_a: str,
    column_b: str,
) -> tuple[pd.Series, pd.Series]:
    original_a = chunk["BioGRID ID Interactor A"].astype(str)
    keep = original_a.eq(pair_a)
    left = chunk[column_a].fillna("").astype(str).where(keep, chunk[column_b].fillna("").astype(str))
    right = chunk[column_b].fillna("").astype(str).where(keep, chunk[column_a].fillna("").astype(str))
    return left, right


def _append_sqlite(
    frame: pd.DataFrame,
    table: str,
    connection: sqlite3.Connection,
) -> None:
    if not frame.empty:
        frame.to_sql(
            table,
            connection,
            if_exists="append",
            index=False,
            chunksize=5000,
        )


def ingest(
    source: Path,
    connection: sqlite3.Connection,
    *,
    chunksize: int,
    reconstituted: str,
    drop_self: bool,
) -> tuple[str, Counter[str], list[str]]:
    member, columns = header_columns(source)
    missing = sorted(set(REQUIRED_COLUMNS).difference(columns))
    if missing:
        raise ValueError(
            "BioGRID Tab3 columns are missing: "
            + ", ".join(missing)
            + "\nColumns actually found:\n"
            + "\n".join(columns)
        )
    build = build_from_member(member)
    mapping = dict(SYSTEM_TIER)
    mapping["reconstituted complex"] = reconstituted
    counters: Counter[str] = Counter()
    physical_systems: set[str] = set()

    connection.execute("DROP TABLE IF EXISTS records")
    connection.execute("DROP TABLE IF EXISTS published_membership")
    with open_tab3(source) as (_, handle):
        reader = pd.read_csv(
            handle,
            sep="\t",
            usecols=REQUIRED_COLUMNS,
            dtype=str,
            chunksize=chunksize,
            low_memory=False,
        )
        for chunk_number, chunk in enumerate(reader, start=1):
            counters["total_rows"] += len(chunk)
            type_normalized = (
                chunk["Experimental System Type"].fillna("").str.strip().str.casefold()
            )
            chunk = chunk.loc[type_normalized.eq("physical")].copy()
            counters["physical_rows"] += len(chunk)
            if chunk.empty:
                continue

            chunk["system_normalized"] = (
                chunk["Experimental System"].fillna("").str.strip().str.casefold()
            )
            pair_a, pair_b, valid_ids = canonical_ids(chunk)
            counters["physical_rows_missing_biogrid_id"] += int((~valid_ids).sum())
            chunk = chunk.loc[valid_ids].copy()
            pair_a = pair_a.loc[valid_ids]
            pair_b = pair_b.loc[valid_ids]
            chunk["pair_a"] = pair_a
            chunk["pair_b"] = pair_b
            chunk["pair_key"] = pair_a + "|" + pair_b
            chunk["is_self"] = pair_a.eq(pair_b)
            if drop_self:
                counters["self_rows_dropped"] += int(chunk["is_self"].sum())
                chunk = chunk.loc[~chunk["is_self"]].copy()
            counters["physical_rows_after_self_filter"] += len(chunk)
            if chunk.empty:
                continue

            org_a = chunk["Organism ID Interactor A"].fillna("").str.strip()
            org_b = chunk["Organism ID Interactor B"].fillna("").str.strip()
            for taxid, species in SPECIES.items():
                mask = org_a.eq(taxid) | org_b.eq(taxid)
                counters[f"physical_raw_either_{species}"] += int(mask.sum())
                membership = chunk.loc[mask, ["pair_key"]].drop_duplicates()
                membership.insert(0, "species", species)
                _append_sqlite(membership, "published_membership", connection)

            both_target = org_a.isin(SPECIES) & org_b.isin(SPECIES)
            counters["physical_rows_with_either_target_species"] += int(
                (org_a.isin(SPECIES) | org_b.isin(SPECIES)).sum()
            )
            counters["physical_rows_with_both_target_species"] += int(both_target.sum())
            selected = chunk.loc[both_target].copy()
            if selected.empty:
                if chunk_number % 10 == 0:
                    print(f"Processed {counters['total_rows']:,} raw rows...", flush=True)
                continue

            physical_systems.update(
                selected["Experimental System"]
                .fillna("")
                .str.strip()
                .unique()
                .tolist()
            )

            org_a_selected = org_a.loc[both_target]
            org_b_selected = org_b.loc[both_target]
            same_species = org_a_selected.eq(org_b_selected)
            species = org_a_selected.map(SPECIES).where(same_species, "cross-species")
            selected["species"] = species
            selected["tier"] = selected["system_normalized"].map(mapping).fillna("excluded")
            counters["tier_assigned_rows"] += int(selected["tier"].ne("excluded").sum())
            counters["excluded_system_rows"] += int(selected["tier"].eq("excluded").sum())

            selected["entrez_a"], selected["entrez_b"] = oriented_values(
                selected,
                selected["pair_a"],
                "Entrez Gene Interactor A",
                "Entrez Gene Interactor B",
            )
            selected["symbol_a"], selected["symbol_b"] = oriented_values(
                selected,
                selected["pair_a"],
                "Official Symbol Interactor A",
                "Official Symbol Interactor B",
            )
            selected["system"] = selected["Experimental System"].fillna("").str.strip()
            selected["publication_source"] = selected["Publication Source"].fillna("").str.strip()
            selected["pubmed_id"] = selected["publication_source"].str.extract(
                r"(?i)(?:PUBMED:)?([0-9]+)", expand=False
            ).fillna("")
            selected["throughput"] = selected["Throughput"].fillna("").str.strip()
            selected["any_low_throughput"] = (
                selected["throughput"].str.casefold().eq("low throughput").astype(int)
            )
            selected["is_self"] = selected["is_self"].astype(int)
            staged = selected[
                [
                    "pair_key",
                    "pair_a",
                    "pair_b",
                    "entrez_a",
                    "entrez_b",
                    "symbol_a",
                    "symbol_b",
                    "species",
                    "system",
                    "system_normalized",
                    "tier",
                    "publication_source",
                    "pubmed_id",
                    "throughput",
                    "any_low_throughput",
                    "is_self",
                ]
            ]
            _append_sqlite(staged, "records", connection)
            connection.commit()
            if chunk_number % 5 == 0:
                print(f"Processed {counters['total_rows']:,} raw rows...", flush=True)

    if not _table_exists(connection, "records"):
        raise ValueError("no physical human/mouse/rat records were retained")
    if not _table_exists(connection, "published_membership"):
        raise ValueError("no species-membership records were retained")
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_records_species_tier
          ON records(species, tier);
        CREATE INDEX IF NOT EXISTS idx_records_pair
          ON records(pair_key);
        CREATE INDEX IF NOT EXISTS idx_records_system
          ON records(system, species, tier);
        CREATE INDEX IF NOT EXISTS idx_membership_species_pair
          ON published_membership(species, pair_key);
        """
    )
    connection.commit()
    return build, counters, sorted(physical_systems, key=str.casefold)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def subset_summary(
    connection: sqlite3.Connection,
    where: str,
    params: tuple[object, ...],
) -> dict[str, int]:
    raw = int(
        connection.execute(
            f"SELECT COUNT(*) FROM records WHERE {where}", params
        ).fetchone()[0]
    )
    row = connection.execute(
        f"""
        WITH pair_support AS (
          SELECT pair_key,
                 COUNT(DISTINCT NULLIF(pubmed_id, '')) AS n_pubs,
                 COUNT(DISTINCT system_normalized) AS n_methods
          FROM records WHERE {where}
          GROUP BY pair_key
        )
        SELECT COUNT(*),
               COALESCE(SUM(n_pubs >= 2), 0),
               COALESCE(SUM(n_methods >= 2), 0)
        FROM pair_support
        """,
        params,
    ).fetchone()
    return {
        "raw": raw,
        "NR": int(row[0]),
        "NR_ge2_pubs": int(row[1]),
        "NR_ge2_methods": int(row[2]),
    }


def build_summary(connection: sqlite3.Connection, build: str) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    groups = [(species, "species = ?", (species,)) for species in SPECIES_ORDER]
    groups.append(
        (
            "all-three-combined",
            "species IN ('human', 'mouse', 'rat')",
            (),
        )
    )
    for label, species_clause, species_params in groups:
        for tier, tier_clause in (
            ("direct", "tier = 'direct'"),
            ("cocomplex", "tier = 'cocomplex'"),
            ("direct+cocomplex", "tier IN ('direct', 'cocomplex')"),
        ):
            values = subset_summary(
                connection,
                f"({species_clause}) AND ({tier_clause})",
                species_params,
            )
            rows.append({"build": build, "species": label, "tier": tier, **values})
    return pd.DataFrame(rows)


def build_by_system(connection: sqlite3.Connection, build: str) -> pd.DataFrame:
    table = pd.read_sql_query(
        """
        SELECT system AS experimental_system, species, tier,
               COUNT(*) AS raw,
               COUNT(DISTINCT pair_key) AS NR
        FROM records
        GROUP BY system, species, tier
        ORDER BY species, tier, raw DESC, system
        """,
        connection,
    )
    table.insert(0, "build", build)
    return table


def _sorted_join(value: object, *, numeric: bool = False) -> str:
    values = {item for item in str(value or "").split(",") if item}
    if numeric:
        return ";".join(sorted(values, key=lambda item: int(item)))
    return ";".join(sorted(values, key=str.casefold))


def build_pairs(connection: sqlite3.Connection, build: str) -> pd.DataFrame:
    pairs = pd.read_sql_query(
        f"""
        SELECT pair_a AS biogrid_id_a,
               pair_b AS biogrid_id_b,
               MAX(CASE WHEN entrez_a NOT IN ('', '-') THEN entrez_a END) AS entrez_a,
               MAX(CASE WHEN entrez_b NOT IN ('', '-') THEN entrez_b END) AS entrez_b,
               MAX(CASE WHEN symbol_a NOT IN ('', '-') THEN symbol_a END) AS symbol_a,
               MAX(CASE WHEN symbol_b NOT IN ('', '-') THEN symbol_b END) AS symbol_b,
               species,
               MAX(tier = 'direct') AS in_direct,
               MAX(tier = 'cocomplex') AS in_cocomplex,
               COUNT(DISTINCT NULLIF(pubmed_id, '')) AS n_pubs,
               COUNT(DISTINCT system_normalized) AS n_methods,
               GROUP_CONCAT(DISTINCT system) AS systems,
               GROUP_CONCAT(DISTINCT NULLIF(pubmed_id, '')) AS pubmed_ids,
               MAX(any_low_throughput) AS any_low_throughput,
               MAX(is_self) AS is_self,
               MAX(pubmed_id = '{HURI_PUBMED_ID}') AS in_huri
        FROM records
        WHERE tier IN ('direct', 'cocomplex')
        GROUP BY species, pair_key
        """,
        connection,
    )
    pairs.insert(0, "build", build)
    pairs["systems"] = pairs["systems"].map(_sorted_join)
    pairs["pubmed_ids"] = pairs["pubmed_ids"].map(
        lambda value: _sorted_join(value, numeric=True)
    )
    for column in ("in_direct", "in_cocomplex", "any_low_throughput", "is_self", "in_huri"):
        pairs[column] = pairs[column].astype(bool)
    pairs[["entrez_a", "entrez_b", "symbol_a", "symbol_b"]] = pairs[
        ["entrez_a", "entrez_b", "symbol_a", "symbol_b"]
    ].fillna("")
    species_order = {
        "human": 0,
        "mouse": 1,
        "rat": 2,
        "cross-species": 3,
    }
    pairs["_species_order"] = pairs["species"].map(species_order)
    pairs["_a"] = pd.to_numeric(pairs["biogrid_id_a"], errors="raise")
    pairs["_b"] = pd.to_numeric(pairs["biogrid_id_b"], errors="raise")
    return pairs.sort_values(
        ["_species_order", "_a", "_b"], kind="stable"
    ).drop(columns=["_species_order", "_a", "_b"]).reset_index(drop=True)


def huri_breakdown(connection: sqlite3.Connection, build: str) -> pd.DataFrame:
    counts = connection.execute(
        f"""
        WITH direct_pairs AS (
          SELECT pair_key,
                 MAX(pubmed_id = '{HURI_PUBMED_ID}') AS in_huri,
                 COUNT(DISTINCT NULLIF(pubmed_id, '')) AS n_pubs
          FROM records
          WHERE species = 'human' AND tier = 'direct'
          GROUP BY pair_key
        )
        SELECT
          SUM(in_huri = 1 AND n_pubs = 1),
          SUM(in_huri = 1 AND n_pubs >= 2),
          SUM(in_huri = 0)
        FROM direct_pairs
        """
    ).fetchone()
    return pd.DataFrame(
        [
            {"build": build, "category": "HuRI only", "NR_pairs": int(counts[0] or 0)},
            {"build": build, "category": "HuRI plus another publication", "NR_pairs": int(counts[1] or 0)},
            {"build": build, "category": "Direct tier not in HuRI", "NR_pairs": int(counts[2] or 0)},
        ]
    )


def sanity_checks(
    connection: sqlite3.Connection,
    build: str,
    pairs: pd.DataFrame,
    counters: Counter[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    published_rows = []
    for species in ("human", "mouse", "rat"):
        observed = int(
            connection.execute(
                "SELECT COUNT(DISTINCT pair_key) FROM published_membership WHERE species = ?",
                (species,),
            ).fetchone()[0]
        )
        expected = PUBLISHED_5_0_261.get(species) if build == "5.0.261" else None
        published_rows.append(
            {
                "build": build,
                "species": species,
                "raw_physical_either_endpoint": int(
                    counters[f"physical_raw_either_{species}"]
                ),
                "published_raw_5_0_261": (
                    PUBLISHED_RAW_5_0_261.get(species)
                    if build == "5.0.261"
                    else None
                ),
                "NR_physical_either_endpoint": observed,
                "published_5_0_261": expected,
                "difference": observed - expected if expected is not None else None,
                "percent_difference": (
                    100.0 * (observed - expected) / expected
                    if expected is not None
                    else None
                ),
            }
        )
    published = pd.DataFrame(published_rows)

    arithmetic_rows = []
    for species in [*SPECIES_ORDER, "all-three-combined"]:
        if species == "all-three-combined":
            subset = pairs.loc[pairs["species"].isin(["human", "mouse", "rat"])]
        else:
            subset = pairs.loc[pairs["species"].eq(species)]
        direct = int(subset["in_direct"].sum())
        cocomplex = int(subset["in_cocomplex"].sum())
        both = int((subset["in_direct"] & subset["in_cocomplex"]).sum())
        union = int(len(subset))
        arithmetic_rows.append(
            {
                "build": build,
                "species": species,
                "NR_direct": direct,
                "NR_cocomplex": cocomplex,
                "NR_both": both,
                "NR_union": union,
                "direct_plus_cocomplex_minus_both": direct + cocomplex - both,
                "passes": direct + cocomplex - both == union,
            }
        )
    arithmetic = pd.DataFrame(arithmetic_rows)
    if not arithmetic["passes"].all():
        raise AssertionError("BioGRID tier arithmetic failed")
    if (
        pd.to_numeric(pairs["biogrid_id_a"], errors="raise")
        > pd.to_numeric(pairs["biogrid_id_b"], errors="raise")
    ).any():
        raise AssertionError("reversed BioGRID pair key found")
    if pairs.duplicated(["species", "biogrid_id_a", "biogrid_id_b"]).any():
        raise AssertionError("duplicate pair/species row found")
    return published, arithmetic


def huri_two_hybrid_count(connection: sqlite3.Connection) -> int:
    return int(
        connection.execute(
            """
            SELECT COUNT(DISTINCT pair_key) FROM records
            WHERE species = 'human'
              AND system_normalized = 'two-hybrid'
              AND pubmed_id = ?
            """,
            (HURI_PUBMED_ID,),
        ).fetchone()[0]
    )


def markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows._"
    values = frame.fillna("").astype(str)
    header = "| " + " | ".join(values.columns) + " |"
    divider = "| " + " | ".join("---" for _ in values.columns) + " |"
    rows = [
        "| "
        + " | ".join(value.replace("|", "\\|") for value in row)
        + " |"
        for row in values.itertuples(index=False, name=None)
    ]
    return "\n".join([header, divider, *rows])


def write_readme(
    path: Path,
    *,
    build: str,
    source: Path,
    source_hash: str,
    reconstituted: str,
    drop_self: bool,
    systems: pd.DataFrame,
    summary: pd.DataFrame,
    huri: pd.DataFrame,
    published: pd.DataFrame,
    arithmetic: pd.DataFrame,
    huri_count: int,
    counters: Counter[str],
) -> None:
    mapping = systems[["experimental_system", "tier"]].drop_duplicates().sort_values(
        ["tier", "experimental_system"], kind="stable"
    )
    release_caveat = ""
    if build == "5.0.261":
        release_caveat = f"""

The downloaded all-species Tab3 member contains
**{counters['total_rows']:,}** total physical-plus-genetic rows, whereas the
BioGRID website reports 3,021,750 protein/genetic interactions for the same
build—a difference of **{3_021_750 - counters['total_rows']:,}**. The entire
raw discrepancy appears in the human organism comparison
({counters['physical_raw_either_human']:,} archive rows versus 1,488,630 on
the website); mouse and rat raw and NR totals match exactly. Consequently, the
pair catalog treats the downloaded Tab3 archive as the reproducible source of
truth and records, rather than conceals, the website/archive discrepancy.
"""
    text = f"""# BioGRID direct-binding and co-complex PPI audit

Generated {datetime.now(timezone.utc).isoformat()} from BioGRID build
**{build}**. BioGRID records publication-reported evidence; this report does
not describe every retained pair as independently confirmed.

## Provenance and flags

- Source: `{source}`
- SHA-256: `{source_hash}`
- Reconstituted Complex tier: `{reconstituted}`
- Self-interactions dropped: `{drop_self}`
- Pair key: sorted BioGRID IDs; direction, method, and publication ignored
- `all-three-combined` contains same-species human, mouse, and rat pairs;
  cross-species pairs are reported separately.

## Filtering ledger

```json
{json.dumps(dict(counters), indent=2)}
```

## Exact method mapping observed in this release

{markdown_table(mapping)}

## Summary

{markdown_table(summary)}

## HuRI breakdown: human direct tier

HuRI PMID {HURI_PUBMED_ID} contributed **{huri_count:,}** distinct human
Two-hybrid pairs in this build; approximately 52,000 were expected.

{markdown_table(huri)}

## Published-total sanity check

For each species, this counts all NR physical pairs where either endpoint has
that taxon, before direct/co-complex tier filtering. BioGRID's published
organism totals may use additional internal conventions. Comparisons are only
filled for build 5.0.261.

{markdown_table(published)}
{release_caveat}

## Tier arithmetic

The required identity is `direct + cocomplex - both = union`.

{markdown_table(arithmetic)}

## Outputs and caveats

- `biogrid_ppi_summary.csv`: raw and NR counts by species and tier.
- `biogrid_ppi_by_system.csv`: included and excluded method counts.
- `biogrid_ppi_pairs.parquet` and `.tsv.gz`: reusable pair-level evidence.
- `biogrid_huri_breakdown.csv`: HuRI-only/overlap/non-HuRI counts.
- `biogrid_published_total_check.csv` and `biogrid_tier_arithmetic.csv`:
  validation details.

Absence from BioGRID is missing curation/experimental coverage, not evidence
that an interaction is absent. Cross-species pairs are retained separately and
are not assigned to either within-species catalog. Direct versus co-complex is
an operational assay classification; in particular, Reconstituted Complex is
configurable because its records span purified systems and lysate pulldowns.
"""
    path.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    args.outdir.mkdir(parents=True, exist_ok=True)
    download_url: str | None = None
    if args.download:
        source, download_url = download_latest(args.outdir)
    else:
        source = args.path.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if not args.skip_parquet:
        try:
            import pyarrow  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Parquet output requires pyarrow. Install requirements.txt or "
                "use --skip-parquet only for development validation."
            ) from exc

    database = args.outdir / ".biogrid_work.sqlite3"
    database.unlink(missing_ok=True)
    connection = sqlite3.connect(database)
    try:
        build, counters, physical_systems = ingest(
            source,
            connection,
            chunksize=args.chunksize,
            reconstituted=args.reconstituted,
            drop_self=args.drop_self,
        )
        summary = build_summary(connection, build)
        by_system = build_by_system(connection, build)
        pairs = build_pairs(connection, build)
        huri = huri_breakdown(connection, build)
        published, arithmetic = sanity_checks(connection, build, pairs, counters)
        huri_count = huri_two_hybrid_count(connection)

        print("\nDistinct physical systems observed:")
        print("\n".join(f"  {value}" for value in physical_systems))
        print("\nSummary:")
        print(summary.to_string(index=False))
        print("\nHuRI breakdown:")
        print(huri.to_string(index=False))
        print(f"\nHuRI human Two-hybrid NR pairs: {huri_count:,}")

        summary.to_csv(args.outdir / "biogrid_ppi_summary.csv", index=False)
        by_system.to_csv(args.outdir / "biogrid_ppi_by_system.csv", index=False)
        pairs.to_csv(
            args.outdir / "biogrid_ppi_pairs.tsv.gz",
            sep="\t",
            index=False,
            compression="gzip",
        )
        if not args.skip_parquet:
            pairs.to_parquet(
                args.outdir / "biogrid_ppi_pairs.parquet",
                index=False,
            )
        huri.to_csv(args.outdir / "biogrid_huri_breakdown.csv", index=False)
        published.to_csv(
            args.outdir / "biogrid_published_total_check.csv", index=False
        )
        arithmetic.to_csv(
            args.outdir / "biogrid_tier_arithmetic.csv", index=False
        )
        source_hash = sha256_file(source)
        provenance = {
            "schema_version": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "build": build,
            "source": str(source),
            "source_sha256": source_hash,
            "download_url": download_url,
            "reconstituted": args.reconstituted,
            "drop_self": bool(args.drop_self),
            "counters": dict(counters),
            "huri_human_two_hybrid_nr_pairs": huri_count,
            "parquet_written": not args.skip_parquet,
        }
        (args.outdir / "provenance.json").write_text(
            json.dumps(provenance, indent=2) + "\n", encoding="utf-8"
        )
        write_readme(
            args.outdir / "README.md",
            build=build,
            source=source,
            source_hash=source_hash,
            reconstituted=args.reconstituted,
            drop_self=args.drop_self,
            systems=by_system,
            summary=summary,
            huri=huri,
            published=published,
            arithmetic=arithmetic,
            huri_count=huri_count,
            counters=counters,
        )
    finally:
        connection.close()
        if not args.keep_work_db:
            database.unlink(missing_ok=True)
    print(f"\nWrote BioGRID audit to {args.outdir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
