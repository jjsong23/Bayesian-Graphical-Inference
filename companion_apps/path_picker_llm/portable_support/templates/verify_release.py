#!/usr/bin/env python3
"""Read-only SHA-256 verification of a newly extracted main/history release."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Verify optional restored historical files too")
    args = parser.parse_args()
    names = ["PORTABLE_MANIFEST.json"]
    if args.history:
        names.append("PORTABLE_HISTORY_MANIFEST.json")
    failures = []
    checked = 0
    for name in names:
        manifest = ROOT / name
        if not manifest.is_file():
            failures.append({"path": name, "error": "manifest missing"})
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in data["files"]:
            path = (ROOT / entry["path"]).resolve()
            try:
                path.relative_to(ROOT)
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                if digest != entry["sha256"]:
                    failures.append({"path": entry["path"], "error": "hash mismatch (expected after relocation/resumed work)"})
                checked += 1
            except (OSError, ValueError) as error:
                failures.append({"path": entry["path"], "error": type(error).__name__})
    print(json.dumps({"checked": checked, "failures": failures, "note": "Verify before relocation or launching, because those intentionally update runtime files."}, indent=2))
    return 1 if failures else 0

if __name__ == "__main__":
    raise SystemExit(main())
