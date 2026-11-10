#!/usr/bin/env python3
"""Independent local HTTP server for collecting-duct ground-truth curation."""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import workbench


WEB_ROOT = Path(__file__).resolve().parent / "web"


class GroundTruthHandler(BaseHTTPRequestHandler):
    server_version = "CDVPGroundTruth/0.6"

    def log_message(self, format: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > 2_000_000:
            raise ValueError("request body is empty or too large")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        if path == "/api/health":
            self.send_json({"status": "ok", "application": "cd-vp-ground-truth", "protocol": "0.6"})
            return
        if path == "/api/ground-truth/config":
            self.send_json({
                "defaults": workbench.load_default_configuration(),
                "active_job": workbench.latest_job(),
                "protocol": {
                    "version": "0.6",
                    "description": (
                        "Evidence-class-specific positive and explicit-negative "
                        "discovery across the acute PKA-responsive network"
                    ),
                },
            })
            return
        if path == "/api/ground-truth/jobs/active":
            self.send_json({"active_job": workbench.latest_job()})
            return
        if path.startswith("/api/ground-truth/jobs/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) == 4:
                payload = workbench.job_payload(parts[3])
                self.send_json(payload if payload else {"error": "job not found"}, HTTPStatus.OK if payload else HTTPStatus.NOT_FOUND)
                return
            if len(parts) == 6 and parts[4] == "files":
                self.send_job_file(parts[3], parts[5])
                return
        if path.startswith("/api/"):
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        self.send_static(path)

    def do_POST(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        parts = [part for part in path.split("/") if part]
        if path == "/api/ground-truth/jobs":
            try:
                payload = self.read_json()
                key = self.headers.get("X-Azure-OpenAI-API-Key", "").strip() or None
                job = workbench.create_job(payload, key)
                self.send_json(workbench.public_job(job), HTTPStatus.ACCEPTED)
            except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        if len(parts) == 5 and parts[:3] == ["api", "ground-truth", "jobs"] and parts[4] == "cancel":
            payload = workbench.cancel_job(parts[3])
            self.send_json(payload if payload else {"error": "job not found"}, HTTPStatus.ACCEPTED if payload else HTTPStatus.NOT_FOUND)
            return
        if len(parts) == 5 and parts[:3] == ["api", "ground-truth", "jobs"] and parts[4] == "retry":
            try:
                key = self.headers.get("X-Azure-OpenAI-API-Key", "").strip() or None
                job = workbench.retry_job(parts[3], key)
                self.send_json(workbench.public_job(job), HTTPStatus.ACCEPTED)
            except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
                self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    def send_job_file(self, job_id: str, requested_name: str) -> None:
        safe_name = Path(requested_name).name
        if safe_name != requested_name:
            self.send_json({"error": "invalid file name"}, HTTPStatus.BAD_REQUEST)
            return
        file_path = workbench.job_file(job_id, safe_name)
        if file_path is None:
            self.send_json({"error": "file not found"}, HTTPStatus.NOT_FOUND)
            return
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
        self.end_headers()
        with file_path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                self.wfile.write(block)

    def send_static(self, requested_path: str) -> None:
        relative = requested_path.lstrip("/") or "index.html"
        file_path = (WEB_ROOT / relative).resolve()
        if WEB_ROOT.resolve() not in file_path.parents and file_path != WEB_ROOT.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = file_path.read_bytes()
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    workbench.recover_latest_job()
    server = ThreadingHTTPServer((args.host, args.port), GroundTruthHandler)
    url = f"http://{args.host}:{server.server_port}/"
    print(f"Collecting-duct ground-truth workbench: {url}")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
