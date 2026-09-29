"""Tests for the optional, downstream literature interpreter."""

from __future__ import annotations

import io
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

import literature_interpreter


def _result() -> dict[str, object]:
    return {
        "hypothesis_label": "Prkaca — Aqp2",
        "classification": "known_in_exact_context",
        "confidence": 0.9,
        "one_sentence_takeaway": "The edge is reported in the requested context.",
        "contextual_evidence": [],
        "novelty_interpretation": "Not novel.",
        "mechanistic_interpretation": "Mechanistically plausible.",
        "bayesian_evidence_summary": "The frozen ledger supports the edge.",
        "database_trace_summary": "STRING and BioGRID records were traced.",
        "conflicting_or_missing_evidence": [],
        "caveats": ["The interpretation cannot alter the graph."],
        "sources": [],
    }


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class LiteratureInterpreterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(__file__).parent / f".literature_test_{uuid.uuid4().hex}"
        self.directory.mkdir()
        self.ledger = {
            "kind": "edge",
            "node_a": "Prkaca",
            "node_b": "Aqp2",
            "prior_probability": 0.5,
            "stored_posterior_probability": 0.9,
            "streams": [
                {
                    "enabled": True,
                    "stream_id": "string_v12",
                    "label": "STRING v12",
                    "status": "supports",
                    "applied_bayes_factor": 3.0,
                    "weight": 0.5,
                    "weighted_log2_odds_contribution": 0.792,
                    "provenance_trace": {
                        "database": "STRING",
                        "summary": "Combined score traceback.",
                        "factors": [{"factor": "Experiments", "value": 0.8}],
                        "records": [],
                        "links": [],
                    },
                }
            ],
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_payload_requires_web_search_and_preserves_frozen_ledger(self) -> None:
        payload = literature_interpreter._request_payload(
            self.ledger,
            cell_type="collecting duct principal cells",
            signaling_purpose="vasopressin-regulated water transport",
            model="gpt-5.5",
            reasoning_effort="high",
        )
        self.assertEqual(payload["tools"][0]["type"], "web_search")
        self.assertEqual(payload["tool_choice"], "required")
        self.assertTrue(payload["text"]["format"]["strict"])
        self.assertIn("Do not recalculate or alter any Bayes factor", payload["input"])
        self.assertIn("Combined score traceback", payload["input"])

    def test_missing_key_is_explicit_and_does_not_create_cache(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                literature_interpreter.interpret_hypothesis(
                    self.ledger,
                    self.directory,
                    cell_type="collecting duct principal cells",
                    signaling_purpose="vasopressin",
                )
        self.assertFalse((self.directory / "literature_interpretations").exists())

    def test_response_is_cached_and_second_request_avoids_network(self) -> None:
        response = {
            "id": "resp_test",
            "usage": {"total_tokens": 10},
            "output": [
                {
                    "type": "web_search_call",
                    "action": {
                        "sources": [
                            {"title": "Primary study", "url": "https://example.org/study"}
                        ]
                    },
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(_result())}],
                },
            ],
        }
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch.object(literature_interpreter, "urlopen", return_value=_Response(response)) as mocked,
        ):
            first = literature_interpreter.interpret_hypothesis(
                self.ledger,
                self.directory,
                cell_type="collecting duct principal cells",
                signaling_purpose="vasopressin",
            )
            second = literature_interpreter.interpret_hypothesis(
                self.ledger,
                self.directory,
                cell_type="collecting duct principal cells",
                signaling_purpose="vasopressin",
            )
        self.assertEqual(mocked.call_count, 1)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["interpretation"]["classification"], "known_in_exact_context")
        self.assertEqual(first["web_sources"][0]["url"], "https://example.org/study")

    def test_explicit_session_key_is_accepted_but_never_cached(self) -> None:
        response = {
            "id": "resp_session_key",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(_result())}]}
            ],
        }
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(literature_interpreter, "urlopen", return_value=_Response(response)) as mocked,
        ):
            result = literature_interpreter.interpret_hypothesis(
                self.ledger,
                self.directory,
                cell_type="collecting duct principal cells",
                signaling_purpose="vasopressin",
                api_key="session-secret",
            )
        request = mocked.call_args.args[0]
        self.assertEqual(request.headers["Authorization"], "Bearer session-secret")
        self.assertNotIn("session-secret", json.dumps(result))
        cache_text = next((self.directory / "literature_interpretations").glob("*.json")).read_text()
        self.assertNotIn("session-secret", cache_text)

    def test_azure_openai_base_url_targets_its_responses_endpoint(self) -> None:
        response = {
            "id": "resp_azure",
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": json.dumps(_result())}]}
            ],
        }
        azure_base_url = "https://songjj-5910-resource.services.ai.azure.com/openai/v1"
        with patch.object(
            literature_interpreter,
            "urlopen",
            return_value=_Response(response),
        ) as mocked:
            result = literature_interpreter.interpret_hypothesis(
                self.ledger,
                self.directory,
                cell_type="collecting duct principal cells",
                signaling_purpose="vasopressin",
                model="gpt-6-sol",
                api_key="azure-session-secret",
                api_base_url=azure_base_url,
            )
        request = mocked.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://songjj-5910-resource.services.ai.azure.com/openai/v1/responses",
        )
        self.assertEqual(request.headers["Authorization"], "Bearer azure-session-secret")
        self.assertEqual(result["model"], "gpt-6-sol")
        self.assertEqual(result["api_provider"], "azure_openai")
        self.assertEqual(result["api_hostname"], "songjj-5910-resource.services.ai.azure.com")
        self.assertNotIn("azure-session-secret", json.dumps(result))

    def test_prompt_searches_all_contexts_and_schema_omits_removed_sections(self) -> None:
        prompt = literature_interpreter._prompt(
            self.ledger,
            cell_type="collecting duct principal cells",
            signaling_purpose="vasopressin",
        )
        self.assertIn("ANY biological context", prompt)
        self.assertIn("do not discard", prompt)
        self.assertIn("biological_context", json.dumps(literature_interpreter.INTERPRETATION_SCHEMA))
        self.assertNotIn("recommended_follow_up", literature_interpreter.INTERPRETATION_SCHEMA["properties"])
        self.assertNotIn("plain_language_summary", literature_interpreter.INTERPRETATION_SCHEMA["properties"])

    def test_azure_missing_deployment_error_explains_endpoint_pairing(self) -> None:
        error_body = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "code": "DeploymentNotFound",
                    "message": "The API deployment for this resource does not exist.",
                }
            }
        ).encode("utf-8")
        error = HTTPError(
            "https://songjj-5910-resource.openai.azure.com/openai/v1/responses",
            404,
            "Not Found",
            {},
            io.BytesIO(error_body),
        )
        with (
            patch.object(literature_interpreter, "urlopen", side_effect=error),
            self.assertRaisesRegex(RuntimeError, "same Azure AI Foundry deployment") as raised,
        ):
            literature_interpreter._openai_response(
                {"model": "gpt-6-sol", "input": "test"},
                api_key="session-secret",
                api_base_url="https://songjj-5910-resource.openai.azure.com/openai/v1",
                timeout_seconds=5,
            )
        self.assertIn("gpt-6-sol", str(raised.exception))
        self.assertIn("services.ai.azure.com", str(raised.exception))

    def test_custom_endpoint_rejects_non_openai_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "official Azure OpenAI"):
            literature_interpreter._api_endpoint("https://example.org/v1")
        with self.assertRaisesRegex(ValueError, "must use HTTPS"):
            literature_interpreter._api_endpoint("http://api.openai.com/v1")

    def test_whole_network_research_audits_every_item_and_synthesizes(self) -> None:
        node_ledger = {
            "kind": "node",
            "symbol": "Aqp2",
            "stored_posterior_probability": 0.8,
            "streams": [],
        }
        edge_result = {"item_id": "edge:aqp2|prkaca", **_result()}
        node_result = {
            "item_id": "node:aqp2",
            **{**_result(), "hypothesis_label": "Aqp2"},
        }
        batch_response = {
            "id": "resp_batch",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps({"items": [edge_result, node_result]}),
                        }
                    ],
                }
            ],
        }
        synthesis = {
            "overall_summary": "A compact pathway audit.",
            "established_core": ["Aqp2"],
            "contextually_novel_candidates": [],
            "important_conflicts": [],
            "pathway_level_interpretation": "The pathway is plausible.",
            "recommended_validation_priorities": ["Validate the edge."],
            "limitations": ["Literature retrieval is not exhaustive."],
        }
        synthesis_response = {
            "id": "resp_synthesis",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(synthesis)}],
                }
            ],
        }
        with patch.object(
            literature_interpreter,
            "urlopen",
            side_effect=[_Response(batch_response), _Response(synthesis_response)],
        ) as mocked:
            first = literature_interpreter.interpret_pathway_network(
                [self.ledger, node_ledger],
                self.directory,
                cell_type="collecting duct principal cells",
                signaling_purpose="vasopressin",
                api_key="session-secret",
            )
            second = literature_interpreter.interpret_pathway_network(
                [self.ledger, node_ledger],
                self.directory,
                cell_type="collecting duct principal cells",
                signaling_purpose="vasopressin",
                api_key="session-secret",
            )
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(first["hypothesis_count"], 2)
        self.assertEqual(first["node_count"], 1)
        self.assertEqual(first["edge_count"], 1)
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(first["synthesis"]["overall_summary"], "A compact pathway audit.")
        self.assertTrue(second["cache_hit"])
        self.assertNotIn("session-secret", json.dumps(first))


if __name__ == "__main__":
    unittest.main()
