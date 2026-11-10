"""Offline RAG integration: fake models, synthetic runs, and no network access."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from path_picker.adaptive import adaptive_progress
from path_picker.client import ResponsesClient
from path_picker.database import RunDatabase, now
from path_picker.ingest import canonical_edge
from path_picker.knowledge import CLAIM_FIELDS, SOURCE_FIELDS, knowledge_summary
from path_picker.rag import RagPathRunner
from path_picker.workflow import PathPickerWorkflow
from tests.test_adaptive import FakeClient, make_graph


CONTEXT = {"organism": "mouse", "cell_type": "kidney collecting duct", "stimulus": "acute dDAVP"}
QUOTE = "The measured phosphorylation response increased after the experimental stimulus."
URL = "https://example.org/primary-experiment"


def add_verified_claim(database, identifier="CLM-SEED", item_type="edge", item_id="N--S", *, accepted=1):
    source = {"source_id": "SRC-" + identifier, "url": URL + "/" + identifier,
              "title": "Synthetic primary experiment", "source_type": "primary_research", "pmid": "", "doi": "",
              "retracted": 0, "retrieved": 1, "http_status": 200, "text_hash": "hash-" + identifier,
              "retrieved_at": now(), "metadata_json": json.dumps({"verified_excerpts": [QUOTE]})}
    claim = {"claim_id": identifier, "item_type": item_type, "item_id": item_id, "subject": "S",
             "predicate": "changes", "object": "phosphorylation", "context_json": json.dumps({**CONTEXT,
                 "assay": "perturbation", "directionality": "S->N"}), "source_id": source["source_id"],
             "quotation": QUOTE, "quote_verified": 1, "entailment_verified": 1, "context_verified": 1,
             "accepted": accepted, "polarity": "positive", "failure_reason": None if accepted else "Rejected fixture"}
    with database.connect() as db:
        db.execute("INSERT OR REPLACE INTO sources (" + ",".join(SOURCE_FIELDS) + ") VALUES (" + ",".join("?" for _ in SOURCE_FIELDS) + ")", [source.get(k) for k in SOURCE_FIELDS])
        db.execute("INSERT OR REPLACE INTO claims (" + ",".join(CLAIM_FIELDS) + ") VALUES (" + ",".join("?" for _ in CLAIM_FIELDS) + ")", [claim.get(k) for k in CLAIM_FIELDS])
    return claim


def new_evidence(bundle, result, raw):
    edge = canonical_edge(*bundle["path_symbols"][:2])
    result["new_claims"] = [{"item_type": "edge", "item_id": edge, "claim": {
        "subject": "S", "predicate": "changes", "object": "phosphorylation", "polarity": "positive",
        "context": {**CONTEXT, "assay": "perturbation", "directionality": "S->N"}, "url": URL,
        "title": "Synthetic primary experiment", "quotation": QUOTE, "pmid": "", "doi": "", "source_type": "primary_research"}}]
    result["investigations"] = [{"item_type": "edge", "item_id": edge, "direction": bundle["path_symbols"][:2],
        "mechanism": "phosphorylation", "question": "Can S change N acutely?", "outcome": "evidence_found", "claim_indices": [0]}]
    raw["output"] = [{"type": "web_search_call", "action": {"sources": [{"url": URL}]}}]


class RagFakeClient(FakeClient):
    source_urls = staticmethod(ResponsesClient.source_urls)

    def __init__(self, mutate=None):
        super().__init__()
        self.mutate = mutate
        self.rag_requests = []

    def structured(self, **kwargs):
        if kwargs["schema_name"] != "path_picker_rag_review":
            return super().structured(**kwargs)
        self.calls[kwargs["schema_name"]] += 1
        bundle = json.loads(kwargs["prompt"].split("BUNDLE:\n", 1)[1])
        self.rag_requests.append({**kwargs, "bundle": bundle})
        symbols = bundle["path_symbols"]
        result = {"assessment": {"assessment": "uncertain", "mechanistic_coherence": .5,
            "reasoning": "Unverified pathway hypothesis.", "steps": [{"upstream": a, "downstream": b,
                "mechanism": "phosphorylation", "reasoning": "Uncertain", "claim_ids": [], "evidence_status": "uncertain"}
                for a, b in zip(symbols, symbols[1:])],
            "node_roles": [{"node": n, "mechanism": "scaffold", "reasoning": "Uncertain", "claim_ids": [], "evidence_status": "uncertain"}
                for n in symbols[1:-1]], "exclusion_proposals": []},
            "new_claims": [], "investigations": [], "needs_more_research": False, "remaining_gaps": ["Experiments needed"]}
        raw = {"output": []}
        if self.mutate:
            self.mutate(bundle, result, raw)
        return result, raw


class RagWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent.parent / "runtime" / "test_rag_workflow"
        self.root = self.test_root / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        self.network = patch("path_picker.client.urlopen", side_effect=AssertionError("Network forbidden in RAG test"))
        self.retrieval = patch("path_picker.workflow.retrieve", side_effect=AssertionError("Network retrieval forbidden in RAG test"))
        self.network.start()
        self.retrieval.start()

    def tearDown(self):
        self.network.stop()
        self.retrieval.stop()
        self.root.resolve().relative_to(self.test_root.resolve())
        # Windows indexers can briefly hold newly rendered report artifacts.
        for attempt in range(5):
            try:
                shutil.rmtree(self.root)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(.1 * (attempt + 1))

    def workflow(self, paths=None, **overrides):
        source = self.root / "agent_graph.json.gz"
        with gzip.open(source, "wt", encoding="utf-8") as handle:
            json.dump(make_graph(paths or [["S", "N", "T"]]), handle)
        configuration = {"workflow_mode": "rag_path_first", "rag_model": "gpt-6-sol", "rag_reasoning": "medium",
            "research_model": "fake", "verification_model": "fake", "reasoning_model": "fake", "synthesis_model": "fake",
            "rag_kb_path": str(self.root / "shared.sqlite3"), "rag_seed_run": "", "rag_max_rounds": 2,
            "rag_claim_limit": 40, "rag_context_chars": 24000, "rag_max_new_claims": 12, "rag_max_investigations": 8,
            "biological_context": CONTEXT, "max_deep_path_reviews": 2, **overrides}
        return PathPickerWorkflow(run_directory=self.root / "run", input_path=source, configuration=configuration, api_key=None)

    def install_grounder(self, wf, *, fail_once=False):
        calls = []
        def ground(kind, item, claim, search_keys):
            calls.append((kind, item))
            self.assertIn(URL, search_keys)
            if fail_once and len(calls) == 1:
                raise RuntimeError("Synthetic verification interruption")
            add_verified_claim(wf.database, "CLM-NEW", kind, item)
            return "CLM-NEW", None
        wf._ground_claim = ground
        return calls

    def test_free_inventory_imports_verified_seed_without_api_or_old_reviews(self):
        seed = RunDatabase(self.root / "seed.sqlite3")
        add_verified_claim(seed)
        add_verified_claim(seed, "CLM-REJECTED", accepted=0)
        seed.set_meta("old_path_assessment", "must-not-copy")
        before = hashlib.sha256(seed.path.read_bytes()).hexdigest()
        wf = self.workflow(rag_seed_run=str(seed.path))
        self.assertIsNone(wf.client)
        result = wf.run(inventory_only=True)
        self.assertEqual(result["status"], "inventory_complete")
        self.assertEqual(knowledge_summary(wf.config["rag_kb_path"])["accepted_claims"], 1)
        self.assertEqual(wf.database.get_meta("rag_seed_summary", decode=True)["accepted_imported"], 1)
        self.assertEqual(adaptive_progress(wf.database)["directly_reviewed"], 0)
        self.assertEqual(adaptive_progress(wf.database)["pending"], 1)
        self.assertIsNone(wf.database.get_meta("old_path_assessment"))
        self.assertEqual(before, hashlib.sha256(seed.path.read_bytes()).hexdigest())
        self.assertFalse((wf.run_directory / "api_usage.jsonl").exists())
        self.assertTrue((wf.run_directory / "rag_audit.json").exists())

    def test_full_workflow_covers_all_paths_audits_final_text_and_resumes_cached(self):
        wf = self.workflow([["S", "N", "A"], ["S", "N", "B"], ["S", "M", "C"]])
        wf.client = RagFakeClient()
        result = wf.run()
        self.assertEqual(wf.database.get_meta("status"), "complete")
        self.assertEqual(len(result["cards"]), 2)
        self.assertEqual(adaptive_progress(wf.database)["directly_reviewed"], 3)
        self.assertEqual(adaptive_progress(wf.database)["uncertain"], 3)
        self.assertEqual(adaptive_progress(wf.database)["pending"], 0)
        self.assertEqual(wf.client.calls["path_picker_rag_review"], 3)
        self.assertEqual(wf.client.calls["path_picker_path_review"], 2)
        self.assertEqual(wf.client.calls["path_picker_narrative_audit"], 3)
        self.assertTrue((wf.run_directory / "report.html").exists())
        self.assertEqual(len((wf.run_directory / "rag_reviews.jsonl").read_text().splitlines()), 3)
        before = dict(wf.client.calls)
        wf.run()
        self.assertEqual(dict(wf.client.calls), before)

    def test_seed_ids_are_exact_schema_enum_not_rejected_or_unrelated_ids(self):
        seed = RunDatabase(self.root / "seed.sqlite3")
        add_verified_claim(seed, "CLM-EXACT")
        add_verified_claim(seed, "CLM-UNRELATED", "node", "Z")
        add_verified_claim(seed, "CLM-BAD", accepted=0)
        wf = self.workflow(rag_seed_run=str(seed.path))
        wf.client = RagFakeClient()
        wf.run()
        request = wf.client.rag_requests[0]
        self.assertEqual(request["model"], "gpt-6-sol")
        self.assertEqual(request["reasoning_effort"], "medium")
        self.assertEqual([c["claim_id"] for c in request["bundle"]["accepted_claims"]], ["CLM-EXACT"])
        for section in ("steps", "node_roles", "exclusion_proposals"):
            refs = request["schema"]["properties"]["assessment"]["properties"][section]["items"]["properties"]["claim_ids"]
            self.assertEqual(refs["items"]["enum"], ["CLM-EXACT"])

    def test_web_claim_is_verified_before_followup_can_cite_it(self):
        wf = self.workflow()
        def mutate(bundle, result, raw):
            if bundle["research_budget"]["round"] == 1:
                new_evidence(bundle, result, raw)
            else:
                self.assertIn("CLM-NEW", {c["claim_id"] for c in bundle["accepted_claims"]})
                result["assessment"]["steps"][0].update(claim_ids=["CLM-NEW"], evidence_status="supported")
        wf.client = RagFakeClient(mutate)
        ground = self.install_grounder(wf)
        wf.run()
        self.assertEqual(len(ground), 1)
        self.assertEqual(wf.client.calls["path_picker_rag_review"], 2)
        self.assertEqual(wf.client.rag_requests[0]["bundle"]["accepted_claims"], [])
        self.assertEqual(knowledge_summary(wf.config["rag_kb_path"])["accepted_claims"], 1)
        with wf.database.connect() as db:
            review = json.loads(db.execute("SELECT result_json FROM tasks WHERE stage='path_screen'").fetchone()[0])
        self.assertEqual(review["rag_round"], 2)
        self.assertEqual(review["steps"][0]["claim_ids"], ["CLM-NEW"])

    def test_local_claim_conflict_cannot_be_replaced_by_shared_version_in_bundle(self):
        seed = RunDatabase(self.root / "seed.sqlite3")
        add_verified_claim(seed)
        wf = self.workflow(rag_seed_run=str(seed.path))
        wf.run(inventory_only=True)
        add_verified_claim(wf.database)
        with wf.database.connect() as db:
            db.execute("UPDATE claims SET object='CONFLICTING-LOCAL-CONTENT' WHERE claim_id='CLM-SEED'")
            path_id = db.execute("SELECT path_id FROM paths").fetchone()[0]
        bundle = wf._path_bundle(path_id)
        self.assertEqual(bundle["accepted_claims"], [])
        self.assertEqual(bundle["rag"]["passages"], [])
        with wf.database.connect() as db:
            self.assertEqual(db.execute("SELECT object FROM claims WHERE claim_id='CLM-SEED'").fetchone()[0], "CONFLICTING-LOCAL-CONTENT")
        self.assertEqual(knowledge_summary(wf.config["rag_kb_path"])["accepted_claims"], 1)

    def test_contextual_fact_is_retained_but_cannot_promote_a_step_to_supported(self):
        seed = RunDatabase(self.root / "seed.sqlite3")
        # The accepted atomic fact concerns phosphorylation, not the exact S->N mechanism.
        add_verified_claim(seed)
        wf = self.workflow(rag_seed_run=str(seed.path))
        def mutate(bundle, result, raw):
            self.assertEqual([c["claim_id"] for c in bundle["accepted_claims"]], ["CLM-SEED"])
            self.assertFalse(bundle["accepted_claims"][0]["status_support_eligible"])
            result["assessment"]["steps"][0].update(claim_ids=["CLM-SEED"], evidence_status="supported")
        wf.client = RagFakeClient(mutate)
        wf.run()
        with wf.database.connect() as db:
            review = json.loads(db.execute("SELECT review_json FROM path_assessments").fetchone()[0])
        self.assertEqual(review["steps"][0]["evidence_status"], "uncertain")
        self.assertEqual(review["steps"][0]["claim_ids"], ["CLM-SEED"])
        self.assertEqual(review["scope_adjustments"][0]["item_id"], "N--S")
        self.assertIn("scope_caveat", review["steps"][0])

    def test_verification_interruption_reuses_saved_search_on_resume(self):
        wf = self.workflow()
        wf.client = RagFakeClient(lambda b, r, raw: new_evidence(b, r, raw) if b["research_budget"]["round"] == 1 else None)
        ground = self.install_grounder(wf, fail_once=True)
        with self.assertRaisesRegex(RuntimeError, "verification interruption"):
            wf.run()
        self.assertEqual(wf.client.calls["path_picker_rag_review"], 1)
        self.assertEqual(adaptive_progress(wf.database)["pending"], 1)
        with wf.database.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM tasks WHERE stage='rag_round_response'").fetchone()[0], "complete")
        wf.run()
        self.assertEqual(wf.client.calls["path_picker_rag_review"], 2)
        self.assertEqual(len(ground), 2)
        self.assertEqual(wf.database.get_meta("status"), "complete")

    def test_invalid_id_is_saved_for_diagnostics_not_accepted_as_path_screen(self):
        wf = self.workflow()
        wf.client = RagFakeClient(lambda b, r, raw: r["assessment"]["steps"][0].update(claim_ids=["CLM-INVENTED"]))
        with self.assertRaisesRegex(RuntimeError, "out-of-bundle"):
            wf.run()
        self.assertEqual(adaptive_progress(wf.database)["pending"], 1)
        self.assertEqual(wf.database.get_meta("status"), "needs_attention")
        with wf.database.connect() as db:
            self.assertEqual(db.execute("SELECT status FROM tasks WHERE stage='path_screen'").fetchone()[0], "pending")
            self.assertEqual(db.execute("SELECT status FROM tasks WHERE stage='rag_round_response'").fetchone()[0], "invalid")
        self.assertEqual(len(list((wf.run_directory / "rejected_responses").glob("*.json"))), 1)

    def test_new_claim_without_assigned_investigation_is_rejected_before_grounding(self):
        wf = self.workflow()
        def mutate(bundle, result, raw):
            new_evidence(bundle, result, raw)
            result["investigations"] = []
        wf.client = RagFakeClient(mutate)
        ground = self.install_grounder(wf)
        with self.assertRaisesRegex(RuntimeError, "must belong to a scoped investigation"):
            wf.run()
        self.assertEqual(ground, [])
        self.assertEqual(adaptive_progress(wf.database)["directly_reviewed"], 0)

    def test_research_budget_disables_tools_and_claim_proposals_but_preserves_uncertainty(self):
        wf = self.workflow(max_research_items=0)
        wf.client = RagFakeClient()
        wf.run()
        request = wf.client.rag_requests[0]
        self.assertFalse(request["web_search"])
        self.assertEqual(request["schema"]["properties"]["new_claims"]["maxItems"], 0)
        self.assertEqual(request["schema"]["properties"]["investigations"]["maxItems"], 0)
        self.assertEqual(adaptive_progress(wf.database)["uncertain"], 1)

    def test_round_limit_is_bounded_and_marks_unresolved_gaps(self):
        wf = self.workflow(rag_max_rounds=1)
        wf.client = RagFakeClient(lambda b, r, raw: r.update(needs_more_research=True))
        wf.run()
        self.assertEqual(wf.client.calls["path_picker_rag_review"], 1)
        with wf.database.connect() as db:
            review = json.loads(db.execute("SELECT review_json FROM path_assessments").fetchone()[0])
        self.assertTrue(review["rag_budget_exhausted"])
        wf.config["rag_max_rounds"] = 3
        with self.assertRaisesRegex(ValueError, "between 1 and 2"):
            RagPathRunner(wf)

    def test_changing_existing_workflow_mode_is_rejected_without_rewriting_config(self):
        wf = self.workflow()
        wf.run(inventory_only=True)
        config_path = wf.run_directory / "configuration.json"
        before = config_path.read_bytes()
        with self.assertRaisesRegex(ValueError, "new run"):
            PathPickerWorkflow(run_directory=wf.run_directory, input_path=wf.input_path,
                configuration={**wf.config, "workflow_mode": "path_first"}, api_key=None)
        self.assertEqual(config_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
