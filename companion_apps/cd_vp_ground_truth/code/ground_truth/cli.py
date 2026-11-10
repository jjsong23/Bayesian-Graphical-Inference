#!/usr/bin/env python3
"""Run or inspect ground-truth curation protocol v0.6."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ground_truth.pipeline import GroundTruthPipeline, dry_run
else:
    from .pipeline import GroundTruthPipeline, dry_run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    report = dry_run(config, args.run_dir) if args.dry_run else GroundTruthPipeline(config, args.run_dir).run()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
