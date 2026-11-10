from __future__ import annotations

import argparse
import json
from pathlib import Path

from .exploration import run_exploration


def main() -> int:
    parser = argparse.ArgumentParser(description="Build and score a diverse signaling-graph exploration bank")
    parser.add_argument("--config", required=True, help="JSON configuration")
    parser.add_argument("--output", required=True, help="Output directory")
    args = parser.parse_args()
    config = json.loads(Path(args.config).resolve().read_text(encoding="utf-8"))
    print(json.dumps(run_exploration(config, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

