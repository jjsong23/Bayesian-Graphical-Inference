from __future__ import annotations

import gzip
import json
import shutil
import uuid
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from path_picker.adaptive import AdaptivePathRunner, adaptive_progress, export_adaptive_audit, list_exclusions, revoke_exclusion, rule_matches_path, target_fair_order
from path_picker.ingest import canonical_edge, import_agent_graphs
from path_picker.workflow import PathPickerWorkflow


def make_graph(paths: list[list[str]], queries: list[str] | None = None) -> dict:
    nodes = sorted({node for path in paths for node in path})
    edges = {canonical_edge(a, b): (a, b) for path in paths for a, b in zip(path, path[1:])}
    return {"run_id": "test", "source_nodes": [paths[0][0]], "target_nodes": sorted({p[-1] for p in paths}),
        "nodes": [{"id": node, "label": node, "posterior_probability": .9, "evidence_ledger": {"evidence": []}} for node in nodes],
        "edges": [{"id": identifier, "node_a": a, "node_b": b, "edge_probability": .8, "evidence_ledger": {"evidence": []}}
                  for identifier, (a, b) in edges.items()],
        "ranked_paths": [{"rank": index+1, "query_id": queries[index] if queries else f"{path[0]}->{path[-1]}",
                          "start_symbol": path[0], "target_symbol": path[-1], "hop_count": len(path)-1,
                          "primary_path_score": .7, "path_symbols": " -> ".join(path)} for index, path in enumerate(paths)]}


class FakeClient:
    def __init__(self, request_node: bool = False, assessment: str = "uncertain") -> None:
        self.calls = Counter()
        self.request_node = request_node
        self.assessment = assessment
        self.screened: list[str] = []

    def structured(self, **kwargs):
        name = kwargs["schema_name"]
        self.calls[name] += 1
        if name == "path_picker_adaptive_screen":
            bundle = json.loads(kwargs["prompt"].split("BUNDLE:\n", 1)[1])
            self.screened.append(bundle["path_id"])
            symbols = bundle["path_symbols"]
            return {"assessment": self.assessment, "mechanistic_coherence": .5, "reasoning": "Unresolved hypothesis, not negative.",
                "steps": [{"upstream": a, "downstream": b, "mechanism": "binding", "reasoning": "Hypothesis", "claim_ids": []} for a, b in zip(symbols, symbols[1:])],
                "node_roles": [{"node": node, "mechanism": "scaffold", "reasoning": "Hypothesis"} for node in symbols[1:-1]],
                "research_requests": ([{"kind": "node", "item_id": symbols[1], "question": "Can it relay acute signal?"}] if self.request_node else []),
                "exclusion_proposals": []}, {}
        if name == "path_picker_exclusion_verification":
            return {key: True for key in ("explicit_negative", "context_matches", "direction_matches", "mechanism_matches", "alternative_mechanisms_considered", "exclusion_justified")} | {"explanation": "Fixture only"}, {}
        if name == "path_picker_path_review":
            return {"mechanistic_coherence": .5, "evidence_strength": .1, "novelty": .5, "experimental_tractability": .5,
                    "weakest_step": "All remain unresolved", "reasoning": "Testable proposal only", "supporting_claim_ids": [],
                    "contradicting_claim_ids": [], "recommended_experiment": "Perturb intermediate", "predicted_result": "Hypothesized change", "falsifying_result": "No change"}, {}
        if name == "path_picker_shortlist":
            candidates = json.loads(kwargs["prompt"].split("CANDIDATES:\n", 1)[1].split("\n\nACCEPTED CLAIM LEDGER:", 1)[0])
            sentence = "This computationally proposed route remains an unverified hypothesis that requires independent experiments to distinguish its predicted signaling mechanism from plausible alternative pathways and missing biological context [INFERENCE]."
            cards = [{"path_id": candidate["path_id"], "title": "A testable pathway", "abstract": " ".join([sentence]*4),
                      "selection_reason": "A proposal", "weakest_step": "Unknown mechanism", "uncertainties": ["No accepted source"],
                      "recommended_experiment": "Test the hypothesis", "predicted_result": "Proposed effect", "falsifying_result": "No predicted effect", "supporting_claim_ids": []} for candidate in candidates]
            return {"cards": cards, "network_synthesis": sentence}, {}
        if name == "path_picker_narrative_audit":
            sentences = json.loads(kwargs["prompt"].split("NUMBERED SENTENCES:\n", 1)[1].split("\nCARD:", 1)[0])
            return {"sentence_checks": [{"sentence_index": item["index"], "supported_by_cited_evidence": False,
                                          "clearly_labeled_defensible_inference": True, "claim_ids": [], "explanation": "A labeled proposal"} for item in sentences],
                    "other_card_fields_grounded_or_explicit_proposals": True, "issues": []}, {}
        raise AssertionError("Unexpected paid-call substitute: " + name)


class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent.parent / "runtime" / "test_adaptive"
        self.root = self.test_root / uuid.uuid4().hex
        self.root.mkdir(parents=True)

    def tearDown(self):
        self.root.resolve().relative_to(self.test_root.resolve())
        shutil.rmtree(self.root)

    def workflow(self, paths, queries=None, **config):
        path = self.root / "agent_graph.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump(make_graph(paths, queries), handle)
        wf = PathPickerWorkflow(run_directory=self.root / "run", input_path=path, api_key=None,
            configuration={"workflow_mode": "path_first", "research_model": "fake", "verification_model": "fake",
                           "reasoning_model": "fake", "synthesis_model": "fake", "max_deep_path_reviews": 2, **config})
        import_agent_graphs(path, wf.database)
        wf.client = FakeClient()
        return wf

    @staticmethod
    def fake_research(wf):
        calls = Counter()
        def research(kind, items, prompt, expected):
            for identifier in expected:
                calls[(kind, identifier)] += 1
                wf._set_task(kind + "_research", identifier, "complete", result={"verdict": "uncertain", "accepted_claim_ids": [], "unresolved_questions": ["No verified source"]})
        wf._research_batch = research
        return calls

    def test_every_imported_path_screened_even_with_small_deep_cap_and_resume_cached(self):
        paths = [["S", f"N{i}", "T"] for i in range(205)]
        wf = self.workflow(paths, path_round_size=23)
        research = self.fake_research(wf)
        wf._deep_review_paths = lambda: None
        runner = AdaptivePathRunner(wf)
        runner.run()
        progress = adaptive_progress(wf.database)
        self.assertEqual(progress["directly_reviewed"], 205)
        self.assertEqual(progress["pending"], 0)
        self.assertEqual(progress["uncertain"], 205)
        self.assertEqual(progress["excluded_by_rule"], 0)
        self.assertEqual(wf.client.calls["path_picker_adaptive_screen"], 205)
        self.assertEqual(len(wf._candidate_path_ids()), 2)
        runner.run()
        self.assertEqual(wf.client.calls["path_picker_adaptive_screen"], 205)
        self.assertTrue(all(count == 1 for count in research.values()))
        coverage = json.loads((wf.run_directory / "path_coverage.json").read_text())
        self.assertEqual(len(coverage["paths"]), 205)
        with wf.database.connect() as db:
            self.assertEqual({row[0] for row in db.execute("SELECT primary_score FROM paths")}, {.7})

    def test_target_fair_order(self):
        rows = [{"path_id": "a1", "query_id": "A"}, {"path_id": "a2", "query_id": "A"},
                {"path_id": "b1", "query_id": "B"}, {"path_id": "b2", "query_id": "B"}]
        self.assertEqual(target_fair_order(rows), ["a1", "b1", "a2", "b2"])

    def test_research_cached_and_refinement_bounded(self):
        wf = self.workflow([["S", "N", "A"], ["S", "N", "B"]])
        wf.client = FakeClient(request_node=True)
        calls = self.fake_research(wf)
        wf._deep_review_paths = lambda: None
        AdaptivePathRunner(wf).run()
        self.assertEqual(calls[("node", "N")], 1)
        self.assertEqual(wf.client.calls["path_picker_adaptive_screen"], 3)

    def test_unknown_and_reverse_support_never_make_rule(self):
        wf = self.workflow([["S", "N", "T"]])
        runner = AdaptivePathRunner(wf)
        runner.initialize()
        with wf.database.connect() as db:
            row = db.execute("SELECT * FROM paths").fetchone()
        wf._accepted_claims = lambda ids: [{"claim_id": "CLM-1", "polarity": "positive"}]
        runner._consider_rule(row, {"scope": "directed_edge", "nodes": ["S", "N"], "mechanism": "binding",
                                    "claim_ids": ["CLM-1"], "rationale": "Reverse support is not contradiction"})
        self.assertEqual(list_exclusions(wf.database)[0]["status"], "rejected")
        self.assertEqual(wf.client.calls["path_picker_exclusion_verification"], 0)

    def test_rule_scope_direction_mechanism_and_role(self):
        rule = {"scope": "directed_edge", "target": {"nodes": ["A", "B"], "mechanism": "binding", "query_id": "q", "origin_path_id": "p"}}
        path = {"path_id": "p", "query_id": "q", "path_symbols": "S -> A -> B -> T"}
        review = {"steps": [{"upstream": "A", "downstream": "B", "mechanism": "binding"}]}
        self.assertTrue(rule_matches_path(rule, path, review))
        self.assertFalse(rule_matches_path(rule, {**path, "path_symbols": "S -> B -> A -> T"}, review))
        self.assertFalse(rule_matches_path(rule, path, None))
        self.assertFalse(rule_matches_path(rule, {**path, "query_id": "different context"}, review))
        self.assertFalse(rule_matches_path(rule, path, {"steps": [{"upstream": "A", "downstream": "B", "mechanism": "scaffolding"}]}))
        role_rule = {"scope": "intermediate", "target": {**rule["target"], "nodes": ["A"], "mechanism": "scaffold"}}
        self.assertFalse(rule_matches_path(role_rule, {**path, "path_symbols": "S -> A"}, {"node_roles": [{"node": "A", "mechanism": "scaffold"}]}))

    def test_high_impact_held_and_revocation_restores(self):
        wf = self.workflow([["S", "N", "T"]], exclusion_max_fraction=.1)
        runner = AdaptivePathRunner(wf)
        runner.initialize()
        runner._screen(next(iter(wf._pending("path_screen", 1))))
        with wf.database.connect() as db:
            row = db.execute("SELECT * FROM paths").fetchone()
        wf._accepted_claims = lambda ids: [{"claim_id": "CLM-1", "polarity": "negative", "context": {}}]
        proposal = {"scope": "path", "nodes": ["S", "N", "T"], "mechanism": "binding", "claim_ids": ["CLM-1"], "rationale": "Mock negative"}
        runner._consider_rule(row, proposal)
        rule = list_exclusions(wf.database)[0]
        self.assertEqual(rule["status"], "held_for_review")
        runner._apply_active_rules()
        self.assertEqual(adaptive_progress(wf.database)["excluded_by_rule"], 0)
        # Fixture activates a verified rule only to exercise reversibility.
        with wf.database.connect() as db:
            db.execute("UPDATE exclusion_rules SET status='active' WHERE rule_id=?", (rule["rule_id"],))
        runner._apply_active_rules()
        self.assertEqual(adaptive_progress(wf.database)["excluded_by_rule"], 1)
        revoke_exclusion(wf.database, rule["rule_id"])
        self.assertEqual(adaptive_progress(wf.database)["excluded_by_rule"], 0)
        self.assertEqual(adaptive_progress(wf.database)["uncertain"], 1)
        self.assertEqual(wf.database.get_meta("shortlist_stale"), "true")

    def test_cap_stops_without_claiming_completion(self):
        wf = self.workflow([["S", "N", "T"]], max_research_items=0)
        wf.client = FakeClient(request_node=True)
        runner = AdaptivePathRunner(wf)
        with self.assertRaisesRegex(RuntimeError, "Research safety cap"):
            runner.run()
        self.assertEqual(adaptive_progress(wf.database)["pending"], 1)
        self.assertEqual(adaptive_progress(wf.database)["directly_reviewed"], 0)
        self.assertEqual(wf.client.calls["path_picker_adaptive_screen"], 1)
        wf.config["max_research_items"] = 100
        self.fake_research(wf)
        wf._deep_review_paths = lambda: None
        runner.run()
        self.assertEqual(wf.client.calls["path_picker_adaptive_screen"], 2)

    def test_context_change_requires_new_run(self):
        wf = self.workflow([["S", "N", "T"]])
        AdaptivePathRunner(wf).initialize()
        wf.config["biological_context"] = {"cell_type": "unrelated"}
        with self.assertRaisesRegex(ValueError, "Biological context changed"):
            AdaptivePathRunner(wf).initialize()

    def test_idle_legacy_audit_is_safe(self):
        wf = self.workflow([["S", "N", "T"]])
        self.assertEqual(adaptive_progress(wf.database)["pending"], 1)
        self.assertEqual(list_exclusions(wf.database), [])
        export_adaptive_audit(wf.database)
        self.assertTrue((wf.run_directory / "exclusion_ledger.json").is_file())

    def test_complete_workflow_audits_final_narratives_and_all_finalist_constituents(self):
        wf = self.workflow([["S", "N", "A"], ["S", "N", "B"]])
        research = self.fake_research(wf)
        result = wf.run()
        self.assertEqual(len(result["cards"]), 2)
        self.assertEqual(wf.database.get_meta("status"), "complete")
        self.assertIn("Only 2", wf.database.get_meta("shortlist_warning"))
        self.assertEqual(wf.client.calls["path_picker_narrative_audit"], 3)
        self.assertEqual(wf.client.calls["path_picker_path_review"], 2)
        self.assertEqual(sum(research.values()), 7)  # 4 nodes + 3 unordered pairs.
        self.assertTrue((wf.run_directory / "report.html").is_file())
        wf.run()
        self.assertEqual(wf.client.calls["path_picker_shortlist"], 1)
        self.assertEqual(wf.client.calls["path_picker_narrative_audit"], 3)

    def test_narrative_audit_rejects_unsupported_fact_even_with_known_id(self):
        wf = self.workflow([["S", "N", "T"]])
        wf.client.structured = lambda **kwargs: ({"sentence_checks": [{"sentence_index": 1,
            "supported_by_cited_evidence": False, "clearly_labeled_defensible_inference": False,
            "claim_ids": ["CLM-A"], "explanation": "Citation does not entail this assertion"}],
            "other_card_fields_grounded_or_explicit_proposals": True, "issues": []}, {})
        with self.assertRaisesRegex(RuntimeError, "does not entail"):
            wf._audit_narrative("Unsupported assertion [CLM-A].", {}, [{"claim_id": "CLM-A"}], {})

    def test_cancel_stops_before_screen_and_preserves_pending(self):
        wf = self.workflow([["S", "N", "T"]])
        wf.cancel_event.set()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            wf.run()
        self.assertEqual(wf.database.get_meta("status"), "cancelled")
        self.assertEqual(adaptive_progress(wf.database)["pending"], 1)
        self.assertTrue((wf.run_directory / "path_coverage.json").is_file())

    def test_malformed_screen_is_not_cached_as_complete(self):
        wf = self.workflow([["S", "N", "T"]])
        runner = AdaptivePathRunner(wf)
        runner.initialize()
        path_id = wf._pending("path_screen", 1)[0]
        good = wf.client.structured
        def malformed(**kwargs):
            result, raw = good(**kwargs)
            result["steps"] = []
            return result, raw
        wf.client.structured = malformed
        with self.assertRaisesRegex(RuntimeError, "every directed traversal"):
            runner._screen(path_id)
        self.assertIsNone(wf._task_result("path_screen_initial", path_id))
        wf.client.structured = good
        runner._screen(path_id)
        self.assertEqual(adaptive_progress(wf.database)["directly_reviewed"], 1)


if __name__ == "__main__":
    unittest.main()
