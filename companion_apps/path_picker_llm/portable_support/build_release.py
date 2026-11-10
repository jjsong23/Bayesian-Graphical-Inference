#!/usr/bin/env python3
"""Build private, relocatable research handoffs; never contact a remote service.

SQLite files use the online backup API. Files are secret-scanned before archive
publication. The manifest records the exact selected scope and omissions.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PROJECTS = ("graphical_bayesian_inference", "path_picker_llm", "cd_vp_ground_truth", "cd_vp_static_causal")
SKIP_DIRS = {".git", ".venv", ".vendor", "__pycache__", "node_modules", ".codex_temp_deps", ".local_python_packages", ".pip_tmp", ".test_python_deps", ".pytest_cache"}
SKIP_NAMES = {".path_picker_server.json", ".ground_truth_workbench.json", ".workbench_server.json"}
SECRET_NAMES = {".env", ".env.local", "credentials.json", "secrets.json", "credentials", "token.json"}
SECRET_SUFFIXES = {".pem", ".key", ".pfx", ".p12"}
PATTERNS = (
    ("private-key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("openai-key", re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b")),
    ("github-token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("aws-access-key", re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("credential-assignment", re.compile(rb"(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password|authorization)[\"']?\s*[:=]\s*[\"'](?:Bearer\s+)?([A-Za-z0-9_./+=-]{24,})[\"']")),
)
PATTERN_MARKERS = {
    "private-key": (b"-----BEGIN ",), "openai-key": (b"sk-",),
    "github-token": (b"ghp_", b"gho_", b"ghu_", b"ghs_", b"ghr_", b"github_pat_"),
    "aws-access-key": (b"AKIA", b"ASIA"),
    "credential-assignment": (b"apikey", b"api_key", b"api-key", b"accesstoken", b"access_token", b"access-token", b"clientsecret", b"client_secret", b"client-secret", b"password", b"authorization"),
}
HISTORY_PREFIXES = (
    "graphical_bayesian_inference/results/gui_runs/",
    "graphical_bayesian_inference/results/prkaca_multitarget/",
    "graphical_bayesian_inference/outputs/",
)
EXTRA_INPUTS = (
    "function_prediction_runs/20261105_pka_functions_kirby_agent/PKA_source_native_predictions_20261109.zip",
    "function_prediction_runs/20261104_pka_functions_4modes/snapshot_project/results/gui_runs/20261104_pka_functions_tuned_known_edges_retry2",
    "outputs/019f860e-7e5f-75f3-876a-7f443d20be08/ccd_multiregime_43_observation_causal_input.xlsx",
)
MAIN_RUNTIME_FILES = tuple(
    "graphical_bayesian_inference/results/gui_runs/20261103_163859_8fe27c63/" + name
    for name in ("supported_edges.tsv.gz", "full_graph_node_statistics.tsv.gz", "node_posteriors.tsv.gz")
)


def file_access(path: Path) -> str:
    """Use extended Windows paths, including for CopyFile2's stricter limit."""
    value = str(path.resolve())
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return value


def safe_copy(source: Path, target: Path) -> None:
    shutil.copy2(file_access(source), file_access(target))


def digest_and_scan(path: Path) -> tuple[str, list[str]]:
    digest = hashlib.sha256()
    findings = set()
    overlap = b""
    with open(file_access(path), "rb") as stream:
        while block := stream.read(4 * 1024 * 1024):
            digest.update(block)
            content = overlap + block
            lower = content.lower()
            for label, pattern in PATTERNS:
                # These literals are necessary components of each regex. The
                # fast guard preserves matching semantics and avoids running
                # an unanchored case-insensitive regex over gigabytes of
                # numeric evidence that contain no credential field names.
                haystack = lower if label == "credential-assignment" else content
                if not any(marker in haystack for marker in PATTERN_MARKERS[label]):
                    continue
                for match in pattern.finditer(content):
                    value = match.group(1) if label == "credential-assignment" else match.group(0)
                    if value.lower().startswith((b"dummy", b"fake", b"test", b"example", b"replace", b"your_", b"sk-test")):
                        continue
                    findings.add(label)
            overlap = content[-512:]
    return digest.hexdigest(), sorted(findings)


def skip_directory(path: Path) -> str | None:
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        return "external link / nonportable dependency"
    if path.name in SKIP_DIRS:
        return "environment, cache, or Git metadata"
    if path.name.startswith("tmp") or path.name.startswith("test_") or path.name == "codex_test_tmp":
        return "temporary/test-runtime fixture directory"
    return None


def inventory(root: Path, tier: str) -> tuple[list[Path], list[dict]]:
    selected, excluded = [], []
    for project in PROJECTS:
        base = root / project
        if not base.is_dir():
            raise FileNotFoundError(base)
        for directory, directories, names in os.walk(base, followlinks=False):
            current = Path(directory)
            for name in list(directories):
                reason = skip_directory(current / name)
                if reason:
                    directories.remove(name)
                    excluded.append({"path": (current / name).relative_to(root).as_posix() + "/", "reason": reason})
            for name in names:
                path = current / name
                relative = path.relative_to(root).as_posix()
                reason = None
                if name in SECRET_NAMES or path.suffix.lower() in SECRET_SUFFIXES:
                    reason = "credential-like file; never packaged"
                elif name in SKIP_NAMES or name.endswith((".log", ".pyc", ".pyo", "-wal", "-shm", ".inspect.ndjson")) or name.startswith(".workbench"):
                    reason = "ephemeral process state, log, or duplicate inspection output"
                elif path.is_symlink():
                    reason = "external file link"
                elif tier == "main" and relative.startswith(HISTORY_PREFIXES) and relative not in MAIN_RUNTIME_FILES:
                    reason = "in optional BGI history archive"
                elif tier == "history" and not relative.startswith(HISTORY_PREFIXES):
                    continue
                if reason:
                    excluded.append({"path": relative, "reason": reason})
                else:
                    selected.append(path)
    if tier == "main":
        for relative in EXTRA_INPUTS:
            target = root / relative
            if not target.exists():
                raise FileNotFoundError(f"Saved Path Picker input missing: {target}")
            selected.extend([target] if target.is_file() else sorted(path for path in target.rglob("*") if path.is_file()))
    return sorted(selected), sorted(excluded, key=lambda item: item["path"])


def is_sqlite(path: Path) -> bool:
    with open(file_access(path), "rb") as stream:
        return stream.read(16) == b"SQLite format 3\0"


def redact_signed_url_auth(source: Path) -> tuple[bytes, int]:
    """Remove only bearer-like query credentials from copied source audit text."""
    if source.suffix.lower() not in {".json", ".jsonl", ".html", ".txt", ".md", ".ndjson"}:
        raise ValueError("Automatic URL credential redaction is limited to text audit artifacts")
    with open(file_access(source), "rb") as stream:
        raw = stream.read()
    pattern = re.compile(rb"(?i)([?&](?:X-Amz-(?:Credential|Signature|Security-Token)|AWSAccessKeyId)=)[^&\s\"<>]+")
    cleaned, count = pattern.subn(lambda match: match.group(1) + b"REDACTED_FOR_PRIVATE_TRANSFER", raw)
    if not count:
        raise ValueError("Credential match was not a recognized presigned URL")
    if source.suffix.lower() == ".json":
        json.loads(cleaned)
    return cleaned, count


def sqlite_backup(source: Path, target: Path) -> None:
    # mode=ro still observes a live WAL, unlike immutable=1 or copying the DB.
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)) as reader:
        with closing(sqlite3.connect(target)) as writer:
            reader.backup(writer, pages=4096, sleep=0.05)
            result = writer.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"SQLite backup quick_check failed: {source.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tier", choices=("main", "history"), default="main")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--name")
    parser.add_argument("--resume-staging", action="store_true", help="Revalidate and reuse an interrupted unpublished staging folder")
    args = parser.parse_args()
    root = args.workspace.resolve()
    output = (args.output or root / "outputs" / "portable_release").resolve()
    output.mkdir(parents=True, exist_ok=True)
    files, excluded = inventory(root, args.tier)
    total = sum(path.stat().st_size for path in files)
    print(json.dumps({"tier": args.tier, "files": len(files), "bytes": total, "free_disk_bytes": shutil.disk_usage(output).free}, indent=2), flush=True)
    if args.inventory_only:
        return 0
    name = args.name or f"research_workbench_{args.tier}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("Archive name must be a simple filename")
    staging = output / name
    archive = output / f"{name}.zip"
    if archive.exists() or (staging.exists() and not args.resume_staging):
        raise FileExistsError("Choose a new release name; existing releases are never overwritten")
    staging.mkdir(exist_ok=args.resume_staging)
    staging_io = Path(file_access(staging))
    manifest = {"schema_version": 1, "created_utc": datetime.now(timezone.utc).isoformat(), "tier": args.tier,
                "source_workspace": str(root), "snapshot_policy": "Per-SQLite online backup; files are point-in-time copies, not a global transactional snapshot",
                "redistribution": "Private research transfer only. External datasets retain their source terms; this is not a redistribution license.",
                "not_in_scope": ["graphical_bayesian_inference_v2/", "function_prediction_runs/ except the two exact original inputs listed in saved_input_targets", "workspace-level scratch/artifact folders"],
                "saved_input_targets": list(EXTRA_INPUTS) if args.tier == "main" else [],
                "static_causal_seed_graph": list(MAIN_RUNTIME_FILES) if args.tier == "main" else [],
                "files": [], "excluded": excluded, "secret_scan": {"policy": "known token/private-key patterns and long credential assignments; heuristic, not a guarantee", "findings": []}}
    started = time.monotonic()
    for index, source in enumerate(files, 1):
        relative = source.relative_to(root)
        target = staging_io / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        db = is_sqlite(source)
        source_stat = source.stat()
        redaction = None
        if db:
            sqlite_backup(source, target)
            sha, hits = digest_and_scan(target)
            source_sha = None
        else:
            source_sha, hits = digest_and_scan(source)
            if hits == ["aws-access-key"]:
                try:
                    cleaned, count = redact_signed_url_auth(source)
                except (ValueError, UnicodeError):
                    target = None
                else:
                    with open(file_access(target), "wb") as stream:
                        stream.write(cleaned)
                    sha, hits = digest_and_scan(target)
                    redaction = {"kind": "presigned_url_auth_query_values", "parameter_values_redacted": count, "reason": "temporary source URL credentials must not be transferred"}
            elif hits:
                target = None
            else:
                reusable = False
                if args.resume_staging and target.is_file():
                    sha, copied_hits = digest_and_scan(target)
                    reusable = sha == source_sha and not copied_hits
                if not reusable:
                    safe_copy(source, target)
                sha, copied_hits = digest_and_scan(target)
                hits += copied_hits
                if sha != source_sha:
                    raise RuntimeError(f"Source changed while copying; create a fresh release: {relative}")
        if hits:
            issue = {"path": relative.as_posix(), "categories": sorted(set(hits))}
            (output / f"{name}.blocked.json").write_text(json.dumps(issue, indent=2), encoding="utf-8")
            raise RuntimeError(f"Secret-pattern scan blocked publication: {relative}; categories={','.join(hits)} (values suppressed)")
        row = {"path": relative.as_posix(), "bytes": target.stat().st_size, "sha256": sha,
                                  "source_sha256": source_sha, "source_bytes_observed": source_stat.st_size,
                                  "copy_method": "sqlite-online-backup" if db else ("redacted-text-copy" if redaction else "byte-copy")}
        if redaction:
            row["redaction"] = redaction
        manifest["files"].append(row)
        if index % 100 == 0 or source_stat.st_size > 512 * 1024 * 1024:
            print(f"Staged {index}/{len(files)} ({time.monotonic()-started:.0f}s): {relative}", flush=True)
    if args.tier == "main":
        templates = Path(__file__).resolve().parent / "templates"
        for source in sorted(templates.iterdir()):
            if source.is_file():
                target = staging_io / source.name
                safe_copy(source, target)
                sha, hits = digest_and_scan(target)
                if hits:
                    raise RuntimeError(f"Template failed secret-pattern scan: {source.name}")
                manifest["files"].append({"path": source.name, "bytes": target.stat().st_size, "sha256": sha, "source_sha256": sha, "copy_method": "generated-release-support"})
    manifest_path = staging / ("PORTABLE_MANIFEST.json" if args.tier == "main" else "PORTABLE_HISTORY_MANIFEST.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.tier == "main":
        (staging / "PORTABLE_ORIGIN.json").write_text(json.dumps({"source_workspace": str(root), "created_utc": manifest["created_utc"]}, indent=2) + "\n", encoding="utf-8")
    print("Creating ZIP64 archive (compression level 1)...", flush=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as bundle:
        # Paths are relative to the workspace root so the history ZIP merges cleanly.
        for path in sorted(staging_io.rglob("*")):
            if path.is_file():
                bundle.write(file_access(path), path.relative_to(staging_io).as_posix())
    sha, hits = digest_and_scan(archive)
    if hits:
        raise RuntimeError("Archive secret-pattern scan failed")
    (output / f"{name}.zip.sha256").write_text(f"{sha}  {archive.name}\n", encoding="ascii")
    summary = {"archive": str(archive), "staging": str(staging), "tier": args.tier, "file_count": len(manifest["files"]),
               "uncompressed_bytes": sum(row["bytes"] for row in manifest["files"]), "zip_bytes": archive.stat().st_size,
               "sha256": sha, "elapsed_seconds": round(time.monotonic()-started, 1)}
    (output / f"{name}.summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
