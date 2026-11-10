"""SQLite persistence for resumable Path Picker runs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS nodes (
  node_id TEXT PRIMARY KEY, label TEXT NOT NULL, name TEXT, classes TEXT,
  is_source INTEGER NOT NULL, is_target INTEGER NOT NULL,
  path_count INTEGER NOT NULL, best_posterior REAL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS edges (
  edge_id TEXT PRIMARY KEY, node_a TEXT NOT NULL, node_b TEXT NOT NULL,
  path_count INTEGER NOT NULL, best_probability REAL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paths (
  path_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, query_id TEXT NOT NULL,
  start_symbol TEXT NOT NULL, target_symbol TEXT NOT NULL, rank INTEGER NOT NULL,
  hop_count INTEGER NOT NULL, primary_score REAL NOT NULL,
  path_symbols TEXT NOT NULL, payload_json TEXT NOT NULL,
  deterministic_status TEXT NOT NULL DEFAULT 'pending', deterministic_score REAL
);
CREATE INDEX IF NOT EXISTS idx_paths_query ON paths(query_id, rank);
CREATE TABLE IF NOT EXISTS session_evidence (
  session_id TEXT NOT NULL, item_type TEXT NOT NULL, item_id TEXT NOT NULL,
  payload_json TEXT NOT NULL, PRIMARY KEY(session_id,item_type,item_id)
);
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, stage TEXT NOT NULL, item_type TEXT NOT NULL,
  item_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT, result_json TEXT, updated_at TEXT NOT NULL,
  UNIQUE(stage, item_type, item_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_stage_status ON tasks(stage, status);
CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT, source_type TEXT,
  pmid TEXT, doi TEXT, retracted INTEGER NOT NULL DEFAULT 0,
  retrieved INTEGER NOT NULL DEFAULT 0, http_status INTEGER,
  text_hash TEXT, retrieved_at TEXT, metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS claims (
  claim_id TEXT PRIMARY KEY, item_type TEXT NOT NULL, item_id TEXT NOT NULL,
  subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
  context_json TEXT NOT NULL, source_id TEXT NOT NULL, quotation TEXT NOT NULL,
  quote_verified INTEGER NOT NULL DEFAULT 0, entailment_verified INTEGER NOT NULL DEFAULT 0,
  context_verified INTEGER NOT NULL DEFAULT 0, accepted INTEGER NOT NULL DEFAULT 0,
  polarity TEXT NOT NULL, failure_reason TEXT,
  FOREIGN KEY(source_id) REFERENCES sources(source_id)
);
CREATE INDEX IF NOT EXISTS idx_claims_item ON claims(item_type, item_id, accepted);
CREATE TABLE IF NOT EXISTS shortlist (
  position INTEGER PRIMARY KEY, path_id TEXT NOT NULL, score REAL NOT NULL,
  card_json TEXT NOT NULL, FOREIGN KEY(path_id) REFERENCES paths(path_id)
);
CREATE TABLE IF NOT EXISTS events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
  stage TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL, details_json TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RunDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=60)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def set_meta(self, key: str, value: Any) -> None:
        encoded = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)", (key, encoded))

    def get_meta(self, key: str, default: Any = None, *, decode: bool = False) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        if row is None:
            return default
        if decode:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return row[0]
        return row[0]

    def event(self, stage: str, message: str, *, level: str = "info", details: Any = None) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(created_at,stage,level,message,details_json) VALUES (?,?,?,?,?)",
                (now(), stage, level, message, json.dumps(details or {}, ensure_ascii=False)),
            )

    def counts(self) -> dict[str, int]:
        tables = ("nodes", "edges", "paths", "claims", "sources", "shortlist")
        with self.connect() as db:
            return {name: int(db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]) for name in tables}

    def progress(self) -> dict[str, Any]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT stage,status,COUNT(*) AS n FROM tasks GROUP BY stage,status ORDER BY stage,status"
            ).fetchall()
            events = [dict(row) for row in db.execute("SELECT * FROM events ORDER BY event_id DESC LIMIT 40")]
        stages: dict[str, dict[str, int]] = {}
        for row in rows:
            stages.setdefault(row["stage"], {})[row["status"]] = row["n"]
        return {"stages": stages, "events": events, "counts": self.counts()}

