"""Database-native provenance for edge evidence shown by the GUI.

The Bayesian ledger explains *how much* a stream changed the odds.  This module
answers the separate question "what inside the source database produced that
number?" for STRING, OmniPath, and BioGRID.  It never changes a Bayes factor.

Tracebacks are intentionally assembled from the exact frozen source files used
by the workflow.  Internet literature interpretation is a later, optional
layer; it must not silently replace these primary database records.
"""

from __future__ import annotations

import gzip
import math
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


STRING_VERSION = "12.0"
STRING_REFERENCE = 0.041
STRING_CHANNELS = (
    ("experimental_score", "Experiments", "Experimental interaction evidence assembled by STRING."),
    ("database_score", "Curated databases", "Pathway and interaction knowledge imported by STRING."),
    ("textmining_score", "Text mining", "Statistical co-mention evidence from scientific text."),
    ("coexpression_score", "Coexpression", "Correlated expression evidence."),
    ("neighborhood_score", "Genomic neighborhood", "Conserved proximity of genes across genomes."),
    ("fusion_score", "Gene fusion", "Evidence that orthologs occur as a fused gene."),
    ("cooccurrence_score", "Phylogenetic co-occurrence", "Correlated presence or absence across genomes."),
)

STRING_SUPPORTED = Path(
    "results/edge_characterization/localization_kinase_predictor_string/"
    "string_supported_undirected_edges.tsv"
)
STRING_MAPPING = Path(
    "data/edge_characterization/string/v12.0/processed/node_to_string_mapping.tsv"
)
STRING_RAW = Path(
    "data/edge_characterization/string/v12.0/raw/"
    "10090.protein.links.detailed.v12.0.txt.gz"
)
OMNIPATH_SUPPORTED = Path(
    "results/edge_characterization/"
    "localization_kinase_predictor_string_hpa_omnipath/"
    "omnipath_supported_undirected_edges.tsv"
)
OMNIPATH_RAW = Path(
    "data/edge_characterization/omnipath/2026-07-30/raw/"
    "omnipath_mouse_core_post_translational.tsv"
)
BIOGRID_MAPPED = Path(
    "data/edge_characterization/biogrid/5.0.261/"
    "mapped_graph_physical_pairs.tsv.gz"
)
BIOGRID_CATALOG = Path(
    "data/edge_characterization/biogrid/5.0.261/biogrid_ppi_pairs.tsv.gz"
)
HUMAN_MAPPING = Path(
    "data/edge_characterization/localization/hpa/v25.1/processed/"
    "mouse_human_hpa_mapping.tsv"
)
RAT_MAPPING = Path(
    "data/node_selection/collecting_duct/raw/ensembl116_rat_mouse_orthologs.tsv"
)
UNIVERSE = Path("data/node_selection/node_universe_combined_nonzero.tsv")
PROTEIN_INDEX = Path(
    "data/edge_characterization/kinase_predictor/phosphosite_database/protein_index.tsv"
)
INCREMENTAL_CACHE = Path(
    "data/edge_characterization/incremental_edge_cache/edge_pair_cache.sqlite3"
)


def canonical_pair(left: object, right: object) -> tuple[str, str]:
    return tuple(sorted((str(left), str(right)), key=lambda value: (value.casefold(), value)))


def _truth(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"true", "1", "yes"}


def _split(value: object) -> list[str]:
    return sorted({item.strip() for item in str(value or "").split(";") if item.strip()})


def _pubmed_links(values: Iterable[str]) -> list[dict[str, str]]:
    identifiers: set[str] = set()
    for value in values:
        tail = str(value).rsplit(":", 1)[-1]
        if tail.isdigit():
            identifiers.add(tail)
    return [
        {
            "label": f"PMID {pmid}",
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            "kind": "primary_reference",
        }
        for pmid in sorted(identifiers, key=int)
    ]


@lru_cache(maxsize=4)
def _table_lookup(path_text: str, columns: tuple[str, ...]) -> dict[tuple[str, str], dict[str, Any]]:
    path = Path(path_text)
    if not path.is_file():
        return {}
    frame = pd.read_csv(path, sep="\t", usecols=list(columns)).fillna("")
    return {
        canonical_pair(row["node_a"], row["node_b"]): row
        for row in frame.to_dict(orient="records")
    }


def _latest_cached_node(project: Path, symbol: str) -> dict[str, str]:
    path = project / INCREMENTAL_CACHE
    if not path.is_file():
        return {}
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            """
            SELECT selected_uniprot, string_id
            FROM node_profiles WHERE symbol=?
            ORDER BY profiled_at DESC LIMIT 1
            """,
            (symbol,),
        ).fetchone()
    except sqlite3.Error:
        return {}
    finally:
        connection.close()
    return {"selected_uniprot": str(row[0]), "string_id": str(row[1])} if row else {}


@lru_cache(maxsize=4)
def _seed_identities(project_text: str) -> dict[str, dict[str, str]]:
    project = Path(project_text)
    identities: dict[str, dict[str, str]] = {}
    mapping_path = project / STRING_MAPPING
    if mapping_path.is_file():
        frame = pd.read_csv(mapping_path, sep="\t", dtype=str).fillna("")
        for row in frame.to_dict(orient="records"):
            identities.setdefault(str(row.get("symbol", "")), {}).update(
                {"string_id": str(row.get("string_id", ""))}
            )
    index_path = project / PROTEIN_INDEX
    if index_path.is_file():
        frame = pd.read_csv(index_path, sep="\t", dtype=str).fillna("")
        for row in frame.to_dict(orient="records"):
            identities.setdefault(str(row.get("symbol", "")), {}).update(
                {"selected_uniprot": str(row.get("selected_uniprot", row.get("mapped_uniprot", "")))}
            )
    return identities


def _identity(project: Path, symbol: str) -> dict[str, str]:
    identity = dict(_seed_identities(str(project.resolve())).get(symbol, {}))
    identity.update({key: value for key, value in _latest_cached_node(project, symbol).items() if value})
    identity["symbol"] = symbol
    return identity


@lru_cache(maxsize=256)
def _scan_string_record(path_text: str, id_a: str, id_b: str) -> dict[str, Any] | None:
    """Find one exact STRING detailed-links row and cache it for this process."""
    if not id_a or not id_b:
        return None
    wanted = {id_a, id_b}
    path = Path(path_text)
    if not path.is_file():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        header = handle.readline().strip().split()
        for line in handle:
            fields = line.split()
            if len(fields) != len(header) or {fields[0], fields[1]} != wanted:
                continue
            record: dict[str, Any] = {"string_id_a": fields[0], "string_id_b": fields[1]}
            for name, value in zip(header[2:], fields[2:], strict=True):
                normalized = "cooccurrence_score" if name == "cooccurence" else f"{name}_score"
                if name == "combined_score":
                    normalized = "string_combined_score"
                record[normalized] = int(value) / 1000.0
            return record
    return None


def _cached_incremental_score(project: Path, pair: tuple[str, str]) -> float | None:
    path = project / INCREMENTAL_CACHE
    if not path.is_file():
        return None
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            """
            SELECT string_score FROM pair_evidence
            WHERE node_a=? AND node_b=? AND string_score IS NOT NULL
            ORDER BY characterized_at DESC LIMIT 1
            """,
            pair,
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    return float(row[0]) if row else None


def string_trace(
    project: Path,
    pair: tuple[str, str],
    *,
    applied_bayes_factor: float | None,
    weight: float,
    reference_multiplier: float,
) -> dict[str, Any]:
    columns = (
        "node_a", "node_b", "string_id_a", "string_id_b",
        *(name for name, _, _ in STRING_CHANNELS),
        "string_combined_score", "string_bayes_factor",
    )
    record = _table_lookup(str((project / STRING_SUPPORTED).resolve()), columns).get(pair)
    identities = [_identity(project, symbol) for symbol in pair]
    if record is None:
        record = _scan_string_record(
            str((project / STRING_RAW).resolve()),
            identities[0].get("string_id", ""),
            identities[1].get("string_id", ""),
        )
    if record is None:
        score = _cached_incremental_score(project, pair)
        if score is not None:
            record = {"string_combined_score": score}

    reference = STRING_REFERENCE * float(reference_multiplier)
    factors: list[dict[str, Any]] = []
    if record:
        for key, label, description in STRING_CHANNELS:
            if key in record:
                factors.append(
                    {
                        "factor": label,
                        "value": float(record[key]),
                        "description": description,
                        "role": "STRING component channel; not independently multiplied by this application",
                    }
                )
        factors.append(
            {
                "factor": "STRING combined score",
                "value": float(record.get("string_combined_score", 0.0)),
                "description": "STRING's own prior-corrected combination of its evidence channels.",
                "role": "This is the sole STRING score converted to a Bayes factor by this application",
            }
        )
    return {
        "database": "STRING",
        "database_version": STRING_VERSION,
        "trace_status": "matched" if record else "no_retained_record",
        "summary": (
            "The displayed STRING BF traces to STRING's combined score; component channels are shown for interpretation but are not multiplied again."
            if record else
            "No STRING detailed-links record was recovered for the mapped endpoints."
        ),
        "endpoint_mapping": identities,
        "score_derivation": {
            "database_score": float(record.get("string_combined_score", 0.0)) if record else None,
            "reference_probability": reference,
            "raw_bayes_factor": applied_bayes_factor,
            "model_weight": float(weight),
            "weighted_effective_bayes_factor": (
                float(applied_bayes_factor) ** float(weight)
                if applied_bayes_factor is not None and applied_bayes_factor > 0
                else None
            ),
            "formula": "BF = odds(STRING combined score) / odds(0.041 × reference multiplier); posterior contribution uses weight × ln(BF)",
            "important_limit": "STRING's combined score is not a simple sum of the displayed channels and can include correlated evidence. The application does not re-combine the channels.",
        },
        "factors": factors,
        "records": [record] if record else [],
        "links": [
            {"label": "STRING score documentation", "url": "https://string-db.org/help/scores/", "kind": "database_documentation"},
            {"label": "STRING v12 paper", "url": "https://pubmed.ncbi.nlm.nih.gov/39558183/", "kind": "database_reference"},
        ],
    }


@lru_cache(maxsize=4)
def _omnipath_rows(path_text: str) -> pd.DataFrame:
    path = Path(path_text)
    return pd.read_csv(path, sep="\t", dtype=str).fillna("") if path.is_file() else pd.DataFrame()


def omnipath_trace(
    project: Path,
    pair: tuple[str, str],
    *,
    applied_bayes_factor: float | None,
    weight: float,
    tq_multiplier: float,
) -> dict[str, Any]:
    identities = [_identity(project, symbol) for symbol in pair]
    raw = _omnipath_rows(str((project / OMNIPATH_RAW).resolve()))
    records: list[dict[str, Any]] = []
    if not raw.empty:
        left_symbols = {pair[0].casefold(), pair[1].casefold()}
        left_accessions = {
            value.get("selected_uniprot", "") for value in identities if value.get("selected_uniprot")
        }
        for row in raw.to_dict(orient="records"):
            endpoints = {
                str(row.get("source_genesymbol", "")).casefold(),
                str(row.get("target_genesymbol", "")).casefold(),
            }
            accessions = {str(row.get("source", "")), str(row.get("target", ""))}
            symbol_match = endpoints == left_symbols
            accession_match = len(left_accessions) == 2 and accessions == left_accessions
            if not (symbol_match or accession_match):
                continue
            references = _split(row.get("references", ""))
            records.append(
                {
                    "source": row.get("source"),
                    "target": row.get("target"),
                    "source_symbol": row.get("source_genesymbol"),
                    "target_symbol": row.get("target_genesymbol"),
                    "is_stimulation": _truth(row.get("is_stimulation")),
                    "is_inhibition": _truth(row.get("is_inhibition")),
                    "consensus_direction": _truth(row.get("consensus_direction")),
                    "consensus_stimulation": _truth(row.get("consensus_stimulation")),
                    "consensus_inhibition": _truth(row.get("consensus_inhibition")),
                    "curation_effort": float(row.get("curation_effort") or 0),
                    "resources": _split(row.get("sources", "")),
                    "references": references,
                }
            )
    effort = max((float(record["curation_effort"]) for record in records), default=None)
    references = sorted({ref for record in records for ref in record["references"]})
    resources = sorted({source for record in records for source in record["resources"]})
    return {
        "database": "OmniPath",
        "database_version": "mouse core post-translational snapshot 2026-07-30",
        "trace_status": "matched" if records else "no_retained_record",
        "summary": (
            f"{len(records)} directed OmniPath record(s) were collapsed to this undirected edge; the maximum curation effort ({effort:g}) supplied the score."
            if records else
            "No mapped OmniPath core record was recovered for this pair."
        ),
        "endpoint_mapping": identities,
        "score_derivation": {
            "database_score": effort,
            "score_name": "maximum curation effort across mapped directed records",
            "tq_reference": 6.0 * float(tq_multiplier),
            "raw_bayes_factor": applied_bayes_factor,
            "model_weight": float(weight),
            "weighted_effective_bayes_factor": (
                float(applied_bayes_factor) ** float(weight)
                if applied_bayes_factor is not None and applied_bayes_factor > 0
                else None
            ),
            "formula": "support = 1 − exp(−0.5 × (curation_effort / Tq)^2); likelihood = 0.5 + 0.5 × support; BF = likelihood / 0.5",
            "important_limit": "The edge model collapses direction and activation/inhibition sign for probability integration; the native annotations below remain available for path-direction auditing.",
        },
        "factors": [
            {"factor": "Directed records", "value": len(records), "description": "Number of matching directed records."},
            {"factor": "Maximum curation effort", "value": effort, "description": "Conservative score used by the Bayesian stream."},
            {"factor": "Unique resources", "value": len(resources), "description": "; ".join(resources)},
            {"factor": "Unique references", "value": len(references), "description": "; ".join(references)},
        ],
        "records": records,
        "links": [
            {"label": "OmniPath interaction documentation", "url": "https://omnipathdb.org/queries/interactions", "kind": "database_documentation"},
            *_pubmed_links(references),
        ],
    }


@lru_cache(maxsize=4)
def _biogrid_mapping_tables(project_text: str) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]]:
    project = Path(project_text)
    universe = pd.read_csv(project / UNIVERSE, sep="\t", dtype=str).fillna("")
    proteins = universe.loc[universe.get("node_type", "protein").astype(str).str.casefold().eq("protein")]
    native: dict[str, set[str]] = {}
    for symbol in proteins["symbol"].astype(str):
        native.setdefault(symbol.casefold(), set()).add(symbol)
    human: dict[str, set[str]] = {}
    human_frame = pd.read_csv(project / HUMAN_MAPPING, sep="\t", dtype=str).fillna("")
    for row in human_frame.to_dict(orient="records"):
        symbol = str(row.get("symbol", ""))
        entrez = str(row.get("human_entrez_gene_id", ""))
        if symbol and entrez:
            human.setdefault(entrez, set()).add(symbol)
    rat: dict[str, set[str]] = {}
    rat_frame = pd.read_csv(project / RAT_MAPPING, sep="\t", dtype=str).fillna("")
    if len(rat_frame.columns) == 8:
        rat_frame.columns = [
            "rat_ensembl_gene_id", "rat_gene_symbol", "mouse_ensembl_gene_id",
            "mouse_gene_symbol", "orthology_type", "mouse_percent_identity",
            "rat_percent_identity", "orthology_confidence",
        ]
    for row in rat_frame.to_dict(orient="records"):
        source = str(row.get("rat_gene_symbol", "")).casefold()
        target = str(row.get("mouse_gene_symbol", ""))
        if source and target:
            rat.setdefault(source, set()).add(target)
    return native, human, rat


def _mapped_biogrid_nodes(
    species: str,
    symbol: object,
    entrez: object,
    mappings: tuple[dict[str, set[str]], dict[str, set[str]], dict[str, set[str]]],
) -> set[str]:
    native, human, rat = mappings
    if species == "mouse":
        return native.get(str(symbol).casefold(), set())
    if species == "human":
        return human.get(str(entrez), set())
    if species == "rat":
        return rat.get(str(symbol).casefold(), set())
    return set()


def _source_ids_for_graph_node(
    graph_symbol: str,
    mapping: dict[str, set[str]],
) -> set[str]:
    """Invert one compact source-to-graph map for an on-demand pair query."""
    return {
        source_id
        for source_id, graph_symbols in mapping.items()
        if graph_symbol in graph_symbols
    }


def _unordered_membership_mask(
    left_values: pd.Series,
    right_values: pd.Series,
    left_ids: set[str],
    right_ids: set[str],
) -> pd.Series:
    if not left_ids or not right_ids:
        return pd.Series(False, index=left_values.index)
    return (
        (left_values.isin(left_ids) & right_values.isin(right_ids))
        | (left_values.isin(right_ids) & right_values.isin(left_ids))
    )


@lru_cache(maxsize=128)
def _biogrid_source_records(project_text: str, left: str, right: str) -> tuple[dict[str, Any], ...]:
    project = Path(project_text)
    path = project / BIOGRID_CATALOG
    if not path.is_file():
        return ()
    pair = canonical_pair(left, right)
    mappings = _biogrid_mapping_tables(project_text)
    native, human, rat = mappings
    mouse_left = {left.casefold()}
    mouse_right = {right.casefold()}
    human_left = _source_ids_for_graph_node(left, human)
    human_right = _source_ids_for_graph_node(right, human)
    rat_left = _source_ids_for_graph_node(left, rat)
    rat_right = _source_ids_for_graph_node(right, rat)
    columns = [
        "build", "biogrid_id_a", "biogrid_id_b", "entrez_a", "entrez_b",
        "symbol_a", "symbol_b", "species", "in_direct", "in_cocomplex",
        "n_pubs", "n_methods", "systems", "pubmed_ids", "any_low_throughput",
        "in_huri", "is_self",
    ]
    output: list[dict[str, Any]] = []
    for chunk in pd.read_csv(
        path, sep="\t", compression="gzip", dtype=str, usecols=columns,
        chunksize=100_000,
    ):
        chunk = chunk.fillna("")
        species = chunk["species"].astype(str)
        symbol_a = chunk["symbol_a"].astype(str).str.casefold()
        symbol_b = chunk["symbol_b"].astype(str).str.casefold()
        entrez_a = chunk["entrez_a"].astype(str)
        entrez_b = chunk["entrez_b"].astype(str)
        candidate_mask = (
            (species.eq("mouse") & _unordered_membership_mask(symbol_a, symbol_b, mouse_left, mouse_right))
            | (species.eq("human") & _unordered_membership_mask(entrez_a, entrez_b, human_left, human_right))
            | (species.eq("rat") & _unordered_membership_mask(symbol_a, symbol_b, rat_left, rat_right))
        )
        for row in chunk.loc[candidate_mask].to_dict(orient="records"):
            if _truth(row.get("is_self")):
                continue
            species = str(row.get("species", ""))
            left_nodes = _mapped_biogrid_nodes(species, row.get("symbol_a"), row.get("entrez_a"), mappings)
            right_nodes = _mapped_biogrid_nodes(species, row.get("symbol_b"), row.get("entrez_b"), mappings)
            if not any(canonical_pair(a, b) == pair for a in left_nodes for b in right_nodes if a != b):
                continue
            output.append(
                {
                    "build": row.get("build"),
                    "source_species": species,
                    "source_symbol_a": row.get("symbol_a"),
                    "source_symbol_b": row.get("symbol_b"),
                    "source_entrez_a": row.get("entrez_a"),
                    "source_entrez_b": row.get("entrez_b"),
                    "biogrid_id_a": row.get("biogrid_id_a"),
                    "biogrid_id_b": row.get("biogrid_id_b"),
                    "direct_contact": _truth(row.get("in_direct")),
                    "co_complex": _truth(row.get("in_cocomplex")),
                    "in_huri": _truth(row.get("in_huri")),
                    "publication_count": int(float(row.get("n_pubs") or 0)),
                    "method_count": int(float(row.get("n_methods") or 0)),
                    "experimental_systems": _split(row.get("systems", "")),
                    "pubmed_ids": _split(row.get("pubmed_ids", "")),
                    "any_low_throughput": _truth(row.get("any_low_throughput")),
                    "mapping_to_graph": f"{species} source endpoints projected to {pair[0]} — {pair[1]}",
                }
            )
    return tuple(output)


def biogrid_trace(
    project: Path,
    pair: tuple[str, str],
    *,
    applied_bayes_factor: float | None,
    weight: float,
    include_source_records: bool = True,
) -> dict[str, Any]:
    columns = (
        "node_a", "node_b", "in_direct", "in_cocomplex", "in_huri",
        "source_species", "systems", "pubmed_ids", "maximum_source_n_pubs",
        "maximum_source_n_methods", "bayes_factor", "factor_rule",
    )
    aggregate = _table_lookup(str((project / BIOGRID_MAPPED).resolve()), columns).get(pair)
    records = (
        list(_biogrid_source_records(str(project.resolve()), *pair))
        if aggregate and include_source_records
        else []
    )
    references = sorted({pmid for record in records for pmid in record["pubmed_ids"]})
    if not references and aggregate:
        references = _split(aggregate.get("pubmed_ids", ""))
    return {
        "database": "BioGRID",
        "database_version": "5.0.261",
        "trace_status": "matched" if aggregate else "no_reported_pair",
        "summary": (
            f"BioGRID contains {len(records) or 1} mapped source pair record(s). Direct-contact and co-complex reports receive one equal BF and overlapping reports are counted once."
            if aggregate else
            "BioGRID contains no mapped reported physical pair for these graph endpoints; nonreporting is neutral."
        ),
        "endpoint_mapping": [_identity(project, symbol) for symbol in pair],
        "score_derivation": {
            "database_score": "reported physical pair" if aggregate else None,
            "raw_bayes_factor": applied_bayes_factor,
            "model_weight": float(weight),
            "weighted_effective_bayes_factor": (
                float(applied_bayes_factor) ** float(weight)
                if applied_bayes_factor is not None and applied_bayes_factor > 0
                else None
            ),
            "formula": "One user-controlled BF is applied once if BioGRID reports either direct-contact or co-complex evidence.",
            "important_limit": (
                "BioGRID does not provide a single calibrated interaction-probability score here. "
                "Publication and method counts are provenance descriptors, not additional multipliers."
                + (
                    " Exact source rows were omitted from this bulk network audit; the frozen aggregate still records assay tiers, systems, species, publication IDs, and counts."
                    if not include_source_records else ""
                )
            ),
        },
        "factors": (
            [
                {"factor": "Direct-contact report", "value": _truth(aggregate.get("in_direct")), "description": "At least one direct/contact assay tier record."},
                {"factor": "Co-complex report", "value": _truth(aggregate.get("in_cocomplex")), "description": "At least one co-complex/association assay tier record."},
                {"factor": "HuRI source", "value": _truth(aggregate.get("in_huri")), "description": "At least one mapped source record belongs to HuRI."},
                {"factor": "Maximum publications per source pair", "value": int(float(aggregate.get("maximum_source_n_pubs") or 0)), "description": "Shown for provenance; not used to scale the BF."},
                {"factor": "Maximum methods per source pair", "value": int(float(aggregate.get("maximum_source_n_methods") or 0)), "description": "Shown for provenance; not used to scale the BF."},
                {"factor": "Experimental systems", "value": aggregate.get("systems", ""), "description": "BioGRID assay-system labels."},
                {"factor": "Source species", "value": aggregate.get("source_species", ""), "description": "Native mouse or orthology-projected human/rat evidence."},
            ] if aggregate else []
        ),
        "records": records,
        "links": [
            {"label": "BioGRID", "url": "https://thebiogrid.org/", "kind": "database_home"},
            *_pubmed_links(references),
        ],
    }


def closure_trace(run: Path, stream_id: str, pair: tuple[str, str]) -> dict[str, Any] | None:
    path = run / f"{stream_id}_audit.tsv.gz"
    if not path.is_file():
        return None
    frame = pd.read_csv(path, sep="\t")
    if frame.empty or not {"node_a", "node_b"}.issubset(frame.columns):
        return None
    mask = [canonical_pair(a, b) == pair for a, b in frame[["node_a", "node_b"]].itertuples(index=False, name=None)]
    rows = frame.loc[mask]
    if rows.empty:
        return None
    records = rows.fillna("").to_dict(orient="records")
    database = "BioGRID shared-partner closure" if stream_id == "biogrid_shared_partner_closure" else "Scaffold-mediated closure"
    return {
        "database": database,
        "database_version": "derived one-pass rule",
        "trace_status": "rule_triggered",
        "summary": (
            "This is inferred closure evidence, not a database-reported endpoint interaction. The triggering shared partner and rule are shown below."
        ),
        "score_derivation": {
            "raw_bayes_factor": float(rows["bayes_factor"].prod()) if "bayes_factor" in rows else None,
            "formula": str(records[0].get("closure_rule", "One-pass closure rule.")),
            "important_limit": "A closure edge must not be described as a directly observed physical interaction.",
        },
        "factors": [
            {"factor": "Supporting partner count", "value": records[0].get("supporting_partner_count"), "description": "Number of qualifying shared physical partners."},
            {"factor": "First supporting partner", "value": records[0].get("first_supporting_partner"), "description": "One auditable shared-partner identifier."},
        ],
        "records": records[:20],
        "links": [],
    }


def trace_database_evidence(
    project_root: Path | str,
    run_directory: Path | str,
    stream_id: str,
    pair: tuple[str, str],
    *,
    applied_bayes_factor: float | None,
    weight: float,
    multiplier: float | None,
    include_source_records: bool = True,
) -> dict[str, Any] | None:
    """Return database-native provenance for one stream without altering evidence."""
    project = Path(project_root).resolve()
    run = Path(run_directory).resolve()
    pair = canonical_pair(*pair)
    if stream_id == "string_v12":
        return string_trace(
            project, pair,
            applied_bayes_factor=applied_bayes_factor,
            weight=weight,
            reference_multiplier=float(multiplier or 1.0),
        )
    if stream_id == "omnipath_core":
        return omnipath_trace(
            project, pair,
            applied_bayes_factor=applied_bayes_factor,
            weight=weight,
            tq_multiplier=float(multiplier or 1.0),
        )
    if stream_id == "biogrid_physical_interaction":
        return biogrid_trace(
            project, pair,
            applied_bayes_factor=applied_bayes_factor,
            weight=weight,
            include_source_records=include_source_records,
        )
    if stream_id in {"biogrid_shared_partner_closure", "scaffold_triadic_closure"}:
        return closure_trace(run, stream_id, pair)
    return None
