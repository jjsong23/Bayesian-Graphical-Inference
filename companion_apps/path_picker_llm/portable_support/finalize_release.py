"""Finalize a not-yet-published build with late code/input updates.

Updates ZIP member records without recompressing unchanged scientific payloads.
Only this builder's local outputs are accepted; source data are never modified.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from build_release import EXTRA_INPUTS, MAIN_RUNTIME_FILES, digest_and_scan, file_access, inventory, safe_copy

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    archive_path, root = args.archive.resolve(), args.workspace.resolve()
    expected_output = (root / "outputs/portable_release").resolve()
    archive_path.relative_to(expected_output)
    stage = archive_path.with_suffix("")
    stage_io = Path(file_access(stage))
    summary_path = archive_path.with_suffix(".summary.json")
    if not summary_path.is_file():
        raise RuntimeError("Build must complete successfully before finalizing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    tier = summary["tier"]
    old_name = "PORTABLE_MANIFEST.json"
    new_name = "PORTABLE_MANIFEST.json" if tier == "main" else "PORTABLE_HISTORY_MANIFEST.json"
    manifest_path = stage / old_name
    if not manifest_path.is_file():
        manifest_path = stage / new_name
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    by_path = {row["path"]: row for row in manifest["files"]}
    updates = {}
    if tier == "main":
        candidates, exclusions = inventory(root, "main")
        for source in candidates:
            relative = source.relative_to(root).as_posix()
            components = source.relative_to(root).parts
            source_code = len(components) <= 2 or (len(components) > 2 and components[1] in {"code", "gui", "configs", "docs", "path_picker", "web", "tests", "portable_support", "tools", "biowulf", "notebooks"})
            extra_input = relative in MAIN_RUNTIME_FILES or any(relative == entry or relative.startswith(entry + "/") for entry in EXTRA_INPUTS)
            if not source_code and not extra_input:
                continue
            sha, hits = digest_and_scan(source)
            if hits:
                raise RuntimeError(f"Credential-pattern scan blocked update: {relative}; values suppressed")
            if relative in by_path and sha == by_path[relative]["sha256"]:
                continue
            target = stage_io / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            safe_copy(source, target)
            copied_sha, hits = digest_and_scan(target)
            if hits or copied_sha != sha:
                raise RuntimeError(f"Copy verification failed: {relative}")
            by_path[relative] = {"path": relative, "bytes": target.stat().st_size, "sha256": sha, "source_sha256": sha, "source_bytes_observed": source.stat().st_size, "copy_method": "finalized-byte-copy"}
            updates[relative] = target
            print(f"Updated: {relative}", flush=True)
        for source in (Path(__file__).resolve().parent / "templates").iterdir():
            if source.is_file():
                sha, hits = digest_and_scan(source)
                if hits:
                    raise RuntimeError("Template credential-pattern match")
                target = stage / source.name
                safe_copy(source, target)
                by_path[source.name] = {"path": source.name, "bytes": target.stat().st_size, "sha256": sha, "source_sha256": sha, "copy_method": "generated-release-support"}
                updates[source.name] = target
        manifest["excluded"] = exclusions
        manifest["saved_input_targets"] = list(EXTRA_INPUTS)
        manifest["static_causal_seed_graph"] = list(MAIN_RUNTIME_FILES)
        manifest["not_in_scope"] = ["graphical_bayesian_inference_v2/", "function_prediction_runs/ except the two exact original inputs listed in saved_input_targets", "workspace-level scratch/artifact folders"]
    manifest["files"] = sorted(by_path.values(), key=lambda row: row["path"])
    manifest["finalized_utc"] = datetime.now(timezone.utc).isoformat()
    (stage / new_name).write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    updates[new_name] = stage / new_name
    removals = set(updates)
    if tier == "history":
        removals.update({"PORTABLE_MANIFEST.json", "PORTABLE_ORIGIN.json"})
    # Append mode rewrites the central directory. Existing compressed members
    # remain byte-identical; stale metadata local headers are unreferenced.
    with zipfile.ZipFile(archive_path, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as bundle:
        bundle.filelist = [entry for entry in bundle.filelist if entry.filename not in removals]
        for name in removals:
            bundle.NameToInfo.pop(name, None)
        for name, path in sorted(updates.items()):
            bundle.write(file_access(path), name)
    with zipfile.ZipFile(archive_path) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("Duplicate archive members")
        for name, path in updates.items():
            with bundle.open(name) as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            with path.open("rb") as stream:
                expected = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != expected:
                raise RuntimeError(f"Archive update verification failed: {name}")
        if tier == "history" and {"PORTABLE_MANIFEST.json", "PORTABLE_ORIGIN.json"}.intersection(names):
            raise RuntimeError("History archive would overwrite main metadata")
    with archive_path.open("rb") as stream:
        sha = hashlib.file_digest(stream, "sha256").hexdigest()
    archive_path.with_suffix(".zip.sha256").write_text(f"{sha}  {archive_path.name}\n", encoding="ascii")
    summary.update(sha256=sha, zip_bytes=archive_path.stat().st_size, file_count=len(by_path), uncompressed_bytes=sum(row["bytes"] for row in by_path.values()), finalized_utc=manifest["finalized_utc"])
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
