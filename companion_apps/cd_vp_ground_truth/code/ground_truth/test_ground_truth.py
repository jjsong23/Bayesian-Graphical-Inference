from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

from ground_truth.pipeline import dry_run
from ground_truth.pipeline import GroundTruthPipeline
from ground_truth.evidence_profiles import PROFILE_IDS, profile
from ground_truth.postprocess import (
    build_edge_record,
    build_node_record,
    build_stream_reference_record,
    gate_evidence_item,
    verify_quote,
    write_outputs,
)
from ground_truth.prompts import edge_validation, pair_generation
from ground_truth.responses import ResponsesClient
from ground_truth.retrieval import RetrievalSession


class GroundTruthProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.metadata = {"123": {"title": "Primary experiment", "doi": "10.test/example", "journal": "Test", "year": "2020", "first_author": "One A", "last_author": "Lab A", "publication_types": ["Journal Article"]}}

    def test_quote_verification_normalizes_whitespace_and_unicode(self) -> None:
        self.assertEqual(verify_quote("AQP2\u00a0moved to the membrane.", "Results: AQP2 moved   to the membrane."), 1.0)
        self.assertLess(verify_quote("A paraphrased claim", "An unrelated result"), 0.95)

    def test_evidence_gate_requires_retrieved_quote_and_experimental_method(self) -> None:
        item = {"pmid": "123", "quote": "Direct result.", "method": "CELL_GENETIC"}
        kept, reason = gate_evidence_item(item, retrieved_text_by_pmid={"123": "Direct result."}, metadata_by_pmid=self.metadata)
        self.assertEqual(reason, "admitted")
        self.assertEqual(kept["quote_match"], 1.0)
        self.assertEqual(gate_evidence_item(item, retrieved_text_by_pmid={}, metadata_by_pmid=self.metadata)[1], "quote_source_not_retrieved")
        database_item = {**item, "method": "DATABASE_ASSERTION"}
        self.assertEqual(gate_evidence_item(database_item, retrieved_text_by_pmid={"123": "Direct result."}, metadata_by_pmid=self.metadata)[1], "method_not_experimental")

    def test_transcription_mediated_response_is_excluded(self) -> None:
        item = {
            "pmid": "123",
            "quote": "Vasopressin increased AQP2 mRNA through promoter activation.",
            "method": "CELL_GENETIC",
            "relation": "activates",
        }
        kept, reason = gate_evidence_item(
            item,
            retrieved_text_by_pmid={"123": item["quote"]},
            metadata_by_pmid=self.metadata,
        )
        self.assertIsNone(kept)
        self.assertEqual(reason, "transcription_mediated_not_transient")

    def test_no_evidence_is_unlabeled_not_negative(self) -> None:
        self.assertEqual(build_node_record("AQP2", [])["label"], "UNLABELED")
        self.assertEqual(build_node_record("LRP2", [{"pmid": "1", "polarity": "absent", "context": "native"}])["label"], "NEG_EXPLICIT")

    def test_causal_vp_edge_from_strong_lab_is_supported(self) -> None:
        item = {"pmid": "123", "method": "SITE_MUTANT", "actor": "PRKACA", "target": "AQP2", "relation": "phosphorylates", "sign": "+", "stimulus": "dDAVP", "context": "native_cd", "citation": {"last_author": "Lab A"}}
        record = build_edge_record(("AQP2", "PRKACA"), [item])
        self.assertEqual(record["best_label"], "CAUSAL")
        self.assertEqual(record["vp_tier"], "VP_EDGE")
        self.assertEqual(record["establishment_tier"], "SUPPORTED")
        self.assertTrue(record["in_final_list"])

    def test_binding_only_never_enters_final_list(self) -> None:
        item = {"pmid": "123", "method": "PHYSICAL", "actor": "A", "target": "B", "relation": "binds", "sign": "+", "stimulus": "dDAVP", "context": "native_cd", "citation": {"last_author": "Lab A"}}
        self.assertFalse(build_edge_record(("A", "B"), [item])["in_final_list"])

    def test_physical_label_is_preserved_when_pair_also_has_causal_evidence(self) -> None:
        items = [
            # Store the undirected binding record in the opposite orientation
            # to the causal record; it must still survive presentation.
            {"pmid": "123", "method": "PHYSICAL", "actor": "AQP2", "target": "AKAP220", "relation": "binds", "sign": "+", "stimulus": "none", "context": "other_cell", "citation": {"last_author": "Lab A"}},
            {"pmid": "456", "method": "CELL_GENETIC", "actor": "AKAP220", "target": "AQP2", "relation": "activates", "sign": "+", "stimulus": "dDAVP", "context": "native_cd", "citation": {"last_author": "Lab B"}},
        ]
        record = build_edge_record(("AKAP220", "AQP2"), items)
        self.assertEqual(record["best_label"], "CAUSAL")
        self.assertIn("PHYSICAL", record["supported_labels"])
        self.assertTrue(record["physical_interaction_supported"])
        self.assertEqual(len(record["physical_evidence"]), 1)

    def test_output_writer_exports_only_qualifying_edges(self) -> None:
        qualifying = build_edge_record(("AQP2", "PRKACA"), [{"pmid": "123", "method": "SITE_MUTANT", "actor": "PRKACA", "target": "AQP2", "relation": "phosphorylates", "sign": "+", "stimulus": "dDAVP", "context": "native_cd", "citation": {"last_author": "Lab A"}}])
        excluded = build_edge_record(("A", "B"), [{"pmid": "456", "method": "PHYSICAL", "actor": "A", "target": "B", "relation": "binds", "sign": "+", "stimulus": "none", "context": "cell_free", "citation": {"last_author": "Lab B"}}])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_outputs(output, [excluded, qualifying], [{"kind": "edge", "reason": "test"}])
            jsonl = (output / "edges.jsonl").read_text(encoding="utf-8").splitlines()
            tsv = (output / "edges.tsv").read_text(encoding="utf-8")
            audit = (output / "audit.tsv").read_text(encoding="utf-8")
        self.assertEqual(len(jsonl), 1)
        self.assertIn('"source": "PRKACA"', jsonl[0])
        self.assertIn("https://pubmed.ncbi.nlm.nih.gov/123/", tsv)
        self.assertIn("test", audit)

    def test_dry_run_makes_no_api_call_and_reports_pair_search_bounds(self) -> None:
        config = {
            "api_base_url": "https://example.openai.azure.com/openai/v1",
            "deployments": {"generator": "luna", "validator": "sol"},
            "pair_search_calls_per_round": 24,
            "min_pair_search_rounds": 3,
            "max_pair_search_rounds": 10,
            "max_pair_candidates": 1500,
        }
        with tempfile.TemporaryDirectory() as directory:
            report = dry_run(config, Path(directory))
        self.assertEqual(report["protocol_version"], "0.6")
        self.assertEqual(report["pair_search_calls_per_round"], 24)
        self.assertEqual(report["minimum_pair_generation_calls"], 72)
        self.assertEqual(report["maximum_pair_generation_calls"], 240)
        self.assertEqual(report["maximum_pair_candidates"], 1500)

    def test_pair_discovery_stops_at_saturation_and_deduplicates(self) -> None:
        config = {
            "api_base_url": "https://example.openai.azure.com/openai/v1",
            "deployments": {"generator": "luna", "validator": "sol"},
            "generator_concurrency": 2,
            "pair_search_calls_per_round": 2,
            "min_pair_search_rounds": 2,
            "max_pair_search_rounds": 5,
            "pair_search_saturation_rounds": 1,
            "pair_search_min_new_pairs": 1,
            "max_pair_candidates": 10,
        }
        pair_ab = {"source": "A", "target": "B", "confidence": "high", "shown_in_cd": "yes", "shown_with_vasopressin": "yes", "relation": "activates", "candidate_polarity": "positive", "pka_relationship": "pka_dependent"}
        pair_bc = {"source": "B", "target": "C", "confidence": "medium", "shown_in_cd": "unknown", "shown_with_vasopressin": "unknown", "relation": "binds", "candidate_polarity": "positive", "pka_relationship": "direct_pka"}

        def fake_parallel(specifications, **_kwargs):
            round_number = specifications[0]["task"]["round"]
            if round_number == 1:
                return {1: {"pairs": [pair_ab]}, 2: {"pairs": [pair_bc, pair_ab]}}
            return {1: {"pairs": [pair_ab]}, 2: {"pairs": [pair_bc]}}

        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            pipeline = GroundTruthPipeline(config, run_dir, api_key="test")
            with patch.object(pipeline, "_parallel_generation", side_effect=fake_parallel) as pool:
                pairs = pipeline.discover_pairs()
            checkpoint = __import__("json").loads((run_dir / "pair_generation_checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(pool.call_count, 2)
        self.assertEqual({tuple(item["pair"]) for item in pairs}, {("A", "B"), ("B", "C")})
        self.assertEqual(checkpoint["completed_round"], 2)
        self.assertTrue(checkpoint["complete"])

    def test_string_uses_acute_regulation_and_phosphorylation_is_subset(self) -> None:
        self.assertNotIn("targeted_functional_association", PROFILE_IDS)
        self.assertEqual(
            profile("acute_regulatory_signaling")["calibrates_streams"],
            ["omnipath_core", "string_v12"],
        )
        config = {
            "api_base_url": "https://example.openai.azure.com/openai/v1",
            "deployments": {"generator": "luna", "validator": "sol"},
            "evidence_profiles": [
                "directed_phosphorylation", "acute_regulatory_signaling"
            ],
            "pair_search_calls_per_round": 1,
            "min_pair_search_rounds": 1,
            "max_pair_search_rounds": 1,
            "pair_search_saturation_rounds": 1,
            "pair_search_min_new_pairs": 0,
            "max_pair_candidates": 10,
            "minimum_candidates_per_profile_polarity": 1,
        }
        candidate = {
            "source": "PRKACA", "target": "AQP2", "confidence": "high",
            "shown_in_cd": "yes", "shown_with_vasopressin": "yes",
            "relation": "phosphorylates", "candidate_polarity": "positive",
            "pka_relationship": "direct_pka",
        }
        roster = [{
            "validation_profile": "directed_phosphorylation",
            "polarity_target": "positive",
            "search_focus": "direct PKA substrates",
        }]
        with tempfile.TemporaryDirectory() as directory:
            pipeline = GroundTruthPipeline(config, Path(directory), api_key="test")
            with (
                patch("ground_truth.pipeline.PAIR_SEARCH_TASKS", roster),
                patch.object(
                    pipeline, "_parallel_generation",
                    return_value={1: {"pairs": [candidate]}},
                ),
            ):
                pairs = pipeline.discover_pairs()
        profiles = {
            item["validation_profile"] for item in pairs
            if item["pair"] == ["AQP2", "PRKACA"]
        }
        self.assertEqual(
            profiles,
            {"directed_phosphorylation", "acute_regulatory_signaling"},
        )

    def test_production_no_longer_requires_holdout_review(self) -> None:
        config = {"api_base_url": "https://example.openai.azure.com/openai/v1", "api_key_env": "TEST_GROUND_TRUTH_KEY", "deployments": {"generator": "luna", "validator": "sol"}}
        with tempfile.TemporaryDirectory() as directory:
            pipeline = GroundTruthPipeline(config, Path(directory), api_key="test-key")
        self.assertEqual(pipeline.validator_model, "sol")

    def test_tool_loop_resends_call_and_output_without_remote_storage(self) -> None:
        first = {"id": "resp_1", "output": [{"type": "function_call", "call_id": "call_1", "name": "search_pubmed", "arguments": '{"query":"AQP2"}'}]}
        second = {"id": "resp_2", "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}]}
        client = ResponsesClient(base_url="https://example.openai.azure.com/openai/v1", api_key="test")
        with tempfile.TemporaryDirectory() as directory:
            retrieval = RetrievalSession(Path(directory))
            with patch.object(retrieval, "call", return_value={"pmids": ["1"]}), patch.object(client, "request", side_effect=[first, second]) as request:
                result, _ = client.tool_structured(model="validator", prompt="blind pair", schema_name="test", schema={"type": "object", "additionalProperties": False, "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}, reasoning_effort="high", retrieval=retrieval)
        self.assertTrue(result["ok"])
        followup = request.call_args_list[1].args[0]
        self.assertFalse(followup["store"])
        self.assertIn("function_call_output", [item.get("type") for item in followup["input"]])

    def test_responses_client_retries_connection_reset(self) -> None:
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"id":"ok"}'
        client = ResponsesClient(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test", max_retries=2, retry_base_seconds=0,
        )
        with patch("ground_truth.responses.urlopen", side_effect=[URLError(ConnectionResetError(10054, "reset")), response]) as request, patch("ground_truth.responses.time.sleep") as sleep:
            result = client.request({"model": "test"})
        self.assertEqual(result["id"], "ok")
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(0.0)

    def test_responses_client_recovers_from_token_rate_limit(self) -> None:
        limited = HTTPError(
            "https://example.openai.azure.com/openai/v1/responses",
            429,
            "rate limited",
            {"Retry-After": "0"},
            None,
        )
        limited.read = MagicMock(return_value=(
            b'{"error":{"code":"rate_limit_exceeded","message":"token rate limit"}}'
        ))
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"id":"ok"}'
        client = ResponsesClient(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test",
            max_retries=2,
            retry_base_seconds=0,
            retry_max_seconds=0,
            rate_limit_base_seconds=0,
        )
        with (
            patch("ground_truth.responses.urlopen", side_effect=[limited, response]) as request,
            patch("ground_truth.responses.time.sleep"),
        ):
            result = client.request({"model": "test"})
        self.assertEqual(result["id"], "ok")
        self.assertEqual(request.call_count, 2)
        self.assertGreaterEqual(client._adaptive_request_interval, 0.25)

    def test_responses_client_does_not_retry_spend_limit(self) -> None:
        limited = HTTPError(
            "https://example.openai.azure.com/openai/v1/responses",
            429,
            "spend limit",
            {},
            None,
        )
        limited.read = MagicMock(return_value=(
            b'{"error":{"code":"organization_spend_limit_exceeded","message":"limit"}}'
        ))
        client = ResponsesClient(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test",
        )
        with patch("ground_truth.responses.urlopen", side_effect=limited) as request:
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                client.request({"model": "test"})
        self.assertEqual(request.call_count, 1)

    def test_rate_limit_reset_duration_parser(self) -> None:
        self.assertEqual(ResponsesClient._duration_seconds("1m30s"), 90.0)
        self.assertEqual(ResponsesClient._duration_seconds("250ms"), 0.25)
        self.assertIsNone(ResponsesClient._duration_seconds("tomorrow"))

    def test_generator_requires_hosted_web_search(self) -> None:
        response = {
            "id": "resp_web",
            "model": "gpt-6-luna",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}],
        }
        client = ResponsesClient(base_url="https://example.openai.azure.com/openai/v1", api_key="test")
        schema = {
            "type": "object", "additionalProperties": False, "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        }
        with patch.object(client, "request", return_value=response) as request:
            result, _ = client.structured(
                model="gpt-6-luna", prompt="find candidates", schema_name="test",
                schema=schema, reasoning_effort="none", web_search=True,
                search_context_size="low", require_web_search=True,
            )
        self.assertTrue(result["ok"])
        payload = request.call_args.args[0]
        self.assertEqual(payload["tools"], [{"type": "web_search", "search_context_size": "low"}])
        self.assertEqual(payload["include"], ["web_search_call.action.sources"])
        self.assertEqual(payload["tool_choice"], "required")
        self.assertFalse(payload["store"])

    def test_output_text_skips_empty_content_blocks(self) -> None:
        response = {
            "id": "resp_empty_first",
            "output": [{
                "type": "message",
                "content": [
                    {"type": "output_text", "text": ""},
                    {"type": "output_text", "text": '{"ok":true}'},
                ],
            }],
        }
        self.assertEqual(ResponsesClient.output_text(response), '{"ok":true}')

    def test_structured_retries_empty_success_response(self) -> None:
        empty = {
            "id": "resp_empty",
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": ""}]}],
        }
        complete = {
            "id": "resp_complete",
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok":true}'}]}],
        }
        client = ResponsesClient(
            base_url="https://example.openai.azure.com/openai/v1",
            api_key="test", max_content_retries=2, retry_base_seconds=0,
        )
        schema = {
            "type": "object", "additionalProperties": False, "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        }
        with patch.object(client, "request", side_effect=[empty, complete]) as request, patch("ground_truth.responses.time.sleep") as sleep:
            result, response = client.structured(
                model="gpt-6-luna", prompt="find candidates", schema_name="test",
                schema=schema, reasoning_effort="none", web_search=True,
                require_web_search=True,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(response["id"], "resp_complete")
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(0.0)

    def test_edge_generation_reuses_each_completed_call(self) -> None:
        config = {
            "api_base_url": "https://example.openai.azure.com/openai/v1",
            "deployments": {"generator": "luna", "validator": "sol"},
            "edge_generation_repeats": 1, "snowball_depth": 0,
            "generator_concurrency": 1,
        }
        nodes = {"A": {"symbol": "A", "appearances": 1, "provenance": []}}
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            first = GroundTruthPipeline(config, run_dir, api_key="test")
            with patch.object(first, "_structured", side_effect=[{"partners": [{"partner": "B"}]}, RuntimeError("temporary reset")]):
                with self.assertRaisesRegex(RuntimeError, "temporary reset"):
                    first.generate_edges(dict(nodes))
            cached_up = run_dir / "generation" / "edges" / "node_00001_A" / "up_01.json"
            self.assertTrue(cached_up.is_file())
            cached_payload = __import__("json").loads(cached_up.read_text(encoding="utf-8"))
            self.assertEqual(cached_payload["schema_version"], 2)
            self.assertFalse((run_dir / "edge_candidates.json").exists())

            resumed = GroundTruthPipeline(config, run_dir, api_key="test")
            with patch.object(resumed, "_structured", return_value={"partners": []}) as request:
                edges = resumed.generate_edges(dict(nodes))
            request.assert_called_once()
            self.assertEqual(edges[0]["pair"], ["A", "B"])
            self.assertTrue((run_dir / "edge_generation_checkpoint.json").is_file())

    def test_generation_worker_pool_reaches_configured_parallelism(self) -> None:
        config = {
            "api_base_url": "https://example.openai.azure.com/openai/v1",
            "deployments": {"generator": "luna", "validator": "sol"},
            "generator_concurrency": 3,
        }
        with tempfile.TemporaryDirectory() as directory:
            pipeline = GroundTruthPipeline(config, Path(directory), api_key="test")
            active = 0
            maximum_active = 0
            lock = threading.Lock()

            def fake_cached(cache_path, *, task, role, **kwargs):
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                time.sleep(0.02)
                with lock:
                    active -= 1
                return {"key": task["key"]}

            specifications = [{
                "key": index,
                "cache_path": Path(directory) / f"{index}.json",
                "task": {"key": index},
                "role": "node_generation",
                "kwargs": {},
            } for index in range(6)]
            with patch.object(pipeline, "_cached_generation", side_effect=fake_cached):
                results = pipeline._parallel_generation(
                    specifications, message="test", progress_start=0.0, progress_end=1.0,
                )
        self.assertEqual(maximum_active, 3)
        self.assertEqual([results[index]["key"] for index in range(6)], list(range(6)))

    def test_edge_bio_policy_block_is_saved_for_manual_review(self) -> None:
        config = {
            "api_base_url": "https://example.openai.azure.com/openai/v1",
            "deployments": {"generator": "luna", "validator": "sol"},
        }
        edge = {
            "pair": ["NFAT5", "RELA"],
            "validation_profile": "acute_regulatory_signaling",
            "decoy_type": "",
        }
        nodes = {symbol: {"symbol": symbol} for symbol in edge["pair"]}
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            pipeline = GroundTruthPipeline(config, run_dir, api_key="test")
            with patch.object(
                pipeline.client,
                "tool_structured",
                side_effect=RuntimeError(
                    'Responses API HTTP 400: {"error":{"code":"bio_policy",'
                    '"message":"possible biological risk"}}'
                ),
            ):
                record, audit = pipeline._validate_edge(edge, nodes, {})
            marker = run_dir / "validation" / "edges" / "acute_regulatory_signaling" / "NFAT5__RELA" / "policy_review_required.json"
            self.assertTrue(marker.is_file())
            self.assertFalse(record["h1_reference"])
            self.assertFalse(record["h0_reference"])
            self.assertEqual(record["validation_status"], "policy_review_required")
            self.assertEqual(audit[-1]["action"], "manual_literature_review")

            resumed = GroundTruthPipeline(config, run_dir, api_key="test")
            with patch.object(resumed.client, "tool_structured") as request:
                cached_record, cached_audit = resumed._validate_edge(edge, nodes, {})
            request.assert_not_called()
            self.assertEqual(cached_record["validation_status"], "policy_review_required")
            self.assertEqual(cached_audit[-1]["reason"], "policy_review_required")

    def test_stream_specific_prompts_are_pka_wide_and_search_both_polarities(self) -> None:
        discovery = pair_generation(
            "physical_interaction", "negative", "PKA complexes", 1, 1
        )
        validation = edge_validation(
            ({"symbol": "AKAP11"}, {"symbol": "PPP1CA"}),
            "physical_interaction",
        )
        self.assertIn(
            "Do not restrict the search to AQP2 partners",
            " ".join(discovery.split()),
        )
        self.assertIn("explicit published null experiment", discovery)
        self.assertIn("co-ip", validation.casefold())
        self.assertIn("Search positive and negative evidence separately", validation)

    def test_stream_reference_requires_pka_specific_explicit_results(self) -> None:
        positive = {
            "pmid": "1", "polarity": "positive", "method": "COIP",
            "actor": "A", "target": "B", "pka_relationship": "pka_dependent",
        }
        negative = {
            "pmid": "2", "polarity": "negative", "method": "COIP",
            "actor": "A", "target": "C", "pka_relationship": "pka_dependent",
            "negative_controls": "input and positive-control immunoprecipitation reported",
        }
        context_only = {
            "pmid": "3", "polarity": "positive", "method": "COIP",
            "actor": "A", "target": "D",
            "pka_relationship": "camp_or_vasopressin_only",
        }
        self.assertEqual(
            build_stream_reference_record(
                ("A", "B"), "physical_interaction", [positive]
            )["reference_label"],
            "H1_POSITIVE",
        )
        self.assertEqual(
            build_stream_reference_record(
                ("A", "C"), "physical_interaction", [negative]
            )["reference_label"],
            "H0_EXPLICIT_NEGATIVE",
        )
        self.assertEqual(
            build_stream_reference_record(
                ("A", "D"), "physical_interaction", [context_only]
            )["reference_label"],
            "CONTEXT_ONLY",
        )


if __name__ == "__main__":
    unittest.main()
