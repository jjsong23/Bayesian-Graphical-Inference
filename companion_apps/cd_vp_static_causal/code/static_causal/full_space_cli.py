from __future__ import annotations

import argparse
import json
from pathlib import Path

from .full_space import run_full_space


def main() -> int:
    parser = argparse.ArgumentParser(description="Explore the full V1 node/edge registry with sparse scoring")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--coverage-shard-index", type=int)
    parser.add_argument("--coverage-shard-count", type=int)
    parser.add_argument("--coverage-seed-edges-per-graph", type=int)
    parser.add_argument("--coverage-passes", type=int)
    parser.add_argument("--coverage-only", action="store_true", help="Generate only the deterministic graphs needed for this shard")
    parser.add_argument("--runtime-budget-seconds", type=float, help="Maximum wall-clock budget including final output")
    parser.add_argument("--target-graph-count", type=int, help="Upper bound on unique graphs")
    args = parser.parse_args()
    config = json.loads(Path(args.config).resolve().read_text(encoding="utf-8"))
    scan = config.setdefault("full_space", {}).setdefault("coverage_scan", {})
    if args.coverage_shard_index is not None:
        scan["enabled"] = True
        scan["shard_index"] = args.coverage_shard_index
    if args.coverage_shard_count is not None:
        scan["shard_count"] = args.coverage_shard_count
    if args.coverage_seed_edges_per_graph is not None:
        scan["seed_edges_per_graph"] = args.coverage_seed_edges_per_graph
    if args.coverage_passes is not None:
        scan["passes"] = args.coverage_passes
    if args.coverage_only:
        scan["enabled"] = True
        scan["only"] = True
        config.setdefault("dynamic_fit", {})["budget"] = 0
    if args.runtime_budget_seconds is not None:
        config.setdefault("full_space", {})["runtime_budget_seconds"] = args.runtime_budget_seconds
    if args.target_graph_count is not None:
        config.setdefault("full_space", {})["target_graph_count"] = args.target_graph_count
    print(json.dumps(run_full_space(config, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

