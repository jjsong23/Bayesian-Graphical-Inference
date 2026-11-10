#!/usr/bin/env python3
"""Create an isolated Python environment and install the workbench dependencies."""
from __future__ import annotations
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="Install only from wheels/ (must be supplied separately)")
    parser.add_argument("--causal-engine", action="store_true", help="Also install optional y0 engine")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        raise SystemExit("Python 3.11 or later is required; Python 3.11/3.12 is recommended.")
    environment = ROOT / ".venv"
    if not environment.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(environment)])
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    command = [str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]
    if args.offline:
        command += ["--no-index", "--find-links", str(ROOT / "wheels")]
    subprocess.check_call(command, cwd=ROOT)
    if args.causal_engine:
        optional = [str(python), "-m", "pip", "install", "-r", str(ROOT / "cd_vp_static_causal" / "requirements-engine.txt")]
        if args.offline:
            optional += ["--no-index", "--find-links", str(ROOT / "wheels")]
        subprocess.check_call(optional, cwd=ROOT)
    print("Setup complete. Run: python launch.py --relocate --check")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
