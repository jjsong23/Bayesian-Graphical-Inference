#!/usr/bin/env python3
"""Build one cross-species COMPARTMENTS localization stream for Version 1.

The COMPARTMENTS *integrated* channel contains the knowledge, experiments,
text-mining, and prediction channels.  For human proteins this subsumes the
Human Protein Atlas localization evidence used by the older standalone HPA
stream.  Mouse, human, and rat annotations are projected onto the mouse graph
node universe with the same orthology resources used by the BioGRID importer.

Species are consolidated before edge scoring: the maximum confidence for a
mouse-node/GO-term combination is retained and species agreement is recorded.
Consequently, the same localization transferred from several orthologs is one
piece of edge evidence, not several independent Bayes factors.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
UNIVERSE = PROJECT_ROOT / "data/node_selection/node_universe_combined_nonzero.tsv"
HUMAN_MAPPING = (
    PROJECT_ROOT
    / "data/edge_characterization/localization/hpa/v25.1/processed/"
    "mouse_human_hpa_mapping.tsv"
)
RAT_MAPPING = (
    PROJECT_ROOT
    / "data/node_selection/collecting_duct/raw/ensembl116_rat_mouse_orthologs.tsv"
)
OUTPUT_DIR = (
    PROJECT_ROOT / "data/edge_characterization/compartments/integrated/processed"
)
FACTOR_FILE = (
    PROJECT_ROOT
    / "results/backend_bayes_factor_catalogs/edge_factors_891/"
    "compartments_localization_bf_gt1.tsv.gz"
)
URLS = {
    species: (
        f"https://download.jensenlab.org/"
        f"{species}_compartment_integrated_full.tsv"
    )
    for species in ("mouse", "human", "rat")
}
Q = 0.75
MINIMUM_SCORE = 4.0
NEUTRAL_LIKELIHOOD = 0.5
EPSILON = 1e-12


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument("--universe", type=Path)
    parser.add_argument("--human-mapping", type=Path)
    parser.add_argument("--rat-mapping", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--factor-file", type=Path)
    parser.add_argument("--minimum-score", type=float, default=MINIMUM_SCORE)
    for species in URLS:
        parser.add_argument(
            f"--{species}-source",
            help=(
                "Local integrated TSV or URL. Defaults to the official current "
                f"COMPARTMENTS {species} integrated release."
            ),
        )
    return parser.parse_args(argv)


def canonical_pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right), key=lambda value: (value.casefold(), value)))


def _resolved_paths(args: argparse.Namespace) -> dict[str, Path]:
    project = args.project_root.resolve()
    return {
        "project": project,
        "universe": (args.universe or project / UNIVERSE.relative_to(PROJECT_ROOT)).resolve(),
        "human_mapping": (
            args.human_mapping or project / HUMAN_MAPPING.relative_to(PROJECT_ROOT)
        ).resolve(),
        "rat_mapping": (
            args.rat_mapping or project / RAT_MAPPING.relative_to(PROJECT_ROOT)
        ).resolve(),
        "output_dir": (
            args.output_dir or project / OUTPUT_DIR.relative_to(PROJECT_ROOT)
        ).resolve(),
        "factor_file": (
            args.factor_file or project / FACTOR_FILE.relative_to(PROJECT_ROOT)
        ).resolve(),
    }


def _mapping_tables(
    universe_path: Path, human_mapping_path: Path, rat_mapping_path: Path
) -> tuple[pd.DataFrame, dict[str, set[str]]]:
    universe = pd.read_csv(universe_path, sep="\t", dtype=str).fillna("")
    if universe["symbol"].duplicated().any():
        raise ValueError("Node universe contains duplicate symbols")
    proteins = universe.loc[
        universe["node_type"].astype(str).str.casefold().eq("protein")
    ]
    graph_symbols = set(proteins["symbol"].astype(str))
    mappings: dict[str, dict[str, set[str]]] = {
        species: defaultdict(set) for species in URLS
    }
    for symbol in graph_symbols:
        mappings["mouse"][symbol.casefold()].add(symbol)

    human = pd.read_csv(human_mapping_path, sep="\t", dtype=str).fillna("")
    for row in human.to_dict("records"):
        mouse_symbol = str(row.get("symbol", "")).strip()
        human_symbol = str(row.get("human_symbol", "")).strip()
        if mouse_symbol in graph_symbols and human_symbol:
            mappings["human"][human_symbol.casefold()].add(mouse_symbol)

    rat = pd.read_csv(rat_mapping_path, sep="\t", dtype=str).fillna("")
    if len(rat.columns) == 8:
        rat.columns = [
            "rat_ensembl_gene_id",
            "rat_gene_symbol",
            "mouse_ensembl_gene_id",
            "mouse_gene_symbol",
            "orthology_type",
            "mouse_percent_identity",
            "rat_percent_identity",
            "orthology_confidence",
        ]
    for row in rat.to_dict("records"):
        mouse_symbol = str(row.get("mouse_gene_symbol", "")).strip()
        rat_symbol = str(row.get("rat_gene_symbol", "")).strip()
        if mouse_symbol in graph_symbols and rat_symbol:
            mappings["rat"][rat_symbol.casefold()].add(mouse_symbol)
    return universe, mappings


def _open_source(source: str) -> tuple[BinaryIO, dict[str, str]]:
    if source.startswith(("http://", "https://")):
        response = urlopen(
            Request(source, headers={"User-Agent": "GBI-V1-COMPARTMENTS/1.0"}),
            timeout=120,
        )
        metadata = {
            "source": source,
            "last_modified": str(response.headers.get("Last-Modified", "")),
            "etag": str(response.headers.get("ETag", "")),
            "content_length": str(response.headers.get("Content-Length", "")),
        }
        return response, metadata
    path = Path(source).expanduser().resolve()
    return path.open("rb"), {
        "source": str(path),
        "last_modified": datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
        "etag": "",
        "content_length": str(path.stat().st_size),
    }


def _stream_mapped_annotations(
    species: str,
    source: str,
    mapping: dict[str, set[str]],
    minimum_score: float,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    records: dict[tuple[str, str], dict[str, object]] = {}
    raw_rows = 0
    score_rows = 0
    mapped_rows = 0
    digest = hashlib.sha256()
    handle, source_metadata = _open_source(source)
    try:
        for raw_line in handle:
            digest.update(raw_line)
            raw_rows += 1
            line = raw_line.decode("utf-8-sig").rstrip("\r\n")
            if not line:
                continue
            fields = line.split("\t")
            if len(fields) != 5:
                raise ValueError(
                    f"Unexpected {species} integrated row {raw_rows}: {fields!r}"
                )
            source_id, source_symbol, go_id, location_name, raw_score = fields
            try:
                score = float(raw_score)
            except ValueError as error:
                raise ValueError(
                    f"Invalid {species} score on row {raw_rows}: {raw_score!r}"
                ) from error
            if score < minimum_score:
                continue
            score_rows += 1
            graph_nodes = mapping.get(source_symbol.strip().casefold(), set())
            for graph_node in graph_nodes:
                mapped_rows += 1
                key = (graph_node, go_id)
                current = records.get(key)
                if current is None or score > float(current["score"]):
                    records[key] = {
                        "symbol": graph_node,
                        "go_id": go_id,
                        "location_name": location_name,
                        "score": score,
                        "source_species": species,
                        "source_symbols": source_symbol,
                        "source_ids": source_id,
                    }
                elif math.isclose(score, float(current["score"])):
                    current["source_symbols"] = ";".join(
                        sorted(
                            set(str(current["source_symbols"]).split(";"))
                            | {source_symbol}
                        )
                    )
                    current["source_ids"] = ";".join(
                        sorted(
                            set(str(current["source_ids"]).split(";"))
                            | {source_id}
                        )
                    )
    finally:
        handle.close()
    source_metadata.update(
        {
            "sha256": digest.hexdigest(),
            "raw_rows": raw_rows,
            "rows_at_or_above_minimum_score": score_rows,
            "mapped_node_location_rows_before_cross_species_collapse": mapped_rows,
            "mapped_node_location_rows_after_within_species_collapse": len(records),
        }
    )
    return list(records.values()), source_metadata


def _consolidate_annotations(
    rows: Iterable[dict[str, object]],
) -> pd.DataFrame:
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for row in rows:
        key = (str(row["symbol"]), str(row["go_id"]))
        record = grouped.setdefault(
            key,
            {
                "symbol": key[0],
                "go_id": key[1],
                "location_names": set(),
                "maximum_score": 0.0,
                "source_species": set(),
                "source_symbols": set(),
                "source_ids": set(),
            },
        )
        record["location_names"].add(str(row["location_name"]))
        record["maximum_score"] = max(
            float(record["maximum_score"]), float(row["score"])
        )
        record["source_species"].add(str(row["source_species"]))
        record["source_symbols"].update(
            filter(None, str(row["source_symbols"]).split(";"))
        )
        record["source_ids"].update(filter(None, str(row["source_ids"]).split(";")))
    output = []
    for record in grouped.values():
        output.append(
            {
                "symbol": record["symbol"],
                "go_id": record["go_id"],
                "location_names": ";".join(sorted(record["location_names"])),
                "maximum_score": record["maximum_score"],
                "source_species": ";".join(sorted(record["source_species"])),
                "source_species_count": len(record["source_species"]),
                "source_symbols": ";".join(sorted(record["source_symbols"])),
                "source_ids": ";".join(sorted(record["source_ids"])),
            }
        )
    return pd.DataFrame(output).sort_values(
        ["symbol", "go_id"], kind="stable"
    ).reset_index(drop=True)


def _profiles_and_scores(
    universe: pd.DataFrame, annotations: pd.DataFrame
) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    symbols = universe["symbol"].astype(str).tolist()
    go_ids = sorted(set(annotations["go_id"].astype(str)))
    symbol_index = {symbol: index for index, symbol in enumerate(symbols)}
    go_index = {go_id: index for index, go_id in enumerate(go_ids)}
    weighted = np.zeros((len(symbols), len(go_ids)), dtype=float)
    species_by_symbol: dict[str, set[str]] = defaultdict(set)
    locations_by_symbol: dict[str, list[str]] = defaultdict(list)
    scores_by_symbol: dict[str, list[str]] = defaultdict(list)
    for row in annotations.itertuples(index=False):
        weighted[symbol_index[str(row.symbol)], go_index[str(row.go_id)]] = (
            float(row.maximum_score) / 5.0
        )
        species_by_symbol[str(row.symbol)].update(str(row.source_species).split(";"))
        locations_by_symbol[str(row.symbol)].append(str(row.go_id))
        scores_by_symbol[str(row.symbol)].append(f"{float(row.maximum_score):.6g}")

    observed = weighted.sum(axis=1) > 0
    observed_indices = np.flatnonzero(observed)
    unit = weighted[observed] / np.linalg.norm(
        weighted[observed], axis=1, keepdims=True
    )
    observed_similarity = np.clip(unit @ unit.T, 0.0, 1.0)
    background = observed_similarity.copy()
    np.fill_diagonal(background, np.nan)
    raw_tq = np.nanquantile(background, Q, axis=1)
    tq = raw_tq.copy()
    methods: list[str] = []
    positive_sizes: list[int] = []
    for index, raw in enumerate(raw_tq):
        positive = background[index][
            np.isfinite(background[index]) & (background[index] > 0)
        ]
        positive_sizes.append(int(len(positive)))
        if np.isfinite(raw) and raw > 0:
            methods.append("all_observed_nodes_q75")
        elif len(positive):
            tq[index] = float(np.quantile(positive, Q))
            methods.append("positive_overlap_q75_fallback")
        else:
            tq[index] = np.nan
            methods.append("no_positive_overlap_neutral")

    directed = np.full_like(observed_similarity, NEUTRAL_LIKELIHOOD)
    valid = np.isfinite(tq) & (tq > 0)
    if np.any(valid):
        z = observed_similarity[valid] / tq[valid, np.newaxis]
        directed[valid] = np.maximum(
            NEUTRAL_LIKELIHOOD, 1.0 - np.exp(-0.5 * np.square(z))
        )
    np.fill_diagonal(directed, NEUTRAL_LIKELIHOOD)
    likelihood_observed = (directed + directed.T) / 2.0

    similarity = np.zeros((len(symbols), len(symbols)), dtype=float)
    likelihood = np.full(
        (len(symbols), len(symbols)), NEUTRAL_LIKELIHOOD, dtype=float
    )
    similarity[np.ix_(observed_indices, observed_indices)] = observed_similarity
    likelihood[np.ix_(observed_indices, observed_indices)] = likelihood_observed
    np.fill_diagonal(similarity, 0.0)
    np.fill_diagonal(likelihood, NEUTRAL_LIKELIHOOD)

    tq_full = np.full(len(symbols), np.nan)
    raw_tq_full = np.full(len(symbols), np.nan)
    method_full = np.full(len(symbols), "profile_not_observed", dtype=object)
    positive_full = np.zeros(len(symbols), dtype=int)
    tq_full[observed_indices] = tq
    raw_tq_full[observed_indices] = raw_tq
    method_full[observed_indices] = methods
    positive_full[observed_indices] = positive_sizes
    profiles = universe[["symbol", "display_symbol", "node_type"]].copy()
    profiles["profile_observed"] = observed
    profiles["profile_go_ids"] = profiles["symbol"].map(
        lambda value: ";".join(locations_by_symbol.get(str(value), []))
    )
    profiles["profile_scores"] = profiles["symbol"].map(
        lambda value: ";".join(scores_by_symbol.get(str(value), []))
    )
    profiles["source_species"] = profiles["symbol"].map(
        lambda value: ";".join(sorted(species_by_symbol.get(str(value), set())))
    )
    profiles["source_species_count"] = profiles["symbol"].map(
        lambda value: len(species_by_symbol.get(str(value), set()))
    )
    profiles["raw_Tq_q75"] = raw_tq_full
    profiles["Tq_q75"] = tq_full
    profiles["Tq_method"] = method_full
    profiles["positive_overlap_background_size"] = positive_full
    return profiles, similarity, likelihood, observed


def _factor_table(
    profiles: pd.DataFrame,
    similarity: np.ndarray,
    likelihood: np.ndarray,
    observed: np.ndarray,
) -> pd.DataFrame:
    symbols = profiles["symbol"].astype(str).tolist()
    upper_i, upper_j = np.triu_indices(len(symbols), 1)
    factor = likelihood[upper_i, upper_j] / NEUTRAL_LIKELIHOOD
    retain = factor > 1.0 + EPSILON
    rows = pd.DataFrame(
        {
            "edge_id": [
                "|".join(canonical_pair(symbols[i], symbols[j]))
                for i, j in zip(upper_i[retain], upper_j[retain])
            ],
            "node_a": [symbols[i] for i in upper_i[retain]],
            "node_b": [symbols[j] for j in upper_j[retain]],
            "profiles_observed": (
                observed[upper_i[retain]] & observed[upper_j[retain]]
            ),
            "cosine_similarity": similarity[upper_i[retain], upper_j[retain]],
            "Tq_node_a": pd.to_numeric(
                profiles.iloc[upper_i[retain]]["Tq_q75"], errors="coerce"
            ).to_numpy(float),
            "Tq_node_b": pd.to_numeric(
                profiles.iloc[upper_j[retain]]["Tq_q75"], errors="coerce"
            ).to_numpy(float),
            "species_node_a": profiles.iloc[upper_i[retain]][
                "source_species"
            ].to_numpy(str),
            "species_node_b": profiles.iloc[upper_j[retain]][
                "source_species"
            ].to_numpy(str),
            "likelihood": likelihood[upper_i[retain], upper_j[retain]],
            "bayes_factor": factor[retain],
        }
    )
    rows["log_bayes_factor"] = np.log(rows["bayes_factor"].to_numpy(float))
    rows["posterior_after_stream"] = rows["bayes_factor"] / (
        1.0 + rows["bayes_factor"]
    )
    return rows


def _write_gzip(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        frame.to_csv(handle, sep="\t", index=False, float_format="%.9g")


def build(args: argparse.Namespace) -> dict[str, object]:
    paths = _resolved_paths(args)
    for required in (paths["universe"], paths["human_mapping"], paths["rat_mapping"]):
        if not required.is_file():
            raise FileNotFoundError(required)
    if not 0 < args.minimum_score <= 5:
        raise ValueError("minimum score must be in (0, 5]")
    universe, mappings = _mapping_tables(
        paths["universe"], paths["human_mapping"], paths["rat_mapping"]
    )
    all_rows: list[dict[str, object]] = []
    sources: dict[str, object] = {}
    for species in ("mouse", "human", "rat"):
        source = getattr(args, f"{species}_source") or URLS[species]
        rows, metadata = _stream_mapped_annotations(
            species, source, mappings[species], args.minimum_score
        )
        all_rows.extend(rows)
        sources[species] = metadata
    annotations = _consolidate_annotations(all_rows)
    profiles, similarity, likelihood, observed = _profiles_and_scores(
        universe, annotations
    )
    factors = _factor_table(profiles, similarity, likelihood, observed)
    output_dir = paths["output_dir"]
    output_dir.mkdir(parents=True, exist_ok=True)
    annotations_path = output_dir / "mapped_integrated_annotations.tsv.gz"
    profiles_path = output_dir / "node_compartments_profiles.tsv.gz"
    _write_gzip(annotations_path, annotations)
    _write_gzip(profiles_path, profiles)
    _write_gzip(paths["factor_file"], factors)
    species_coverage = {
        species: int(
            profiles["source_species"]
            .fillna("")
            .astype(str)
            .map(lambda value: species in value.split(";"))
            .sum()
        )
        for species in ("mouse", "human", "rat")
    }
    summary: dict[str, object] = {
        "schema_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "minimum_integrated_score": args.minimum_score,
        "node_universe_count": int(len(universe)),
        "protein_node_count": int(
            universe["node_type"].astype(str).str.casefold().eq("protein").sum()
        ),
        "profiled_node_count": int(observed.sum()),
        "profiled_nodes_by_source_species": species_coverage,
        "consolidated_node_location_count": int(len(annotations)),
        "go_term_count": int(annotations["go_id"].nunique()),
        "supported_pair_count": int(len(factors)),
        "cross_species_rule": (
            "Map mouse directly and human/rat through existing orthology tables; "
            "retain the maximum integrated confidence per mouse-node/GO term; "
            "record but do not multiply corroborating species."
        ),
        "independence_rule": (
            "This stream replaces standalone HPA localization in the default "
            "model because the human COMPARTMENTS integrated channel includes HPA."
        ),
        "pair_score": (
            "confidence-weighted GO-term cosine similarity with per-node q75 "
            "thresholds and reciprocal complement-of-minimum likelihood"
        ),
        "sources": sources,
        "outputs": {
            "mapped_annotations": str(annotations_path),
            "node_profiles": str(profiles_path),
            "factor_file": str(paths["factor_file"]),
        },
    }
    provenance_path = output_dir / "provenance.json"
    provenance_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return summary


def main() -> int:
    build(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
