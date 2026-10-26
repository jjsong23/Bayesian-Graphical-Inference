#!/usr/bin/env python3
"""Stage a V1-owned feature directory and identify proteins needing MSAs.

Existing nonempty monomer objects are linked from a source cache without
modifying it. Proteins absent from that cache are written to a targeted FASTA
for AlphaPulldown feature generation inside the V1 screen directory.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def read_fasta(path: Path) -> list[tuple[str, str]]:
    records: list[tuple[str, str]] = []
    current = ""
    pieces: list[str] = []

    def finish() -> None:
        nonlocal current, pieces
        if current:
            records.append((current, "".join(pieces)))

    with path.open("r", encoding="utf-8-sig") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith(">"):
                finish()
                current = line[1:].split(maxsplit=1)[0]
                pieces = []
            else:
                if not current:
                    raise ValueError("sequence before first FASTA header")
                pieces.append(line)
    finish()
    if len({symbol for symbol, _ in records}) != len(records):
        raise ValueError("duplicate FASTA identifiers")
    return records


def feature_object(directory: Path, symbol: str) -> Path | None:
    for suffix in (".pkl", ".pkl.gz"):
        candidate = directory / f"{symbol}{suffix}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sequence-fasta", type=Path, required=True)
    parser.add_argument("--source-feature-dir", type=Path, required=True)
    parser.add_argument("--output-feature-dir", type=Path, required=True)
    parser.add_argument("--missing-fasta", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records = read_fasta(args.sequence_fasta.resolve())
    source = args.source_feature_dir.resolve()
    output = args.output_feature_dir.resolve()
    missing_fasta = args.missing_fasta.resolve()
    summary_path = args.summary.resolve()
    output.mkdir(parents=True, exist_ok=True)
    missing_fasta.parent.mkdir(parents=True, exist_ok=True)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    linked = 0
    already_present = 0
    missing: list[tuple[str, str]] = []
    for symbol, sequence in records:
        if feature_object(output, symbol) is not None:
            already_present += 1
            continue
        existing = feature_object(source, symbol)
        if existing is None:
            missing.append((symbol, sequence))
            continue
        destination = output / existing.name
        if destination.exists() or destination.is_symlink():
            raise ValueError(f"invalid destination blocks feature link: {destination}")
        os.symlink(existing, destination)
        linked += 1
    with missing_fasta.open("w", encoding="utf-8", newline="\n") as handle:
        for symbol, sequence in missing:
            handle.write(f">{symbol}\n{sequence}\n")
    summary = {
        "schema_version": 1,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "selected_protein_count": len(records),
        "feature_objects_already_in_v1_directory": already_present,
        "feature_objects_linked_from_source_cache": linked,
        "feature_objects_requiring_generation": len(missing),
        "missing_symbols": [symbol for symbol, _ in missing],
        "source_feature_directory": str(source),
        "v1_feature_directory": str(output),
        "missing_sequence_fasta": str(missing_fasta),
        "source_cache_modified": False,
    }
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
