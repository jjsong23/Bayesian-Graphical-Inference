#!/usr/bin/env python3
"""Command-line entry point for the V1 causal-modeling pilot workflow."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CODE_ROOT = PROJECT_ROOT / "code"
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from causal_modeling.workflow import (  # noqa: E402
    PilotConfiguration,
    default_output_directory,
    run_pilot,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fit and compare a compact continuous-time causal pilot using V1 ranked paths "
            "as candidate hypotheses and replicate-level dDAVP phosphoproteomics as observations."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=PROJECT_ROOT / "configs" / "causal_modeling" / "prkaca_aqp2_pilot.json",
        help="JSON configuration file",
    )
    parser.add_argument("--output", type=Path, help="output directory (timestamped by default)")
    parser.add_argument("--fast", action="store_true", help="one optimizer start and fewer evaluations")
    return parser


def _resolve(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def main() -> int:
    args = _parser().parse_args()
    config_path = args.config.resolve()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    configuration = PilotConfiguration(
        phospho_workbook=_resolve(PROJECT_ROOT, payload["phospho_workbook"]),
        ranked_paths=_resolve(PROJECT_ROOT, payload["ranked_paths"]),
        ko_workbooks=tuple(_resolve(PROJECT_ROOT, item) for item in payload.get("ko_workbooks", [])),
        output_directory=(args.output or default_output_directory(PROJECT_ROOT)),
        maximum_states=int(payload.get("maximum_states", 6)),
        path_cutoffs=tuple(int(value) for value in payload.get("path_cutoffs", [1, 5, 20, 100])),
        ridge=float(payload.get("ridge", 0.1)),
        starts=1 if args.fast else int(payload.get("starts", 2)),
        max_nfev=800 if args.fast else int(payload.get("max_nfev", 2500)),
        seed=int(payload.get("seed", 20261015)),
        maximum_edge_ablations=int(payload.get("maximum_edge_ablations", 10)),
        maximum_repairs=int(payload.get("maximum_repairs", 8)),
    )
    output = run_pilot(configuration)
    print(f"Causal-modeling pilot complete: {output}")
    print(f"Report: {output / 'scientific_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
