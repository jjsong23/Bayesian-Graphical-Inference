#!/usr/bin/env python3
"""Build the two-stage AlphaPulldown evidence campaign for Version 1.

The eligible universe is the intersection of positive support in the four
current V1 colocalization streams. BioGRID direct/contact pairs are excluded.
Phase 1 contains the remaining BioGRID co-complex pairs; phase 2 contains the
rest. The two phases are therefore disjoint by construction.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FACTOR_DIR = PROJECT_ROOT / "results/backend_bayes_factor_catalogs/edge_factors_891"
DEFAULT_STREAMS = {
    "mpkccd_localization": FACTOR_DIR / "mpkccd_localization_bf_gt1.tsv.gz",
    "compartments_localization": FACTOR_DIR / "compartments_localization_bf_gt1.tsv.gz",
    "imcd_basal_compartment_presence": FACTOR_DIR / "imcd_basal_compartment_presence_bf_gt1.tsv.gz",
    "imcd_ddavp_compartment_presence": FACTOR_DIR / "imcd_ddavp_compartment_presence_bf_gt1.tsv.gz",
}
DEFAULT_BIOGRID = (
    PROJECT_ROOT
    / "data/edge_characterization/biogrid/5.0.261/mapped_graph_physical_pairs.tsv.gz"
)
DEFAULT_PROTEIN_INDEX = (
    PROJECT_ROOT
    / "data/edge_characterization/kinase_predictor/phosphosite_database/protein_index.tsv"
)
DEFAULT_UNIPROT_TABLE = (
    PROJECT_ROOT
    / "data/edge_characterization/kinase_predictor/phosphosite_database/raw/"
    "uniprot_mouse_reference_proteome.tsv.gz"
)
PROTOCOL_ID = "alphapulldown_v1_colocalized_model1_cycles3_predictions1_compact"


def truth(value: object) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes"}


def canonical_pair(left: str, right: str) -> tuple[str, str]:
    left = str(left).strip()
    right = str(right).strip()
    if not left or not right or left == right:
        raise ValueError("pairs require two distinct nonempty symbols")
    return tuple(sorted((left, right), key=lambda value: (value.casefold(), value)))


def open_text(path: Path):
    if path.suffix.casefold() == ".gz":
        return gzip.open(path, "rt", encoding="utf-8-sig", newline="")
    return path.open("r", encoding="utf-8-sig", newline="")


def positive_pair_factors(path: Path) -> dict[tuple[str, str], float]:
    factors: dict[tuple[str, str], float] = {}
    with open_text(path) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"node_a", "node_b", "bayes_factor"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path} lacks required columns: {sorted(required)}")
        for row in reader:
            factor = float(row["bayes_factor"])
            if factor <= 1.0:
                continue
            pair = canonical_pair(row["node_a"], row["node_b"])
            factors[pair] = max(factor, factors.get(pair, 1.0))
    return factors


def biogrid_tiers(path: Path) -> tuple[set[tuple[str, str]], set[tuple[str, str]]]:
    direct: set[tuple[str, str]] = set()
    cocomplex: set[tuple[str, str]] = set()
    with open_text(path) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"node_a", "node_b", "in_direct", "in_cocomplex"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{path} lacks required columns: {sorted(required)}")
        for row in reader:
            pair = canonical_pair(row["node_a"], row["node_b"])
            if truth(row["in_direct"]):
                direct.add(pair)
            if truth(row["in_cocomplex"]):
                cocomplex.add(pair)
    return direct, cocomplex


def read_fasta(path: Path) -> tuple[dict[str, str], list[str]]:
    sequences: dict[str, str] = {}
    order: list[str] = []
    current = ""
    pieces: list[str] = []

    def finish() -> None:
        nonlocal current, pieces
        if not current:
            return
        sequence = "".join(pieces).replace(" ", "").upper()
        if not sequence:
            raise ValueError(f"empty FASTA sequence for {current}")
        if current in sequences:
            raise ValueError(f"duplicate FASTA identifier: {current}")
        sequences[current] = sequence
        order.append(current)

    with path.open("r", encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                finish()
                current = line[1:].strip().split(maxsplit=1)[0]
                pieces = []
                if not current:
                    raise ValueError(f"empty FASTA identifier in {path}")
            else:
                if not current:
                    raise ValueError(f"sequence before first FASTA header in {path}")
                pieces.append(line)
    finish()
    if not sequences:
        raise ValueError(f"no FASTA records in {path}")
    return sequences, order


def supplement_sequences_from_uniprot(
    needed: set[str],
    sequences: dict[str, str],
    protein_index_path: Path,
    uniprot_path: Path,
) -> dict[str, str]:
    """Fill symbols absent from the cache FASTA using the audited V1 mapping."""
    missing = needed - set(sequences)
    if not missing:
        return {}
    selected_accessions: dict[str, list[str]] = {}
    with protein_index_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"symbol", "selected_uniprot"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{protein_index_path} lacks {sorted(required)}")
        for row in reader:
            symbol = str(row["symbol"]).strip()
            if symbol not in missing:
                continue
            selected_accessions[symbol] = [
                value.strip().upper()
                for value in str(row["selected_uniprot"]).replace(",", ";").split(";")
                if value.strip()
            ]
    accession_to_symbols: dict[str, set[str]] = {}
    for symbol, accessions in selected_accessions.items():
        for accession in accessions:
            accession_to_symbols.setdefault(accession, set()).add(symbol)
            accession_to_symbols.setdefault(accession.split("-", 1)[0], set()).add(symbol)
    symbol_candidates: dict[str, list[tuple[bool, bool, bool, int, str]]] = {
        symbol: [] for symbol in missing
    }
    with open_text(uniprot_path) as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"Entry", "Sequence", "Gene Names (primary)", "Gene Names (synonym)", "Reviewed"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(f"{uniprot_path} lacks {sorted(required)}")
        for row in reader:
            accession = str(row["Entry"]).strip().upper()
            sequence = "".join(character for character in str(row["Sequence"]).upper() if character.isalpha())
            if not accession or not sequence:
                continue
            primary = {value.casefold() for value in str(row["Gene Names (primary)"]).split()}
            synonyms = {value.casefold() for value in str(row["Gene Names (synonym)"]).split()}
            candidates = set(accession_to_symbols.get(accession, set()))
            for symbol in missing:
                if symbol.casefold() in primary or symbol.casefold() in synonyms:
                    candidates.add(symbol)
            reviewed = str(row["Reviewed"]).strip().casefold() == "reviewed"
            for symbol in candidates:
                symbol_candidates[symbol].append(
                    (
                        accession in set(selected_accessions.get(symbol, [])),
                        symbol.casefold() in primary,
                        reviewed,
                        len(sequence),
                        sequence,
                    )
                )
    added: dict[str, str] = {}
    for symbol, candidates in symbol_candidates.items():
        if not candidates:
            continue
        selected = max(candidates, key=lambda value: value[:-1])[-1]
        sequences[symbol] = selected
        added[symbol] = selected
    return added


def pair_id(pair: tuple[str, str]) -> str:
    digest = hashlib.sha256(f"{pair[0]}\0{pair[1]}".encode("utf-8")).hexdigest()[:16]
    return f"v1_{digest}"


def write_fasta(path: Path, symbols: Iterable[str], sequences: dict[str, str]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for symbol in symbols:
            handle.write(f">{symbol}\n{sequences[symbol]}\n")


def write_stage(
    screen_dir: Path,
    name: str,
    pairs: list[tuple[str, str]],
    factors: dict[str, dict[tuple[str, str], float]],
    sequences: dict[str, str],
    selection_basis: str,
) -> dict[str, object]:
    stage = screen_dir / name
    stage.mkdir(parents=True, exist_ok=True)
    (stage / "models").mkdir(exist_ok=True)
    (stage / "logs").mkdir(exist_ok=True)
    columns = [
        "pair_id", "node_a", "node_b", "phase", "selection_basis",
        *[f"{stream}_bayes_factor" for stream in factors],
    ]
    rows: list[dict[str, object]] = []
    for pair in pairs:
        rows.append(
            {
                "pair_id": pair_id(pair),
                "node_a": pair[0],
                "node_b": pair[1],
                "phase": name,
                "selection_basis": selection_basis,
                **{
                    f"{stream}_bayes_factor": values[pair]
                    for stream, values in factors.items()
                },
            }
        )
    for filename in ("pairs.tsv", "candidate_manifest.tsv"):
        with (stage / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, delimiter="\t", lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
    symbols = sorted({symbol for pair in pairs for symbol in pair}, key=str.casefold)
    write_fasta(stage / "sequences.fasta", symbols, sequences)
    config = {
        "schema_version": 1,
        "phase": name,
        "pair_count": len(pairs),
        "protein_count": len(symbols),
        "selection_basis": selection_basis,
        "protocol_id": PROTOCOL_ID,
        "model_names": "model_1_multimer_v3",
        "num_cycle": 3,
        "num_predictions_per_model": 1,
        "metric": "iptm",
    }
    (stage / "round_config.json").write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequence-fasta", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--biogrid", type=Path, default=DEFAULT_BIOGRID)
    parser.add_argument("--protein-index", type=Path, default=DEFAULT_PROTEIN_INDEX)
    parser.add_argument("--uniprot-table", type=Path, default=DEFAULT_UNIPROT_TABLE)
    for stream, default in DEFAULT_STREAMS.items():
        parser.add_argument(f"--{stream.replace('_', '-')}", type=Path, default=default)
    parser.add_argument("--expect-all-localization", type=int)
    parser.add_argument("--expect-phase1", type=int)
    parser.add_argument("--expect-phase2", type=int)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stream_paths = {
        stream: getattr(args, stream) for stream in DEFAULT_STREAMS
    }
    for path in [
        args.sequence_fasta,
        args.biogrid,
        args.protein_index,
        args.uniprot_table,
        *stream_paths.values(),
    ]:
        if not path.is_file():
            raise FileNotFoundError(path)
    factors = {name: positive_pair_factors(path) for name, path in stream_paths.items()}
    all_localization = set.intersection(*(set(values) for values in factors.values()))
    direct, cocomplex = biogrid_tiers(args.biogrid)
    no_direct = all_localization - direct
    phase1 = sorted(no_direct & cocomplex, key=lambda pair: (pair[0].casefold(), pair[1].casefold()))
    phase2 = sorted(no_direct - cocomplex, key=lambda pair: (pair[0].casefold(), pair[1].casefold()))
    expectations = {
        "all_localization": args.expect_all_localization,
        "phase1": args.expect_phase1,
        "phase2": args.expect_phase2,
    }
    observed = {
        "all_localization": len(all_localization),
        "phase1": len(phase1),
        "phase2": len(phase2),
    }
    for key, expected in expectations.items():
        if expected is not None and observed[key] != expected:
            raise ValueError(f"{key}: expected {expected}, observed {observed[key]}")
    sequences, _ = read_fasta(args.sequence_fasta)
    needed = {symbol for pair in [*phase1, *phase2] for symbol in pair}
    original_sequence_symbols = set(sequences)
    supplemented = supplement_sequences_from_uniprot(
        needed,
        sequences,
        args.protein_index,
        args.uniprot_table,
    )
    missing = sorted(needed - set(sequences), key=str.casefold)
    if missing:
        raise ValueError(
            f"sequence FASTA is missing {len(missing)} selected proteins: "
            + ", ".join(missing[:20])
        )
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    phase1_config = write_stage(
        output, "phase1_cocomplex", phase1, factors, sequences,
        "positive in all four V1 colocalization streams; BioGRID co-complex; no BioGRID direct/contact",
    )
    phase2_config = write_stage(
        output, "phase2_other", phase2, factors, sequences,
        "positive in all four V1 colocalization streams; no BioGRID direct/contact or co-complex",
    )
    write_fasta(output / "all_selected_sequences.fasta", sorted(needed, key=str.casefold), sequences)
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "project": "graphical_bayesian_inference_v1",
        "purpose": "new structural edge evidence for V1; not a V2 search run",
        "source_sequence_fasta": str(args.sequence_fasta.resolve()),
        "source_protein_index": str(args.protein_index.resolve()),
        "source_uniprot_table": str(args.uniprot_table.resolve()),
        "sequence_sources": {
            "selected_proteins_already_in_cache_fasta": len(needed & original_sequence_symbols),
            "selected_proteins_supplemented_from_uniprot": len(supplemented),
            "selected_proteins_missing_after_supplement": len(missing),
        },
        "source_streams": {name: str(path.resolve()) for name, path in stream_paths.items()},
        "source_biogrid": str(args.biogrid.resolve()),
        "counts": {
            "supported_by_all_four_colocalization_streams": len(all_localization),
            "direct_biogrid_within_all_localization": len(all_localization & direct),
            "without_direct_biogrid": len(no_direct),
            "phase1_cocomplex_without_direct": len(phase1),
            "phase2_other_without_direct": len(phase2),
            "phase_overlap": len(set(phase1) & set(phase2)),
            "total_scheduled_if_both_phases": len(phase1) + len(phase2),
        },
        "missing_predictions_rule": "unobserved and neutral; never imputed as negative evidence",
        "structural_score_mapping": {
            "metric": "iptm",
            "reference_score": 0.6,
            "bayes_factor_floor": 0.1,
            "bayes_factor_ceiling": 10.0,
            "status": "heuristic pending empirical V1 calibration",
        },
        "phases": [phase1_config, phase2_config],
    }
    (output / "screen_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
