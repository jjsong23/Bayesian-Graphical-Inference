"""Offline packaging regression tests (no credentials, models, or services)."""
from __future__ import annotations
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

from build_release import digest_and_scan, file_access, safe_copy, sqlite_backup
import finalize_release

TEST_ROOT = Path(__file__).resolve().parents[2] / "outputs/portable_release/tests"

class PortableSupportTests(unittest.TestCase):
    def setUp(self):
        TEST_ROOT.mkdir(parents=True, exist_ok=True)
        # Windows managed runners can deny access to tempfile's mode-0700 dirs.
        # Inherit the already-authorized workspace ACL instead.
        self.root = TEST_ROOT / ("inherited_acl_" + uuid.uuid4().hex)
        self.root.mkdir()

    def tearDown(self):
        self.root.resolve().relative_to(TEST_ROOT.resolve())
        shutil.rmtree(file_access(self.root))

    def test_long_path_copy(self):
        source = self.root / "source.txt"
        source.write_text("scientific evidence", encoding="utf-8")
        destination = self.root / ("a" * 70) / ("b" * 70) / ("c" * 70) / "copied.txt"
        Path(file_access(destination.parent)).mkdir(parents=True)
        safe_copy(source, destination)
        self.assertEqual(source.read_bytes(), Path(file_access(destination)).read_bytes())

    def test_scan_does_not_report_values(self):
        source = self.root / "credential_fixture.txt"
        # Synthetic, nonfunctional fixture; assemble so the repository scanner
        # does not treat its own test source as a stored credential.
        source.write_text('api_key="' + "A" * 40 + '"', encoding="ascii")
        _, categories = digest_and_scan(source)
        self.assertEqual(["credential-assignment"], categories)
        self.assertNotIn("A" * 40, str(categories))

    def test_online_backup_observes_wal(self):
        source, target = self.root / "live.sqlite3", self.root / "snapshot.sqlite3"
        with contextlib.closing(sqlite3.connect(source)) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("CREATE TABLE evidence(id INTEGER PRIMARY KEY, value TEXT)")
            writer.execute("INSERT INTO evidence VALUES (1,'accepted')")
            writer.commit()
            sqlite_backup(source, target)
            with contextlib.closing(sqlite3.connect(target)) as snapshot:
                self.assertEqual([(1, "accepted")], snapshot.execute("SELECT * FROM evidence").fetchall())
            self.assertEqual(1, writer.execute("SELECT COUNT(*) FROM evidence").fetchone()[0])

    def test_history_finalization_keeps_payload_and_avoids_collision(self):
        output = self.root / "outputs/portable_release"
        stage = output / "test_history"
        stage.mkdir(parents=True)
        archive = output / "test_history.zip"
        manifest = {"tier": "history", "files": []}
        (stage / "PORTABLE_MANIFEST.json").write_text(json.dumps(manifest), encoding="utf-8")
        summary = {"tier": "history", "sha256": "", "zip_bytes": 0}
        archive.with_suffix(".summary.json").write_text(json.dumps(summary), encoding="utf-8")
        payload = b"scientific evidence" * 500
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("graphical_bayesian_inference/results/test.txt", payload)
            bundle.writestr("PORTABLE_MANIFEST.json", json.dumps(manifest))
            bundle.writestr("PORTABLE_ORIGIN.json", "{}")
        with patch.object(sys, "argv", ["finalize_release.py", str(archive), "--workspace", str(self.root)]), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, finalize_release.main())
        with zipfile.ZipFile(archive) as bundle:
            self.assertIsNone(bundle.testzip())
            self.assertNotIn("PORTABLE_MANIFEST.json", bundle.namelist())
            self.assertNotIn("PORTABLE_ORIGIN.json", bundle.namelist())
            self.assertIn("PORTABLE_HISTORY_MANIFEST.json", bundle.namelist())
            self.assertEqual(payload, bundle.read("graphical_bayesian_inference/results/test.txt"))

if __name__ == "__main__":
    unittest.main()
