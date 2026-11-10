"""Offline shared-evidence tests. No model, network, or existing-run mutations."""

from __future__ import annotations

import hashlib
import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from path_picker.database import RunDatabase, now
from path_picker.knowledge import (CLAIM_FIELDS, SOURCE_FIELDS, SharedKnowledgeBase,
                                   coverage_key, knowledge_summary)
from path_picker import knowledge


CONTEXT = {"organism": "mouse", "cell_type": "kidney collecting duct", "stimulus": "acute dDAVP",
           "assay": "perturbation and rescue", "directionality": "Prkaca->App"}
QUOTE = "The measured phosphorylation response increased after the experimental stimulus."


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent.parent / "runtime" / "test_knowledge"
        self.root = self.test_root / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.origin = RunDatabase(self.root / "origin.sqlite3")
        self.library_path = self.root / "knowledge.sqlite3"
        self.kb = SharedKnowledgeBase(self.library_path)

    def tearDown(self):
        self.root.resolve().relative_to(self.test_root.resolve())
        shutil.rmtree(self.root)

    def add(self, identifier="CLM-ONE", item_type="node", item_id="App", *,
            claim_changes=None, source_changes=None, database=None):
        target = database or self.origin
        source = {"source_id": "SRC-" + identifier, "url": "https://example.org/" + identifier,
                  "title": "Primary experiment", "source_type": "publisher_or_other", "pmid": "", "doi": "",
                  "retracted": 0, "retrieved": 1, "http_status": 200, "text_hash": "hash-" + identifier,
                  "retrieved_at": now(), "metadata_json": json.dumps({"verified_excerpts": ["Intro. " + QUOTE + " Surrounding experimental details.",
                                                                                          "This unrelated passage contains no claimed quotation."],
                                                                      "api_key": "DO-NOT-COPY-THIS-KEY"})}
        claim = {"claim_id": identifier, "item_type": item_type, "item_id": item_id, "subject": "App",
                 "predicate": "changes", "object": "phosphorylation after stimulation", "context_json": json.dumps(CONTEXT),
                 "source_id": source["source_id"], "quotation": QUOTE, "quote_verified": 1, "entailment_verified": 1,
                 "context_verified": 1, "accepted": 1, "polarity": "positive", "failure_reason": None}
        claim.update(claim_changes or {})
        source.update(source_changes or {})
        with target.connect() as db:
            db.execute("INSERT OR REPLACE INTO sources (" + ",".join(SOURCE_FIELDS) + ") VALUES (" + ",".join("?" for _ in SOURCE_FIELDS) + ")",
                       [source.get(key) for key in SOURCE_FIELDS])
            db.execute("INSERT OR REPLACE INTO claims (" + ",".join(CLAIM_FIELDS) + ") VALUES (" + ",".join("?" for _ in CLAIM_FIELDS) + ")",
                       [claim.get(key) for key in CLAIM_FIELDS])
        return claim, source

    def lookup(self, **kwargs):
        params = {"symbols": ["Prkaca", "App"], "edge_ids": ["App--Prkaca"], "context": CONTEXT,
                  "query": "phosphorylation stimulation", "max_chars": 24000}
        params.update(kwargs)
        return self.kb.retrieve(**params)

    def test_import_is_readonly_idempotent_and_separates_rejected_claims(self):
        self.add()
        self.add("CLM-BAD", claim_changes={"accepted": 0, "failure_reason": "Quotation not found"})
        with self.origin.connect() as db:
            db.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?)", ("node_research:node:App", "node_research", "node", "App", "complete", 1, None,
                json.dumps({"verdict": "uncertain", "accepted_claim_ids": ["CLM-ONE"], "reasoning": "UNVERIFIED-PROSE-MUST-NOT-BECOME-A-FACT",
                            "unresolved_questions": ["Does the effect occur acutely?"]}), now()))
        before = hashlib.sha256(self.origin.path.read_bytes()).hexdigest()
        imported = self.kb.import_run(self.origin.path)
        first_revision = self.kb.summary()["revision"]
        again = self.kb.import_run(self.origin.path)
        self.assertEqual(before, hashlib.sha256(self.origin.path.read_bytes()).hexdigest())
        self.assertEqual(imported["accepted_imported"], 1)
        self.assertEqual(imported["rejected_imported"], 1)
        self.assertEqual(again["accepted_imported"], 0)
        self.assertEqual(again["rejected_imported"], 0)
        self.assertEqual(first_revision, self.kb.summary()["revision"])
        result = self.lookup()
        self.assertEqual([c["claim_id"] for c in result["accepted_claims"]], ["CLM-ONE"])
        self.assertFalse(result["coverage"][0]["reusable"])
        raw = self.library_path.read_bytes()
        self.assertNotIn(b"DO-NOT-COPY-THIS-KEY", raw)
        self.assertNotIn(b"UNVERIFIED-PROSE-MUST-NOT-BECOME-A-FACT", raw)
        self.assertEqual(self.kb.summary()["rejected_claims"], 1)

    def test_invalid_verification_source_or_context_flags_never_import_as_facts(self):
        self.add("CLM-GOOD")
        cases = [("ACCEPT", {"accepted": 0}, {}), ("QUOTE", {"quote_verified": 0}, {}),
                 ("ENTAIL", {"entailment_verified": 0}, {}), ("CTXFLAG", {"context_verified": 0}, {}),
                 ("CTX", {"context_json": "{}"}, {}), ("MISSINGQUOTE", {"quotation": "short"}, {}),
                 ("RETRACT", {}, {"retracted": 1}), ("FETCH", {}, {"retrieved": 0}),
                 ("HTTP", {}, {"http_status": 404}), ("FAILURE", {"failure_reason": "failed"}, {}),
                 ("CREDENTIAL", {}, {"url": "https://example.org/paper?api_key=SECRET-IN-URL"})]
        for name, claim, source in cases:
            self.add("CLM-" + name, claim_changes=claim, source_changes=source)
        imported = self.kb.import_run(self.origin.path)
        self.assertEqual(imported["accepted_imported"], 1)
        self.assertEqual(imported["rejected_imported"], len(cases))
        self.assertEqual(len(self.lookup()["accepted_claims"]), 1)
        self.assertNotIn(b"SECRET-IN-URL", self.library_path.read_bytes())

    def test_exact_node_and_pair_gating_prevents_substring_or_unrelated_matches(self):
        self.add("CLM-NODE", item_id="App")
        self.add("CLM-SUBSTRING", item_id="Application")
        self.add("CLM-PAIR", "edge", "Prkaca--App")
        self.add("CLM-WRONGPAIR", "edge", "App--Ctnnb1")
        self.kb.import_run(self.origin.path)
        ids = {c["claim_id"] for c in self.lookup()["accepted_claims"]}
        self.assertEqual(ids, {"CLM-NODE", "CLM-PAIR"})
        self.assertEqual(self.lookup(symbols=[], edge_ids=[])["accepted_claims"], [])

    def test_other_context_and_opposite_direction_are_annotated_not_transferred(self):
        other = {**CONTEXT, "organism": "human", "directionality": "App->Prkaca; not evidence for the proposed direction"}
        self.add("CLM-OPPOSITE", "edge", "App--Prkaca", claim_changes={"context_json": json.dumps(other)})
        self.kb.import_run(self.origin.path)
        claim = self.lookup()["accepted_claims"][0]
        self.assertEqual(claim["context_match"]["status"], "mismatch")
        self.assertEqual(claim["direction_match"]["status"], "opposite_direction_mentioned")
        self.assertEqual(claim["polarity"], "positive")

    def test_verified_excerpts_are_bound_to_claim_quote_and_old_quotes_survive(self):
        self.add("CLM-EXCERPT")
        self.add("CLM-OLD", source_changes={"metadata_json": "{}"})
        self.kb.import_run(self.origin.path)
        result = self.lookup()
        self.assertEqual(len([p for p in result["passages"] if p["kind"] == "verified_quotation"]), 2)
        excerpts = [p for p in result["passages"] if p["kind"] == "verified_source_excerpt"]
        self.assertEqual(len(excerpts), 1)
        self.assertEqual(excerpts[0]["claim_id"], "CLM-EXCERPT")
        self.assertIn(QUOTE, excerpts[0]["text"])
        self.assertNotIn("unrelated passage", json.dumps(result))

    def test_hydrate_is_idempotent_and_does_not_overwrite_local_rejections(self):
        self.add("CLM-KEEP")
        self.add("CLM-COPY")
        self.kb.import_run(self.origin.path)
        destination = RunDatabase(self.root / "destination.sqlite3")
        self.add("CLM-KEEP", database=destination, claim_changes={"accepted": 0, "failure_reason": "Local contradiction"})
        self.assertEqual(self.kb.hydrate_run(["CLM-KEEP", "CLM-COPY"], destination), 1)
        self.assertEqual(self.kb.hydrate_run(["CLM-KEEP", "CLM-COPY"], destination), 0)
        with destination.connect() as db:
            self.assertEqual(db.execute("SELECT accepted FROM claims WHERE claim_id='CLM-KEEP'").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT accepted FROM claims WHERE claim_id='CLM-COPY'").fetchone()[0], 1)

    def test_hydrate_does_not_overwrite_retracted_or_changed_source(self):
        self.add("CLM-RETRACT")
        self.add("CLM-CHANGED")
        self.kb.import_run(self.origin.path)
        destination = RunDatabase(self.root / "destination.sqlite3")
        self.add("CLM-RETRACT", database=destination, source_changes={"retracted": 1})
        self.add("CLM-CHANGED", database=destination, source_changes={"text_hash": "different-content"})
        with destination.connect() as db:
            db.execute("DELETE FROM claims")  # Only this test's two synthetic rows.
        self.assertEqual(self.kb.hydrate_run(["CLM-RETRACT", "CLM-CHANGED"], destination), 0)

    def test_conflicting_same_id_is_quarantined_without_overwrite(self):
        self.add()
        self.kb.import_run(self.origin.path)
        with self.origin.connect() as db:
            db.execute("UPDATE claims SET predicate='a different incompatible claim'")
        self.kb.import_run(self.origin.path)
        self.assertEqual(self.lookup()["accepted_claims"], [])
        self.assertEqual(self.kb.summary()["quarantined_claims"], 1)
        self.assertEqual(self.kb.summary()["rejected_claims"], 1)

    def test_first_time_fetch_failure_does_not_ban_later_verified_claims(self):
        self.add("CLM-FAILED", claim_changes={"accepted": 0}, source_changes={"retrieved": 0})
        self.kb.import_run(self.origin.path)
        self.assertEqual(self.kb.summary()["revoked_sources"], 0)
        self.add("CLM-FAILED")
        self.kb.import_run(self.origin.path)
        self.assertEqual([c["claim_id"] for c in self.lookup()["accepted_claims"]], ["CLM-FAILED"])
        self.assertEqual(self.kb.summary()["quarantined_claims"], 0)
        self.assertEqual(self.kb.summary()["rejected_claims"], 1)

    def test_later_rejection_quarantines_previously_accepted_claim(self):
        self.add()
        self.kb.import_run(self.origin.path)
        with self.origin.connect() as db:
            db.execute("UPDATE claims SET accepted=0,failure_reason='Later verification rejected the claim'")
        outcome = self.kb.import_run(self.origin.path)
        self.assertEqual(outcome["quarantined"], 1)
        self.assertEqual(self.lookup()["accepted_claims"], [])
        destination = RunDatabase(self.root / "destination.sqlite3")
        self.assertEqual(self.kb.hydrate_run(["CLM-ONE"], destination), 0)

    def test_source_revocation_from_other_run_quarantines_absent_claim_ids(self):
        claim, source = self.add("CLM-FIRST")
        self.kb.import_run(self.origin.path)
        other = RunDatabase(self.root / "other.sqlite3")
        # The second run does not contain CLM-FIRST, only the same now-retracted
        # source and a different claim. Its source observation must still revoke it.
        self.add("CLM-SECOND", database=other,
                 claim_changes={"source_id": source["source_id"], "accepted": 0},
                 source_changes={**source, "retracted": 1})
        outcome = self.kb.import_run(other.path)
        self.assertEqual(outcome["quarantined"], 1)
        self.assertEqual(self.lookup()["accepted_claims"], [])
        self.assertEqual(self.kb.summary()["quarantined_claims"], 1)
        # A stale earlier accepted run cannot silently restore a revoked fact.
        self.kb.import_run(self.origin.path)
        self.assertEqual(self.lookup()["accepted_claims"], [])
        # A previously unseen claim ID from a stale accepted source snapshot must
        # also remain blocked, not merely the original quarantined claim ID.
        stale = RunDatabase(self.root / "stale.sqlite3")
        self.add("CLM-STALE", database=stale, claim_changes={"source_id": source["source_id"]}, source_changes=source)
        self.kb.import_run(stale.path)
        self.assertEqual(self.lookup()["accepted_claims"], [])
        self.assertEqual(self.kb.summary()["revoked_sources"], 1)

    def test_coverage_keys_include_context_orientation_mechanism_question_and_policy(self):
        args = {"context": CONTEXT, "item_type": "edge", "item_id": "App--Prkaca",
                "ordered_edge": ["Prkaca", "App"], "mechanism": "phosphorylation", "question": "Direct substrate?", "policy_version": "v1"}
        key = coverage_key(**args)
        for field, value in (("context", {**CONTEXT, "organism": "human"}), ("ordered_edge", ["App", "Prkaca"]),
                             ("mechanism", "scaffold"), ("question", "Colocalization?"), ("policy_version", "v2")):
            self.assertNotEqual(key, coverage_key(**{**args, field: value}))
        self.kb.record_coverage(key, {**args, "api_key": "SENSITIVE", "nested": {"authorization": "PRIVATE", "safe": True}})
        value = self.kb.get_coverage(key)
        self.assertNotIn("api_key", value)
        self.assertEqual(value["nested"], {"safe": True})
        revision = self.kb.summary()["revision"]
        self.kb.record_coverage(key, value)
        self.assertEqual(revision, self.kb.summary()["revision"])
        self.assertIsNone(self.kb.get_coverage("not-the-same-scope"))

    def test_numpy_lsa_is_real_deterministic_and_fallback_is_explicit(self):
        descriptions = ["phosphorylation kinase substrate catalytic", "kinase substrate binding phosphorylation",
                        "trafficking vesicle membrane localization", "localization membrane scaffold binding",
                        "calcium channel transport membrane"]
        for index, text in enumerate(descriptions):
            self.add("CLM-SEM" + str(index), claim_changes={"object": text})
        self.kb.import_run(self.origin.path)
        first = self.lookup(query="kinase phosphorylation")
        second = self.lookup(query="kinase phosphorylation")
        self.assertEqual(first["accepted_claims"], second["accepted_claims"])
        if knowledge.np is not None:
            self.assertEqual(first["stats"]["method"], "tfidf_lsa")
            self.assertTrue(first["stats"]["semantic_applied"])
            self.assertGreater(first["stats"]["lsa_dimensions"], 0)
        with patch.object(knowledge, "np", None):
            fallback = self.lookup(query="kinase phosphorylation")
        self.assertEqual(fallback["stats"]["method"], "lexical_tfidf")
        self.assertIn("NumPy", fallback["stats"]["warning"])
        self.assertFalse(fallback["stats"]["semantic_applied"])

    def test_budget_and_count_limits_keep_claim_passage_links_intact(self):
        for i in range(8):
            self.add("CLM-LIMIT" + str(i))
        self.kb.import_run(self.origin.path)
        count_limited = self.lookup(limit=2)
        self.assertEqual(len(count_limited["accepted_claims"]), 2)
        budget_limited = self.lookup(max_chars=5000)
        self.assertTrue(budget_limited["stats"]["truncated"])
        self.assertLessEqual(len(json.dumps(budget_limited, ensure_ascii=False, separators=(",", ":"))), 5000)
        allowed = {c["claim_id"] for c in budget_limited["accepted_claims"]}
        self.assertTrue(all(p["claim_id"] in allowed for p in budget_limited["passages"]))

    def test_missing_safe_summary_does_not_create_a_library(self):
        missing = self.root / "uncreated" / "knowledge.sqlite3"
        self.assertFalse(knowledge_summary(missing)["exists"])
        self.assertFalse(missing.parent.exists())
        self.assertEqual(self.kb.summary()["accepted_claims"], 0)


if __name__ == "__main__":
    unittest.main()
