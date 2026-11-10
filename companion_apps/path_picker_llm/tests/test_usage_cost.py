"""Offline accounting checks: no model calls, credentials, or live billing reads."""

import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from path_picker.client import ResponsesClient
from path_picker.usage import default_pricing, summarize_usage


def response(**changes):
    return {"event": "response", "response_id": "resp_1", "attempt_number": 1,
            "model": "gpt-6-luna", "actual_model": "gpt-6-luna", "service_tier": "default",
            "provider": "azure", "web_search_requested": False,
            "input_tokens": 1000, "cached_input_tokens": 200, "cache_write_input_tokens": 100,
            "cache_write_included_in_input": True, "output_tokens": 500,
            "reasoning_tokens_included_in_output": 400, **changes}


class UsageCostTests(unittest.TestCase):
    def summarize(self, *rows, config=None, suffix=""):
        content = "\n".join(json.dumps(row) for row in rows) + "\n" + suffix
        with patch.object(Path, "exists", return_value=True), patch.object(Path, "open", return_value=io.StringIO(content)), patch("path_picker.client.urlopen") as network:
            summary = summarize_usage("offline.jsonl", config)
            network.assert_not_called()
            return summary

    def test_no_log_is_no_calls_not_a_paid_request(self):
        with patch.object(Path, "exists", return_value=False):
            summary = summarize_usage("not-present.jsonl")
        self.assertEqual(summary["attempts"], 0)
        self.assertEqual(summary["tokens"]["input"], 0)
        self.assertEqual(summary["cost"]["known_estimated_usd"], 0)
        self.assertTrue(summary["cost"]["complete"])

    def test_cache_read_write_and_reasoning_are_not_double_billed(self):
        summary = self.summarize(response())
        expected = (700 * .1 + 200 * .01 + 100 * .125 + 500 * .5) / 1_000_000
        self.assertAlmostEqual(summary["cost"]["token_estimated_usd"], expected)
        self.assertEqual(summary["tokens"]["uncached_input"], 700)
        self.assertEqual(summary["tokens"]["reasoning_included_in_output"], 400)
        self.assertTrue(summary["cost"]["complete"])

    def test_long_context_is_per_request_not_cumulative(self):
        row = response(input_tokens=300000, cached_input_tokens=100000, cache_write_input_tokens=50000, output_tokens=1000)
        summary = self.summarize(row)
        self.assertAlmostEqual(summary["cost"]["token_estimated_usd"], .04525)
        rows = [response(response_id=str(i), input_tokens=200000, cached_input_tokens=0, cache_write_input_tokens=0) for i in range(2)]
        summary = self.summarize(*rows)
        self.assertAlmostEqual(summary["cost"]["token_estimated_usd"], 2 * (.02 + .00025))

    def test_threshold_is_strictly_greater_than_272k(self):
        summary = self.summarize(response(input_tokens=272000, cached_input_tokens=0, cache_write_input_tokens=0))
        self.assertAlmostEqual(summary["cost"]["token_estimated_usd"], .0272 + .00025)

    def test_azure_uses_reported_bing_requests_never_output_actions(self):
        summary = self.summarize(response(web_search_requested=True, bing_requests=3, openai_search_actions=99))
        self.assertAlmostEqual(summary["cost"]["search_estimated_usd"], .042)
        self.assertEqual(summary["search"]["reported_requests"], 3)
        self.assertEqual(summary["search"]["observed_actions"], 99)
        self.assertTrue(any("unverified reference rate" in warning for warning in summary["warnings"]))

    def test_search_actions_without_billable_counter_remain_unknown(self):
        summary = self.summarize(response(web_search_requested=True, openai_search_actions=99))
        self.assertEqual(summary["cost"]["search_estimated_usd"], 0)
        self.assertFalse(summary["cost"]["search_complete"])
        self.assertEqual(summary["search"]["unknown_response_count"], 1)

    def test_nonsearch_zero_distinct_from_missing_token_usage(self):
        summary = self.summarize(response(input_tokens=None, output_tokens=None, cached_input_tokens=None, cache_write_input_tokens=None))
        self.assertIsNone(summary["tokens"]["input"])
        self.assertFalse(summary["cost"]["token_complete"])
        self.assertTrue(summary["cost"]["search_complete"])
        self.assertEqual(summary["cost"]["search_estimated_usd"], 0)
        self.assertFalse(summary["cost"]["complete"])

    def test_explicit_zero_usage_is_complete(self):
        summary = self.summarize(response(input_tokens=0, cached_input_tokens=None, cache_write_input_tokens=None, output_tokens=0, reasoning_tokens_included_in_output=0))
        self.assertTrue(summary["cost"]["complete"])
        self.assertEqual(summary["cost"]["known_estimated_usd"], 0)

    def test_missing_cache_writes_do_not_default_to_zero(self):
        summary = self.summarize(response(cache_write_input_tokens=None))
        self.assertIsNone(summary["tokens"]["cache_write"])
        self.assertFalse(summary["cost"]["token_complete"])
        self.assertAlmostEqual(summary["cost"]["token_estimated_usd"], .00025)
        configured = self.summarize(response(cache_write_input_tokens=None), config={"models": {"gpt-6-luna": {"cache_write_separately_billed": False}}})
        self.assertTrue(configured["cost"]["token_complete"])

    def test_ambiguous_or_overlapping_cache_usage_is_not_double_priced(self):
        for changes in ({"cache_write_included_in_input": None}, {"cached_input_tokens": 950}):
            with self.subTest(changes=changes):
                summary = self.summarize(response(**changes))
                self.assertFalse(summary["cost"]["token_complete"])
                self.assertAlmostEqual(summary["cost"]["token_estimated_usd"], .00025)

    def test_openai_search_requires_verified_rate_and_authoritative_counter(self):
        row = response(provider="openai", web_search_requested=True, billable_search_requests=4,
                       search_usage_source="tool_usage.web_search.num_requests", openai_search_actions=100)
        summary = self.summarize(row)
        self.assertFalse(summary["cost"]["search_complete"])
        summary = self.summarize(row, config={"search": {"openai_rate_verified": True}})
        self.assertTrue(summary["cost"]["search_complete"])
        self.assertAlmostEqual(summary["cost"]["search_estimated_usd"], .04)
        row["search_usage_source"] = None
        self.assertFalse(self.summarize(row, config={"search": {"openai_rate_verified": True}})["cost"]["search_complete"])

    def test_unknown_model_and_null_prices_do_not_silently_default(self):
        summary = self.summarize(response(model="deployment-a", actual_model="model-new"))
        self.assertFalse(summary["cost"]["token_complete"])
        self.assertTrue(any("No pricing" in warning for warning in summary["warnings"]))
        summary = self.summarize(response(), config={"models": {"gpt-6-luna": {"output_per_million": None}}})
        self.assertFalse(summary["cost"]["token_complete"])
        self.assertAlmostEqual(summary["cost"]["token_estimated_usd"], .0000845)

    def test_explicit_deployment_rate_used_for_returned_snapshot(self):
        summary = self.summarize(response(actual_model="gpt-6-luna-2026-11-01"))
        self.assertTrue(summary["cost"]["token_complete"])
        self.assertEqual(summary["models"][0]["pricing_model"], "gpt-6-luna")
        summary = self.summarize(response(model="my-deployment", actual_model="gpt-6-luna"),
                                 config={"models": {"my-deployment": {"input_per_million": 1, "cached_input_per_million": 1,
                                                                        "cache_write_per_million": 1, "output_per_million": 1}}})
        self.assertAlmostEqual(summary["cost"]["token_estimated_usd"], .0015)
        alias = self.summarize(response(model="my-luna", actual_model="gpt-6-luna-snapshot"),
                               config={"model_aliases": {"my-luna": "gpt-6-luna"}})
        self.assertTrue(alias["cost"]["token_complete"])
        self.assertEqual(alias["models"][0]["pricing_model"], "gpt-6-luna")

    def test_service_tier_not_silently_priced_as_standard(self):
        summary = self.summarize(response(service_tier="priority"))
        self.assertFalse(summary["cost"]["token_complete"])
        self.assertEqual(summary["cost"]["token_estimated_usd"], 0)

    def test_failed_and_unfinished_attempts_remain_billing_unknown(self):
        summary = self.summarize({"event": "attempt", "attempt_number": 1},
                                 {"event": "failure", "attempt_number": 1, "terminal": True},
                                 {"event": "attempt", "attempt_number": 2})
        self.assertEqual(summary["attempts"], 2)
        self.assertEqual(summary["failed_attempts"], 1)
        self.assertEqual(summary["unfinished_attempts"], 1)
        self.assertIsNone(summary["tokens"]["input"])
        self.assertFalse(summary["cost"]["complete"])

    def test_duplicate_response_ids_and_torn_lines(self):
        summary = self.summarize(response(), response(attempt_number=2), suffix='{"event":')
        self.assertEqual(summary["response_count"], 1)
        self.assertEqual(summary["duplicate_responses"], 1)
        self.assertEqual(summary["malformed_lines"], 1)
        self.assertAlmostEqual(summary["cost"]["token_estimated_usd"], .0003345)
        self.assertFalse(summary["cost"]["complete"])

    def test_multiple_models_and_fresh_default_config(self):
        summary = self.summarize(response(), response(response_id="resp2", model="gpt-6-sol", actual_model="gpt-6-sol"))
        self.assertEqual(len(summary["models"]), 2)
        self.assertEqual(summary["tokens"]["input"], 2000)
        pricing = default_pricing()
        pricing["models"]["gpt-6-luna"]["input_per_million"] = 999
        self.assertEqual(default_pricing()["models"]["gpt-6-luna"]["input_per_million"], .1)


class UsageInstrumentationTests(unittest.TestCase):
    def client(self, **kwargs):
        return ResponsesClient(base_url="https://demo.openai.azure.com/openai/v1", api_key="NEVER-LOG-SECRET", **kwargs)

    def test_records_actual_model_tier_cache_and_search_without_prompt(self):
        client = self.client()
        raw = {"id": "resp1", "model": "actual-model", "service_tier": "default", "status": "completed",
               "usage": {"input_tokens": 100, "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 10},
                         "output_tokens": 30, "output_tokens_details": {"reasoning_tokens": 25}},
               "tool_usage": {"web_search": {"num_requests": 2}}, "output": []}
        records = []
        with patch.object(client, "_record", side_effect=records.append), patch("path_picker.client.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())):
            client.request({"model": "deployment", "input": "NEVER-LOG-PROMPT", "tools": [{"type": "web_search"}]})
        self.assertEqual(records[-1]["actual_model"], "actual-model")
        self.assertEqual(records[-1]["provider"], "azure")
        self.assertEqual(records[-1]["cache_write_input_tokens"], 10)
        self.assertTrue(records[-1]["cache_write_included_in_input"])
        self.assertTrue(records[-1]["web_search_requested"])
        self.assertEqual(records[-1]["service_tier"], "default")
        self.assertNotIn("NEVER-LOG", json.dumps(records))

    def test_failed_retries_record_each_outcome_and_terminal_failure(self):
        client = self.client(max_retries=1)
        records = []
        with patch.object(client, "_record", side_effect=records.append), patch.object(client, "_wait"), patch("path_picker.client.urlopen", side_effect=URLError("NEVER-LOG-ERROR")):
            with self.assertRaises(RuntimeError):
                client.request({"model": "gpt-6-luna"})
        failures = [row for row in records if row["event"] == "failure"]
        self.assertEqual(len(failures), 2)
        self.assertFalse(failures[0]["terminal"])
        self.assertTrue(failures[1]["terminal"])
        self.assertNotIn("NEVER-LOG", json.dumps(records))

    def test_http_failure_records_only_status_not_body(self):
        client = self.client()
        records = []
        failure = HTTPError("https://demo.openai.azure.com", 401, "secret", {}, io.BytesIO(b"NEVER-LOG-BODY"))
        with patch.object(client, "_record", side_effect=records.append), patch("path_picker.client.urlopen", side_effect=failure):
            with self.assertRaises(RuntimeError):
                client.request({"model": "gpt-6-luna"})
        self.assertEqual(records[-1]["http_status"], 401)
        self.assertTrue(records[-1]["terminal"])
        self.assertNotIn("NEVER-LOG", json.dumps(records))

    def test_invalid_response_has_structured_terminal_failure(self):
        client = self.client()
        records = []
        with patch.object(client, "_record", side_effect=records.append), patch("path_picker.client.urlopen", return_value=io.BytesIO(b"not-json")):
            with self.assertRaises(RuntimeError):
                client.request({"model": "gpt-6-luna"})
        self.assertEqual(records[-1]["failure_kind"], "invalid_response")
        self.assertTrue(records[-1]["terminal"])
