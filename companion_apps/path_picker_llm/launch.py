#!/usr/bin/env python3
"""Launch Path Picker LLM as a detached or foreground local application."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PID = ROOT / ".path_picker_server.json"


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
            return json.loads(response.read()).get("application") == "path-picker-llm"
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1"); parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--foreground", action="store_true"); parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(); url = f"http://{args.host}:{args.port}"
    command = [sys.executable, str(ROOT / "server.py"), "--host", args.host, "--port", str(args.port), "--no-browser"]
    if args.foreground: return subprocess.call(command, cwd=ROOT)
    if healthy(url):
        print(f"Path Picker LLM already running: {url}"); return 0
    creationflags = (0x00000008 | 0x00000200 | 0x08000000) if os.name == "nt" else 0
    with (ROOT / "server.out.log").open("ab") as out, (ROOT / "server.err.log").open("ab") as err:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=err, creationflags=creationflags, close_fds=True)
    for _ in range(150):
        if healthy(url):
            PID.write_text(json.dumps({"pid": process.pid, "url": url}, indent=2) + "\n")
            print(f"Path Picker LLM started (PID {process.pid}): {url}")
            if not args.no_browser: webbrowser.open(url)
            return 0
        if process.poll() is not None: break
        time.sleep(0.2)
    raise RuntimeError(f"Server failed to start; inspect {ROOT / 'server.err.log'}")


if __name__ == "__main__": raise SystemExit(main())

