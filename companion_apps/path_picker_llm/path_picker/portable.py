"""Explicit, audited relocation of copied Path Picker run configurations.

This never changes scientific claims, assessments, source provenance, or API
usage. Call only on an extracted, idle portable copy, before starting servers.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path, PurePosixPath

from .database import now
from .grounding import stable_id


def _mapped(value, old_workspace: str, new_workspace: Path):
    if not isinstance(value, str):
        return value
    normalized = value.replace("\\", "/").rstrip("/")
    old = str(old_workspace).replace("\\", "/").rstrip("/")
    if normalized.casefold() == old.casefold():
        return str(new_workspace)
    if normalized.casefold().startswith(old.casefold() + "/"):
        suffix = PurePosixPath(normalized[len(old) + 1:])
        if suffix.is_absolute() or ".." in suffix.parts or any(":" in part for part in suffix.parts):
            raise ValueError("Refusing a portable path that escapes its workspace")
        candidate = new_workspace.joinpath(*suffix.parts).resolve()
        try:
            candidate.relative_to(new_workspace.resolve())
        except ValueError as exc:
            raise ValueError("Refusing a portable path that escapes its workspace") from exc
        return str(candidate)
    return value


def remap_runs(application_root: str | Path, old_workspace: str | Path,
               new_workspace: str | Path) -> dict:
    app = Path(application_root).resolve()
    destination = Path(new_workspace).resolve()
    app.relative_to(destination)
    old = str(old_workspace)
    if not old.strip():
        raise ValueError("An explicit original workspace is required")
    # This command is for copied archives, never a migration of the live source.
    if str(destination).replace("\\", "/").casefold() == old.replace("\\", "/").rstrip("/").casefold():
        return {"runs_changed": 0, "changes": [], "note": "Already at original workspace; no rewrite performed"}
    changes = []
    for path in sorted((app / "runs").glob("*/path_picker.sqlite3")):
        path.resolve().relative_to(app)
        with closing(sqlite3.connect(path, timeout=5)) as db, db:
            meta = dict(db.execute("SELECT key,value FROM metadata"))
            if meta.get("status") == "running":
                raise ValueError(f"Run {path.parent.name} is marked running; the portable snapshot must first be marked stopped")
            config = json.loads(meta.get("configuration", "{}"))
            history = json.loads(meta.get("portable_path_migrations", "[]"))
            origins = [*[record["new_workspace"] for record in reversed(history)
                         if isinstance(record, dict) and isinstance(record.get("new_workspace"), str)], old]

            def relocate_value(value):
                # A copied workspace may itself be inside the source workspace.
                # Never prepend its destination twice on a repeated --relocate.
                if isinstance(value, str):
                    normalized = value.replace("\\", "/").rstrip("/").casefold()
                    current = str(destination).replace("\\", "/").rstrip("/").casefold()
                    if normalized == current or normalized.startswith(current + "/"):
                        _mapped(value, str(destination), destination)  # validate before no-op
                        return value
                for origin in origins:
                    mapped = _mapped(value, origin, destination)
                    if mapped != value:
                        return mapped
                return value

            updates = {}
            for key in ("rag_kb_path", "rag_seed_run"):
                if key in config:
                    mapped = relocate_value(config[key])
                    if mapped != config[key]:
                        updates[key] = {"old": config[key], "new": mapped}
                        config[key] = mapped
            input_path = relocate_value(meta.get("input_path", ""))
            if input_path != meta.get("input_path", ""):
                updates["input_path"] = {"old": meta.get("input_path"), "new": input_path}
            if not updates:
                continue
            if "rag_kb_path" in updates and meta.get("rag_policy_fingerprint"):
                from .rag import POLICY
                context = json.loads(meta["adaptive_biological_context"])
                old_kb = updates["rag_kb_path"]["old"]
                previous = stable_id("RAG", POLICY, old_kb, json.dumps(context, sort_keys=True))
                if previous != meta["rag_policy_fingerprint"]:
                    raise ValueError(f"Run {path.parent.name} has a different RAG policy/context; relocation will not override it")
                new_kb = Path(config["rag_kb_path"]).resolve()
                if not new_kb.is_file():
                    raise ValueError(f"Bundled shared library missing: {new_kb}")
                fingerprint = stable_id("RAG", POLICY, str(new_kb), json.dumps(context, sort_keys=True))
                db.execute("UPDATE metadata SET value=? WHERE key='rag_policy_fingerprint'", (fingerprint,))
            db.execute("UPDATE metadata SET value=? WHERE key='configuration'", (json.dumps(config, ensure_ascii=False),))
            db.execute("UPDATE metadata SET value=? WHERE key='input_path'", (input_path,))
            record = {"at": now(), "old_workspace": old, "new_workspace": str(destination), "fields": updates}
            history.append(record)
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('portable_path_migrations',?)", (json.dumps(history, ensure_ascii=False),))
            db.execute("INSERT INTO events(created_at,stage,level,message,details_json) VALUES (?,?,?,?,?)",
                       (now(), "portable_relocation", "info", "Relocated copied run configuration; scientific records and citations unchanged", json.dumps(record, ensure_ascii=False)))
        (path.parent / "configuration.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        changes.append({"run_id": path.parent.name, "fields": list(updates)})
    return {"runs_changed": len(changes), "changes": changes}
