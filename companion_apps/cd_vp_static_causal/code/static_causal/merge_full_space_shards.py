from __future__ import annotations

import argparse
import csv
import gzip
import glob
import json
from collections import Counter
from pathlib import Path

import pandas as pd


def merge_shards(inputs: list[Path], output: Path) -> dict[str, object]:
    output.mkdir(parents=True, exist_ok=True)
    seen_shards = set()
    edge_rows: dict[str, dict[str, object]] = {}
    node_counts: Counter[str] = Counter()
    ranking_frames = []
    source_edge_count = None
    for directory in inputs:
        manifest = json.loads((directory / "full_space_manifest.json").read_text(encoding="utf-8"))
        config = json.loads((directory / "full_space_resolved_configuration.json").read_text(encoding="utf-8"))
        scan = config.get("full_space", {}).get("coverage_scan", {})
        shard = (int(scan.get("shard_index", -1)), int(scan.get("shard_count", -1)))
        if shard in seen_shards:
            raise ValueError(f"duplicate shard {shard}")
        seen_shards.add(shard)
        if source_edge_count is None:
            source_edge_count = int(manifest["full_registry_edge_count"])
        elif source_edge_count != int(manifest["full_registry_edge_count"]):
            raise ValueError("shards do not share the same source edge count")
        ledger = directory / str(manifest.get("coverage_ledger", "shard_edge_coverage.tsv.gz"))
        with gzip.open(ledger, "rt", encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                edge_id = row["edge_id"]
                if edge_id in edge_rows:
                    edge_rows[edge_id]["graphs_containing"] = int(edge_rows[edge_id]["graphs_containing"]) + int(row["graphs_containing"])
                else:
                    edge_rows[edge_id] = dict(row)
                    edge_rows[edge_id]["graphs_containing"] = int(row["graphs_containing"])
        with (directory / "full_node_coverage.tsv").open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle, delimiter="\t"):
                node_counts[row["node"]] += int(row["graphs_containing"])
        frame = pd.read_csv(directory / "full_graph_rankings.tsv", sep="\t")
        frame.insert(0, "coverage_shard", shard[0])
        frame["graph_id"] = frame["graph_id"].map(lambda value: f"S{shard[0]:03d}_{value}")
        ranking_frames.append(frame)
    with gzip.open(output / "merged_edge_coverage.tsv.gz", "wt", encoding="utf-8", newline="") as handle:
        fields = ["edge_id", "node_a", "node_b", "source_score", "mapped_preference", "confidence_quartile", "graphs_containing"]
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for edge_id in sorted(edge_rows, key=lambda value: int(value[2:])):
            writer.writerow(edge_rows[edge_id])
    pd.DataFrame([{"node": node, "graphs_containing": count} for node, count in sorted(node_counts.items())]).to_csv(output / "merged_node_coverage.tsv", sep="\t", index=False)
    if ranking_frames:
        pd.concat(ranking_frames, ignore_index=True).to_csv(output / "merged_graph_rankings.tsv.gz", sep="\t", index=False, compression="gzip")
    summary = {
        "schema_version": 1, "input_shard_count": len(inputs), "shards": sorted([list(value) for value in seen_shards]),
        "source_edge_count": source_edge_count, "covered_edge_count": len(edge_rows),
        "covered_edge_fraction": len(edge_rows) / max(1, int(source_edge_count or 0)),
        "covered_node_count": sum(value > 0 for value in node_counts.values()),
        "complete_single_pass": len(edge_rows) == source_edge_count,
        "note": "Coverage is exposure, not posterior probability or biological validation.",
    }
    (output / "merged_coverage_manifest.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge sparse full-space coverage shards")
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    expanded = []
    for value in args.inputs:
        matches = glob.glob(value)
        expanded.extend(matches or [value])
    print(json.dumps(merge_shards([Path(value).resolve() for value in expanded], Path(args.output).resolve()), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
