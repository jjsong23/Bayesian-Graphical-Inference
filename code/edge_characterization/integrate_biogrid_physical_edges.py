#!/usr/bin/env python3
"""Map BioGRID physical interactions onto the mouse signaling graph.

The input is the audited pair table from ``biogrid_ppi_counts.py``. Native
mouse pairs are used directly; human pairs (including HuRI) and rat pairs are
projected through the project's existing orthology mappings. Two auditable
tables are emitted:

* graph-to-graph reported physical pairs for ordinary edge evidence; and
* graph-node-to-source-partner anchors for one-pass shared-partner closure.

An unreported BioGRID pair is never treated as negative evidence.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAIR_CATALOG = (
    PROJECT_ROOT
    / "data/edge_characterization/biogrid/5.0.261/biogrid_ppi_pairs.tsv.gz"
)
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
OUTPUT_DIR = PROJECT_ROOT / "data/edge_characterization/biogrid/5.0.261"
FACTOR_FILE = (
    PROJECT_ROOT
    / "results/backend_bayes_factor_catalogs/edge_factors_891/"
    "biogrid_physical_interaction_bf_gt1.tsv.gz"
)
DEFAULT_REPORTED_PAIR_BF = 5.0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pair-catalog", type=Path, default=PAIR_CATALOG)
    parser.add_argument("--universe", type=Path, default=UNIVERSE)
    parser.add_argument("--human-mapping", type=Path, default=HUMAN_MAPPING)
    parser.add_argument("--rat-mapping", type=Path, default=RAT_MAPPING)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--factor-file", type=Path, default=FACTOR_FILE)
    parser.add_argument(
        "--reported-pair-bf", type=float, default=DEFAULT_REPORTED_PAIR_BF,
        help="One BF applied identically to direct/contact and co-complex reports.",
    )
    parser.add_argument("--chunksize", type=int, default=100_000)
    args = parser.parse_args(argv)
    if args.reported_pair_bf <= 1:
        parser.error("reported-pair Bayes factors must be greater than 1")
    return args


def truth(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"true", "1", "yes"}


def canonical_pair(left: str, right: str) -> tuple[str, str]:
    return tuple(sorted((left, right), key=lambda value: (value.casefold(), value)))


def _mapping_tables(
    universe_path: Path,
    human_mapping_path: Path,
    rat_mapping_path: Path,
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]], set[str]]:
    universe = pd.read_csv(universe_path, sep="\t", dtype=str).fillna("")
    if "node_type" in universe.columns:
        universe = universe.loc[
            universe["node_type"].astype(str).str.casefold().eq("protein")
        ]
    symbols = set(universe["symbol"].astype(str))
    native: dict[str, set[str]] = defaultdict(set)
    for symbol in symbols:
        native[symbol.casefold()].add(symbol)

    human = pd.read_csv(human_mapping_path, sep="\t", dtype=str).fillna("")
    human_map: dict[str, set[str]] = defaultdict(set)
    for row in human.to_dict("records"):
        symbol = str(row.get("symbol", "")).strip()
        entrez = str(row.get("human_entrez_gene_id", "")).strip()
        if symbol in symbols and entrez:
            human_map[entrez].add(symbol)

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
    rat_map: dict[str, set[str]] = defaultdict(set)
    for row in rat.to_dict("records"):
        rat_symbol = str(row.get("rat_gene_symbol", "")).strip().casefold()
        mouse_symbol = str(row.get("mouse_gene_symbol", "")).strip()
        if rat_symbol and mouse_symbol in symbols:
            rat_map[rat_symbol].add(mouse_symbol)
    return native, human_map, rat_map, symbols


def _mapped_nodes(
    species: str,
    symbol: object,
    entrez: object,
    native: dict[str, set[str]],
    human: dict[str, set[str]],
    rat: dict[str, set[str]],
) -> set[str]:
    if species == "mouse":
        return native.get(str(symbol).strip().casefold(), set())
    if species == "human":
        return human.get(str(entrez).strip(), set())
    if species == "rat":
        return rat.get(str(symbol).strip().casefold(), set())
    return set()


def _partner_key(species: str, entrez: object, symbol: object) -> str:
    identifier = str(entrez).strip()
    if not identifier or identifier.casefold() == "nan":
        identifier = str(symbol).strip().casefold()
    return f"{species}:{identifier}"


def build(args: argparse.Namespace) -> dict[str, Any]:
    for path in (
        args.pair_catalog,
        args.universe,
        args.human_mapping,
        args.rat_mapping,
    ):
        if not path.is_file():
            raise FileNotFoundError(path)
    native, human, rat, graph_symbols = _mapping_tables(
        args.universe, args.human_mapping, args.rat_mapping
    )
    pair_records: dict[tuple[str, str], dict[str, Any]] = {}
    anchor_records: dict[tuple[str, str], dict[str, Any]] = {}
    source_rows = 0
    mapped_source_rows = 0
    huri_source_rows = 0

    columns = [
        "species",
        "entrez_a",
        "entrez_b",
        "symbol_a",
        "symbol_b",
        "in_direct",
        "in_cocomplex",
        "n_pubs",
        "n_methods",
        "systems",
        "pubmed_ids",
        "in_huri",
        "is_self",
    ]
    for chunk in pd.read_csv(
        args.pair_catalog,
        sep="\t",
        compression="gzip",
        dtype=str,
        usecols=columns,
        chunksize=args.chunksize,
    ):
        chunk = chunk.loc[chunk["species"].isin({"mouse", "human", "rat"})].fillna("")
        source_rows += len(chunk)
        for row in chunk.itertuples(index=False):
            # A self-interaction cannot provide the distinct third protein
            # required by the closure rule and cannot become a graph edge.
            if truth(row.is_self):
                continue
            species = str(row.species)
            left_nodes = _mapped_nodes(
                species, row.symbol_a, row.entrez_a, native, human, rat
            )
            right_nodes = _mapped_nodes(
                species, row.symbol_b, row.entrez_b, native, human, rat
            )
            if not left_nodes and not right_nodes:
                continue
            mapped_source_rows += 1
            direct = truth(row.in_direct)
            cocomplex = truth(row.in_cocomplex)
            in_huri = truth(row.in_huri)
            huri_source_rows += int(in_huri)
            tier = "direct" if direct and not cocomplex else "cocomplex" if cocomplex and not direct else "direct;cocomplex"
            for graph_node in left_nodes:
                key = (graph_node, _partner_key(species, row.entrez_b, row.symbol_b))
                record = anchor_records.setdefault(
                    key,
                    {
                        "graph_node": graph_node,
                        "partner_key": key[1],
                        "partner_symbol": str(row.symbol_b).strip(),
                        "source_species": species,
                        "in_direct": False,
                        "in_cocomplex": False,
                        "in_huri": False,
                        "systems": set(),
                    },
                )
                record["in_direct"] |= direct
                record["in_cocomplex"] |= cocomplex
                record["in_huri"] |= in_huri
                record["systems"].update(filter(None, str(row.systems).split(";")))
            for graph_node in right_nodes:
                key = (graph_node, _partner_key(species, row.entrez_a, row.symbol_a))
                record = anchor_records.setdefault(
                    key,
                    {
                        "graph_node": graph_node,
                        "partner_key": key[1],
                        "partner_symbol": str(row.symbol_a).strip(),
                        "source_species": species,
                        "in_direct": False,
                        "in_cocomplex": False,
                        "in_huri": False,
                        "systems": set(),
                    },
                )
                record["in_direct"] |= direct
                record["in_cocomplex"] |= cocomplex
                record["in_huri"] |= in_huri
                record["systems"].update(filter(None, str(row.systems).split(";")))

            for node_a in left_nodes:
                for node_b in right_nodes:
                    if node_a == node_b:
                        continue
                    pair = canonical_pair(node_a, node_b)
                    record = pair_records.setdefault(
                        pair,
                        {
                            "node_a": pair[0],
                            "node_b": pair[1],
                            "in_direct": False,
                            "in_cocomplex": False,
                            "in_huri": False,
                            "source_species": set(),
                            "systems": set(),
                            "pubmed_ids": set(),
                            "maximum_source_n_pubs": 0,
                            "maximum_source_n_methods": 0,
                        },
                    )
                    record["in_direct"] |= direct
                    record["in_cocomplex"] |= cocomplex
                    record["in_huri"] |= in_huri
                    record["source_species"].add(species)
                    record["systems"].update(filter(None, str(row.systems).split(";")))
                    record["pubmed_ids"].update(filter(None, str(row.pubmed_ids).split(";")))
                    record["maximum_source_n_pubs"] = max(
                        record["maximum_source_n_pubs"], int(float(row.n_pubs or 0))
                    )
                    record["maximum_source_n_methods"] = max(
                        record["maximum_source_n_methods"], int(float(row.n_methods or 0))
                    )

    pair_rows: list[dict[str, Any]] = []
    for record in pair_records.values():
        factor = args.reported_pair_bf
        pair_rows.append(
            {
                **record,
                "source_species": ";".join(sorted(record["source_species"])),
                "systems": ";".join(sorted(record["systems"])),
                "pubmed_ids": ";".join(sorted(record["pubmed_ids"])),
                "bayes_factor": factor,
                "factor_rule": "one reported-pair BF for either assay tier",
            }
        )
    anchors = pd.DataFrame.from_records(list(anchor_records.values()))
    if not anchors.empty:
        anchors["systems"] = anchors["systems"].map(
            lambda values: ";".join(sorted(values))
        )
        anchors = anchors.sort_values(["graph_node", "partner_key"], kind="stable")
    pairs = pd.DataFrame.from_records(pair_rows).sort_values(
        ["node_a", "node_b"], kind="stable"
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.factor_file.parent.mkdir(parents=True, exist_ok=True)
    pair_path = args.output_dir / "mapped_graph_physical_pairs.tsv.gz"
    anchor_path = args.output_dir / "graph_incident_physical_anchors.tsv.gz"
    pairs.to_csv(pair_path, sep="\t", index=False, compression="gzip")
    anchors.to_csv(anchor_path, sep="\t", index=False, compression="gzip")
    pairs[["node_a", "node_b", "bayes_factor"]].to_csv(
        args.factor_file, sep="\t", index=False, compression="gzip"
    )
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_pair_catalog": str(args.pair_catalog.resolve()),
        "graph_protein_count": len(graph_symbols),
        "source_rows_considered": source_rows,
        "source_rows_incident_to_mapped_graph_node": mapped_source_rows,
        "incident_anchor_count": int(len(anchors)),
        "reported_graph_pair_count": int(len(pairs)),
        "reported_direct_graph_pair_count": int(pairs["in_direct"].sum()),
        "reported_cocomplex_graph_pair_count": int(pairs["in_cocomplex"].sum()),
        "reported_huri_graph_pair_count": int(pairs["in_huri"].sum()),
        "source_huri_rows_incident_to_graph": huri_source_rows,
        "reported_pair_bayes_factor": float(args.reported_pair_bf),
        "pair_factor_rule": "one BF for either assay tier; overlapping tiers are counted once",
        "absence_rule": "neutral; BioGRID nonreporting is not negative evidence",
        "pair_file": str(pair_path.resolve()),
        "anchor_file": str(anchor_path.resolve()),
        "factor_file": str(args.factor_file.resolve()),
        "projection": {
            "mouse": "native official-symbol match",
            "human": "human Entrez to mouse graph symbol via HPA mapping; includes HuRI",
            "rat": "rat symbol to mouse symbol via Ensembl release 116 orthology",
            "cross_species": "excluded",
        },
    }
    (args.output_dir / "mapped_graph_physical_summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    summary = build(parse_args(argv))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
