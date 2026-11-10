#!/usr/bin/env python3
"""Launch the independent collecting-duct ground-truth curation workbench."""

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


PROJECT_ROOT = Path(__file__).resolve().parent
SERVER = PROJECT_ROOT / "gui" / "server.py"
PID_FILE = PROJECT_ROOT / "gui" / ".ground_truth_workbench.json"


def server_environment() -> dict[str, str]:
    """Avoid passing Codex's intentionally dead network proxy to the local app."""

    environment = os.environ.copy()
    dead_local_proxies = {"http://127.0.0.1:9", "https://127.0.0.1:9"}
    for name in ("ALL_PROXY", "HTTP_PROXY", "HTTPS_PROXY", "GIT_HTTP_PROXY", "GIT_HTTPS_PROXY"):
        if environment.get(name, "").strip().rstrip("/").casefold() in dead_local_proxies:
            environment.pop(name, None)
    return environment


def healthy(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/health", timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("application") == "cd-vp-ground-truth"
    except Exception:
        return False


def launch_detached(host: str, port: int, timeout: float) -> int:
    url = f"http://{host}:{port}"
    if healthy(url):
        print(f"Ground-truth workbench is already running: {url}")
        return 0
    stdout_path = PROJECT_ROOT / "gui" / "server.stdout.log"
    stderr_path = PROJECT_ROOT / "gui" / "server.stderr.log"
    creation_flags = 0
    startupinfo = None
    if os.name == "nt":
        creation_flags = 0x00000008 | 0x00000200 | 0x08000000
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
        process = subprocess.Popen(
            [sys.executable, str(SERVER), "--host", host, "--port", str(port), "--no-browser"],
            cwd=PROJECT_ROOT,
            env=server_environment(),
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            creationflags=creation_flags,
            startupinfo=startupinfo,
            close_fds=True,
        )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy(url):
            PID_FILE.write_text(json.dumps({"pid": process.pid, "host": host, "port": port}, indent=2) + "\n", encoding="utf-8")
            print(f"Ground-truth workbench started (PID {process.pid}): {url}")
            return 0
        if process.poll() is not None:
            break
        time.sleep(0.2)
    raise RuntimeError(f"Server did not become healthy. Inspect {stderr_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--foreground", action="store_true")
    args = parser.parse_args()
    if args.foreground:
        command = [sys.executable, str(SERVER), "--host", args.host, "--port", str(args.port)]
        if args.no_browser:
            command.append("--no-browser")
        return subprocess.call(command, cwd=PROJECT_ROOT)
    result = launch_detached(args.host, args.port, args.timeout)
    if result == 0 and not args.no_browser:
        webbrowser.open(f"http://{args.host}:{args.port}/")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
