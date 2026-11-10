"""Offline, synthetic-only checks for explicit copied-run relocation."""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import unittest
import uuid
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from path_picker.database import RunDatabase
from path_picker.grounding import stable_id
from path_picker.knowledge import SharedKnowledgeBase
from path_picker.portable import _mapped, remap_runs
from path_picker.rag import POLICY, RagPathRunner
from path_picker.workflow import PathPickerWorkflow
from tests.test_rag_workflow import CONTEXT, add_verified_claim


class PortableRunTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parents[1] / "runtime" / "test_portable"
        self.root = self.test_root / uuid.uuid4().hex
        self.original = self.root / "original"
        self.copy = self.root / "extracted"
        self.original_app = self.original / "path_picker_llm"
        self.copy_app = self.copy / "path_picker_llm"
        self.original_db = RunDatabase(self.original_app / "runs" / "pilot" / "path_picker.sqlite3")
        self.copy_db_path = self.copy_app / "runs" / "pilot" / "path_picker.sqlite3"
        self.original_kb = self.original_app / "runtime" / "shared_knowledge" / "evidence.sqlite3"
        self.copy_kb = self.copy_app / "runtime" / "shared_knowledge" / "evidence.sqlite3"
        self.original_input = self.original_app / "inputs" / "example.json"
        self.original_input.parent.mkdir(parents=True)
        self.original_input.write_text('{"synthetic": true}', encoding="utf-8")
        self.config = {"workflow_mode": "rag_path_first", "rag_kb_path": str(self.original_kb),
                       "rag_seed_run": str(self.original_db.path), "biological_context": CONTEXT,
                       "rag_max_rounds": 2}
        self.original_db.set_meta("configuration", self.config)
        self.original_db.set_meta("input_path", str(self.original_input))
        self.original_db.set_meta("status", "cancelled")
        self.original_db.set_meta("adaptive_biological_context", CONTEXT)
        self.original_db.set_meta("adaptive_context_fingerprint", stable_id("CTX", json.dumps(CONTEXT, sort_keys=True), "adaptive-v1"))
        self.original_db.set_meta("rag_policy_fingerprint", stable_id("RAG", POLICY, str(self.original_kb), json.dumps(CONTEXT, sort_keys=True)))
        add_verified_claim(self.original_db)
        self.original_db.event("synthetic", "Existing scientific provenance")
        SharedKnowledgeBase(self.original_kb).import_run(self.original_db.path)
        self.original_hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (self.original_db.path, self.original_kb, self.original_input)}
        self.backup(self.original_db.path, self.copy_db_path)
        self.backup(self.original_kb, self.copy_kb)
        copied_input = self.copy_app / "inputs" / "example.json"
        copied_input.parent.mkdir(parents=True)
        shutil.copy2(self.original_input, copied_input)
        self.network = patch("path_picker.client.urlopen", side_effect=AssertionError("Network forbidden in portability tests"))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        for path, digest in self.original_hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, str(path))
        self.root.resolve().relative_to(self.test_root.resolve())
        shutil.rmtree(self.root)

    @staticmethod
    def backup(source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)

    def meta(self):
        with closing(sqlite3.connect(self.copy_db_path)) as db:
            return dict(db.execute("SELECT key,value FROM metadata"))

    def scientific_rows(self):
        with closing(sqlite3.connect(self.copy_db_path)) as db:
            return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                    for table in ("nodes", "edges", "paths", "session_evidence", "sources", "claims", "tasks", "shortlist")}

    def relocate(self):
        return remap_runs(self.copy_app, self.original, self.copy)

    def copy_snapshot_to(self, destination):
        destination.resolve().relative_to(self.root.resolve())
        previous_db, previous_kb = self.copy_db_path, self.copy_kb
        self.copy = destination
        self.copy_app = self.copy / "path_picker_llm"
        self.copy_db_path = self.copy_app / "runs" / "pilot" / "path_picker.sqlite3"
        self.copy_kb = self.copy_app / "runtime" / "shared_knowledge" / "evidence.sqlite3"
        self.backup(previous_db, self.copy_db_path)
        self.backup(previous_kb, self.copy_kb)
        copied_input = self.copy_app / "inputs" / "example.json"
        copied_input.parent.mkdir(parents=True)
        shutil.copy2(self.original_input, copied_input)

    def test_same_policy_relocates_then_initializes_without_paid_call(self):
        before = self.scientific_rows()
        result = self.relocate()
        self.assertEqual(result["runs_changed"], 1)
        meta = self.meta()
        config = json.loads(meta["configuration"])
        self.assertEqual(config["rag_kb_path"], str(self.copy_kb))
        self.assertEqual(config["rag_seed_run"], str(self.copy_db_path))
        self.assertEqual(meta["input_path"], str(self.copy_app / "inputs" / "example.json"))
        self.assertEqual(meta["rag_policy_fingerprint"], stable_id("RAG", POLICY, str(self.copy_kb), json.dumps(CONTEXT, sort_keys=True)))
        self.assertEqual(json.loads((self.copy_db_path.parent / "configuration.json").read_text()), config)
        self.assertEqual(before, self.scientific_rows())
        history = json.loads(meta["portable_path_migrations"])
        self.assertEqual(len(history), 1)
        self.assertEqual(set(history[0]["fields"]), {"rag_kb_path", "rag_seed_run", "input_path"})
        with closing(sqlite3.connect(self.copy_db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE stage='portable_relocation'").fetchone()[0], 1)
        wf = PathPickerWorkflow(run_directory=self.copy_db_path.parent, input_path=meta["input_path"], configuration=config, api_key=None)
        self.assertIsNone(wf.client)
        RagPathRunner(wf).initialize()
        self.assertEqual(before, self.scientific_rows())

    def test_relocation_is_idempotent(self):
        self.relocate()
        before = self.meta()
        file_before = (self.copy_db_path.parent / "configuration.json").read_bytes()
        self.assertEqual(self.relocate()["runs_changed"], 0)
        self.assertEqual(self.meta(), before)
        self.assertEqual((self.copy_db_path.parent / "configuration.json").read_bytes(), file_before)

    def test_copy_nested_in_original_workspace_is_idempotent(self):
        self.copy_snapshot_to(self.original / "portable_bundle")
        self.assertEqual(self.relocate()["runs_changed"], 1)
        before = self.meta()
        config = json.loads(before["configuration"])
        self.assertEqual(config["rag_kb_path"], str(self.copy_kb))
        self.assertEqual(self.relocate()["runs_changed"], 0)
        self.assertEqual(self.meta(), before)

    def test_second_move_uses_last_migration_before_original_prefix(self):
        self.copy_snapshot_to(self.original / "portable_bundle")
        self.relocate()
        first_destination = self.copy
        before = self.scientific_rows()
        self.copy_snapshot_to(self.root / "second_extraction")
        self.assertEqual(self.relocate()["runs_changed"], 1)
        meta = self.meta()
        config = json.loads(meta["configuration"])
        self.assertEqual(config["rag_kb_path"], str(self.copy_kb))
        self.assertEqual(config["rag_seed_run"], str(self.copy_db_path))
        self.assertEqual(meta["input_path"], str(self.copy_app / "inputs" / "example.json"))
        history = json.loads(meta["portable_path_migrations"])
        self.assertEqual([entry["new_workspace"] for entry in history], [str(first_destination), str(self.copy)])
        self.assertEqual(meta["rag_policy_fingerprint"], stable_id("RAG", POLICY, str(self.copy_kb), json.dumps(CONTEXT, sort_keys=True)))
        self.assertEqual(self.relocate()["runs_changed"], 0)
        wf = PathPickerWorkflow(run_directory=self.copy_db_path.parent, input_path=meta["input_path"], configuration=config, api_key=None)
        RagPathRunner(wf).initialize()
        self.assertEqual(self.scientific_rows(), before)

    def test_already_destination_prefix_does_not_allow_traversal(self):
        self.relocate()
        RunDatabase(self.copy_db_path).set_meta("input_path", str(self.copy) + "/../outside.json")
        before = self.meta()
        with self.assertRaisesRegex(ValueError, "escapes its workspace"):
            self.relocate()
        self.assertEqual(self.meta(), before)

    def test_changed_context_does_not_override_original_policy_guard(self):
        database = RunDatabase(self.copy_db_path)
        database.set_meta("adaptive_biological_context", {**CONTEXT, "organism": "human"})
        before = self.meta()
        with self.assertRaisesRegex(ValueError, "different RAG policy/context"):
            self.relocate()
        self.assertEqual(self.meta(), before)
        self.assertFalse((self.copy_db_path.parent / "configuration.json").exists())

    def test_changed_policy_does_not_override_original_guard(self):
        RunDatabase(self.copy_db_path).set_meta("rag_policy_fingerprint", "RAG-different-policy")
        before = self.meta()
        with self.assertRaisesRegex(ValueError, "different RAG policy/context"):
            self.relocate()
        self.assertEqual(self.meta(), before)

    def test_configuration_context_guard_still_applies_after_relocation(self):
        self.relocate()
        meta = self.meta()
        config = json.loads(meta["configuration"])
        config["biological_context"] = {**CONTEXT, "organism": "human"}
        wf = PathPickerWorkflow(run_directory=self.copy_db_path.parent, input_path=meta["input_path"], configuration=config, api_key=None)
        with self.assertRaisesRegex(ValueError, "Biological context changed"):
            RagPathRunner(wf).initialize()

    def test_missing_shared_library_fails_without_metadata_rewrite(self):
        self.copy_kb.rename(self.copy_kb.with_suffix(".missing"))
        before = self.meta()
        with self.assertRaisesRegex(ValueError, "Bundled shared library missing"):
            self.relocate()
        self.assertEqual(self.meta(), before)

    def test_running_snapshot_is_rejected(self):
        RunDatabase(self.copy_db_path).set_meta("status", "running")
        before = self.meta()
        with self.assertRaisesRegex(ValueError, "marked running"):
            self.relocate()
        self.assertEqual(self.meta(), before)

    def test_workspace_escape_is_rejected_and_prefix_sibling_is_unmodified(self):
        config = {**self.config, "rag_kb_path": str(self.original) + "/../escape.sqlite3"}
        RunDatabase(self.copy_db_path).set_meta("configuration", config)
        before = self.meta()
        with self.assertRaisesRegex(ValueError, "escapes its workspace"):
            self.relocate()
        self.assertEqual(self.meta(), before)
        sibling = str(self.original) + "_sibling/file.sqlite3"
        self.assertEqual(_mapped(sibling, str(self.original), self.copy), sibling)
        with self.assertRaises(ValueError):
            remap_runs(self.original_app, self.original, self.copy)

    def test_original_workspace_is_never_rewritten(self):
        result = remap_runs(self.original_app, self.original, self.original)
        self.assertEqual(result["runs_changed"], 0)

    def test_absolute_and_drive_qualified_suffixes_cannot_escape(self):
        for suffix in ("/outside.sqlite3", "D:/outside.sqlite3", "D:outside.sqlite3"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                _mapped(str(self.original) + "/" + suffix, str(self.original), self.copy)


if __name__ == "__main__":
    unittest.main()
