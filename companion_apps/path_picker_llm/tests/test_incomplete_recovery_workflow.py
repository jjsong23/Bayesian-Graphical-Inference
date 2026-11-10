"""Offline regression tests for incomplete research and checkpointed recovery."""

from __future__ import annotations

import gzip
import json
import shutil
import unittest
import uuid
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from path_picker.adaptive import AdaptivePathRunner, adaptive_progress
from path_picker.ingest import canonical_edge, import_agent_graphs
from path_picker.workflow import PathPickerWorkflow


class IncompleteResearchClient:
    """Exercise workflow persistence without constructing a real API client."""

    def __init__(self, fail_on_calls: set[int]) -> None:
        self.fail_on_calls = fail_on_calls
        self.screen_calls: Counter[str] = Counter()
        self.research_batches: list[list[str]] = []

    def structured(self, **kwargs):
        name = kwargs["schema_name"]
        if name == "path_picker_adaptive_screen":
            bundle = json.loads(kwargs["prompt"].split("BUNDLE:\n", 1)[1])
            self.screen_calls[bundle["path_id"]] += 1
            symbols = bundle["path_symbols"]
            return {
                "assessment": "uncertain",
                "mechanistic_coherence": .2,
                "reasoning": "Fixture: no verified evidence is available.",
                "steps": [
                    {"upstream": a, "downstream": b, "mechanism": "unresolved",
                     "reasoning": "Hypothesis only.", "claim_ids": []}
                    for a, b in zip(symbols, symbols[1:])
                ],
                "node_roles": [
                    {"node": node, "mechanism": "unresolved", "reasoning": "Hypothesis only."}
                    for node in symbols[1:-1]
                ],
                "research_requests": [
                    {"kind": "edge", "item_id": canonical_edge(a, b),
                     "question": "Is there primary evidence for this acute step?"}
                    for a, b in zip(symbols, symbols[1:])
                ],
                "exclusion_proposals": [],
            }, {}
        if name == "path_picker_edge_research":
            items = json.loads(kwargs["prompt"].split("ITEMS:\n", 1)[1])
            identifiers = [item["item_id"] for item in items]
            self.research_batches.append(identifiers)
            if len(self.research_batches) in self.fail_on_calls:
                raise RuntimeError("Response incomplete; no partial claims accepted")
            return {"items": [
                {"item_id": identifier, "verdict": "uncertain", "mechanism": "unresolved",
                 "reasoning": "Fixture: search found no verifiable claim.", "claims": [],
                 "unresolved_questions": ["No accepted primary evidence."]}
                for identifier in identifiers
            ]}, {}
        raise AssertionError("Unexpected API substitute call: " + name)

    @staticmethod
    def source_urls(response):
        return set()


class IncompleteRecoveryWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent.parent / "runtime" / "test_incomplete_recovery"
        self.root = self.test_root / uuid.uuid4().hex
        self.root.mkdir(parents=True)
        for target in ("path_picker.client.urlopen", "path_picker.workflow.retrieve"):
            blocker = patch(target, side_effect=AssertionError("Network access is forbidden in this test"))
            blocker.start()
            self.addCleanup(blocker.stop)
        paths = [["S", "A", "B", "T1"], ["S", "A", "B", "T2"]]
        nodes = sorted({node for path in paths for node in path})
        edges = {canonical_edge(a, b): (a, b) for path in paths for a, b in zip(path, path[1:])}
        graph = {
            "run_id": "incomplete-recovery-fixture", "source_nodes": ["S"], "target_nodes": ["T1", "T2"],
            "nodes": [{"id": node, "label": node, "posterior_probability": .8,
                       "evidence_ledger": {"evidence": []}} for node in nodes],
            "edges": [{"id": identifier, "node_a": a, "node_b": b, "edge_probability": .8,
                       "evidence_ledger": {"evidence": []}} for identifier, (a, b) in edges.items()],
            "ranked_paths": [
                {"rank": index + 1, "query_id": f"S->{path[-1]}", "start_symbol": "S",
                 "target_symbol": path[-1], "hop_count": len(path) - 1, "primary_path_score": .7,
                 "path_symbols": " -> ".join(path)}
                for index, path in enumerate(paths)
            ],
        }
        self.input_path = self.root / "agent_graph.json.gz"
        with gzip.open(self.input_path, "wt", encoding="utf-8") as handle:
            json.dump(graph, handle)

    def tearDown(self):
        self.root.resolve().relative_to(self.test_root.resolve())
        shutil.rmtree(self.root)

    def workflow(self, client, *, batch_size: int, initialize: bool = False):
        wf = PathPickerWorkflow(
            run_directory=self.root / "run", input_path=self.input_path, api_key=None,
            configuration={"workflow_mode": "path_first", "research_model": "offline-fixture",
                           "screening_model": "offline-fixture", "edge_batch_size": batch_size},
        )
        wf.client = client
        if initialize:
            import_agent_graphs(self.input_path, wf.database)
        runner = AdaptivePathRunner(wf)
        runner.initialize()
        return wf, runner

    @staticmethod
    def path_ids(wf):
        with wf.database.connect() as db:
            return dict(db.execute("SELECT target_symbol,path_id FROM paths"))

    @staticmethod
    def task_snapshot(wf, stage, item_id):
        with wf.database.connect() as db:
            row = db.execute("SELECT * FROM tasks WHERE stage=? AND item_id=?", (stage, item_id)).fetchone()
        return dict(row) if row else None

    def assert_no_claims_or_sources(self, wf):
        with wf.database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM claims").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 0)

    def test_batch_three_failure_resumes_as_single_edges_without_repeating_initial_screen(self):
        client = IncompleteResearchClient(fail_on_calls={1})
        wf, runner = self.workflow(client, batch_size=3, initialize=True)
        identifiers = self.path_ids(wf)
        first = identifiers["T1"]
        edges = [canonical_edge("S", "A"), canonical_edge("A", "B"), canonical_edge("B", "T1")]

        with self.assertRaisesRegex(RuntimeError, "Response incomplete"):
            runner._screen(first)

        initial = self.task_snapshot(wf, "path_screen_initial", first)
        self.assertEqual(initial["status"], "complete")
        self.assertEqual(initial["attempts"], 1)
        self.assertTrue(json.loads(initial["result_json"])["_needs_refinement"])
        self.assertEqual(client.screen_calls[first], 1)
        self.assertEqual(client.research_batches, [edges])
        for edge in edges:
            task = self.task_snapshot(wf, "edge_research", edge)
            self.assertEqual(task["status"], "retryable")
            self.assertIsNone(task["result_json"])
            self.assertIsNone(wf._task_result("edge_research", edge))
        self.assertIsNone(wf._task_result("path_screen", first))
        self.assertEqual(adaptive_progress(wf.database)["pending"], 2)
        self.assertEqual(adaptive_progress(wf.database)["directly_reviewed"], 0)
        self.assertEqual(adaptive_progress(wf.database)["research_items_completed"], 0)
        self.assert_no_claims_or_sources(wf)

        # A new workflow instance demonstrates that recovery reads disk checkpoints.
        resumed, resumed_runner = self.workflow(client, batch_size=1)
        resumed_runner._screen(first)
        self.assertEqual(self.task_snapshot(resumed, "path_screen_initial", first), initial)
        self.assertEqual(client.screen_calls[first], 2)  # Initial once, refinement once.
        self.assertEqual(client.research_batches, [edges, *[[edge] for edge in edges]])
        for edge in edges:
            result = resumed._task_result("edge_research", edge)
            self.assertEqual(result["verdict"], "uncertain")
            self.assertEqual(result["accepted_claim_ids"], [])
        first_complete = self.task_snapshot(resumed, "path_screen", first)

        # The second path reuses two successful edge results and researches only its new edge.
        shared = {edge: self.task_snapshot(resumed, "edge_research", edge) for edge in edges[:2]}
        resumed_runner._screen(identifiers["T2"])
        self.assertEqual(client.research_batches, [edges, *[[edge] for edge in edges], [canonical_edge("B", "T2")]])
        self.assertEqual(self.task_snapshot(resumed, "path_screen", first), first_complete)
        for edge, saved in shared.items():
            self.assertEqual(self.task_snapshot(resumed, "edge_research", edge), saved)
        self.assertEqual(client.screen_calls[first], 2)
        self.assertEqual(adaptive_progress(resumed.database)["pending"], 0)
        self.assertEqual(adaptive_progress(resumed.database)["directly_reviewed"], 2)
        self.assertEqual(adaptive_progress(resumed.database)["uncertain"], 2)
        self.assertEqual(adaptive_progress(resumed.database)["research_items_completed"], 4)
        self.assert_no_claims_or_sources(resumed)

    def test_later_incomplete_preserves_already_completed_edge_on_resume(self):
        client = IncompleteResearchClient(fail_on_calls={2})
        wf, runner = self.workflow(client, batch_size=1, initialize=True)
        first = self.path_ids(wf)["T1"]
        edges = [canonical_edge("S", "A"), canonical_edge("A", "B"), canonical_edge("B", "T1")]
        with self.assertRaisesRegex(RuntimeError, "Response incomplete"):
            runner._screen(first)

        initial = self.task_snapshot(wf, "path_screen_initial", first)
        completed = self.task_snapshot(wf, "edge_research", edges[0])
        self.assertEqual(completed["status"], "complete")
        self.assertEqual(completed["attempts"], 1)
        self.assertEqual(self.task_snapshot(wf, "edge_research", edges[1])["status"], "retryable")
        self.assertIsNone(self.task_snapshot(wf, "edge_research", edges[2]))
        self.assertEqual(adaptive_progress(wf.database)["research_items_completed"], 1)
        self.assert_no_claims_or_sources(wf)

        resumed, resumed_runner = self.workflow(client, batch_size=1)
        resumed_runner._screen(first)
        self.assertEqual(self.task_snapshot(resumed, "path_screen_initial", first), initial)
        self.assertEqual(self.task_snapshot(resumed, "edge_research", edges[0]), completed)
        self.assertEqual(client.research_batches, [[edges[0]], [edges[1]], [edges[1]], [edges[2]]])
        self.assertEqual(client.screen_calls[first], 2)
        self.assertEqual(adaptive_progress(resumed.database)["research_items_completed"], 3)
        self.assertEqual(adaptive_progress(resumed.database)["directly_reviewed"], 1)
        self.assertEqual(adaptive_progress(resumed.database)["pending"], 1)
        self.assertEqual(resumed._task_result("path_screen", first)["assessment"], "uncertain")
        self.assert_no_claims_or_sources(resumed)


if __name__ == "__main__":
    unittest.main()
