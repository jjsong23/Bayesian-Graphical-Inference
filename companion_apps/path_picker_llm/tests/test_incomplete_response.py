from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from path_picker.client import IncompleteResponseError, ResponsesClient


class IncompleteResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.log = Path(self.folder.name) / "usage.jsonl"
        self.client = ResponsesClient(
            base_url="https://api.openai.com/v1", api_key="NEVER-LOG-API-KEY",
            usage_log=self.log, max_output_tokens=8000, max_tool_calls=6, max_retries=2,
        )

    @staticmethod
    def response(reason: str = "max_output_tokens", status: str = "incomplete") -> dict:
        return {
            "status": status,
            "incomplete_details": {"reason": reason},
            "usage": {"input_tokens": 100, "output_tokens": 7909,
                      "output_tokens_details": {"reasoning_tokens": 7079}},
            # Even syntactically valid JSON must not be accepted before completed.
            "output": [{"type": "message", "content": [{"type": "output_text",
                        "text": '{"claim":"NEVER-LOG-PARTIAL-RESPONSE"}'}]}],
        }

    def structured(self, **kwargs):
        return self.client.structured(
            model="test-model", prompt="NEVER-LOG-USER-PROMPT", schema_name="research",
            schema={}, reasoning_effort="high", **kwargs,
        )

    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def test_token_limit_reason_usage_and_requested_limits_are_preserved(self) -> None:
        raw = self.response()
        raw["output"] += [
            {"type": "web_search_call", "action": {"type": "search"}},
            {"type": "web_search_call", "action": {"type": "open_page"}},
            {"type": "web_search_call", "action": {"type": "find_in_page"}},
        ]
        with patch("path_picker.client.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())) as request:
            with patch.object(self.client, "output_text") as parse_partial:
                with self.assertRaises(IncompleteResponseError) as raised:
                    self.structured(web_search=True, require_web_search=True)
                # The ledger is already complete at the point the caller sees the error.
                records = self.records()
                self.assertEqual([row["event"] for row in records], ["attempt", "response"])
                parse_partial.assert_not_called()
        request.assert_called_once()
        error = raised.exception
        self.assertEqual(error.reason, "max_output_tokens")
        self.assertEqual(error.stage, "research")
        self.assertEqual(error.model, "test-model")
        self.assertEqual(error.output_tokens, 7909)
        self.assertEqual(error.reasoning_tokens, 7079)
        self.assertEqual(error.output_usage, {"output_tokens": 7909, "reasoning_tokens": 7079})
        self.assertEqual(error.max_limits, {"max_output_tokens": 8000, "max_tool_calls": 6})
        self.assertIn("reason=max_output_tokens", str(error))
        self.assertIn("includes reasoning tokens", str(error))
        self.assertIn("7079", str(error))
        for row in records:
            self.assertEqual(row["requested_max_output_tokens"], 8000)
            self.assertEqual(row["requested_max_tool_calls"], 6)
            self.assertEqual(row["requested_reasoning_effort"], "high")
        self.assertEqual(records[-1]["incomplete_reason"], "max_output_tokens")
        self.assertEqual(records[-1]["reasoning_tokens_included_in_output"], 7079)
        self.assertEqual(records[-1]["web_search_call_count"], 3)
        self.assertEqual(records[-1]["openai_search_actions"], 1)
        self.assertIsNone(records[-1]["billable_search_requests"])
        sent = json.loads(request.call_args.args[0].data)
        self.assertEqual(sent["max_output_tokens"], 8000)
        self.assertEqual(sent["max_tool_calls"], 6)
        self.assertEqual(self.client.max_output_tokens, 8000)
        self.assertEqual(self.client.max_tool_calls, 6)
        self.assertEqual(self.client.attempts, 1)

    def test_content_filter_has_precise_guidance_not_token_limit_advice(self) -> None:
        with patch("path_picker.client.urlopen", return_value=io.BytesIO(json.dumps(self.response("content_filter")).encode())):
            with self.assertRaises(IncompleteResponseError) as raised:
                self.structured()
        self.assertEqual(raised.exception.reason, "content_filter")
        self.assertIn("API reported content filtering", str(raised.exception))
        self.assertNotIn("Review the requested output cap", str(raised.exception))
        self.assertEqual(self.records()[-1]["incomplete_reason"], "content_filter")

    def test_missing_or_unknown_reasons_are_not_invented_from_usage(self) -> None:
        cases = [
            (None, "not_reported"), ({}, "not_reported"),
            ({"reason": None}, "not_reported"),
            ({"reason": "max_tool_calls"}, "unknown"),
            ({"reason": "future_reason"}, "unknown"),
            ({"reason": {"private": "NEVER-LOG-DETAIL"}}, "unknown"),
            ("NEVER-LOG-DETAIL", "unknown"),
        ]
        for details, expected in cases:
            with self.subTest(details=details):
                raw = self.response()
                raw["incomplete_details"] = details
                with patch("path_picker.client.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())) as request:
                    with self.assertRaises(IncompleteResponseError) as raised:
                        self.structured()
                request.assert_called_once()
                self.assertEqual(raised.exception.reason, expected)
                self.assertIn("token or tool limits cannot be assumed", str(raised.exception))
                self.assertEqual(self.records()[-1]["incomplete_reason"], expected)

    def test_missing_details_key_has_not_reported_reason(self) -> None:
        raw = self.response()
        del raw["incomplete_details"]
        with patch("path_picker.client.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())):
            with self.assertRaises(IncompleteResponseError) as raised:
                self.structured()
        self.assertEqual(raised.exception.reason, "not_reported")

    def test_all_noncompleted_statuses_reject_valid_partial_json_without_retry(self) -> None:
        for status in ("failed", "cancelled", "queued", "in_progress", "future_status", None):
            with self.subTest(status=status):
                raw = self.response(status=status)
                with patch("path_picker.client.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())) as request:
                    with patch.object(self.client, "output_text") as parse_partial:
                        with self.assertRaisesRegex(RuntimeError, "no partial claims accepted"):
                            self.structured()
                        parse_partial.assert_not_called()
                request.assert_called_once()
                self.assertEqual(self.records()[-1]["event"], "response")

    def test_completed_response_still_parses_and_unsent_tool_cap_is_unknown(self) -> None:
        raw = self.response(status="completed")
        raw["incomplete_details"] = None
        with patch("path_picker.client.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())):
            result, returned = self.structured()
        self.assertEqual(result, {"claim": "NEVER-LOG-PARTIAL-RESPONSE"})
        self.assertEqual(returned, raw)
        for row in self.records():
            self.assertIsNone(row["requested_max_tool_calls"])
        self.assertEqual(self.records()[-1]["incomplete_reason"], "not_reported")

    def test_raw_details_errors_prompts_and_output_are_never_logged(self) -> None:
        secret_detail = "NEVER-LOG-DETAIL" * 1000
        raw = self.response(secret_detail)
        raw["incomplete_details"]["message"] = "NEVER-LOG-DETAIL-MESSAGE"
        raw["error"] = {"message": "NEVER-LOG-RAW-ERROR", "code": "NEVER-LOG-ERROR-CODE"}
        with patch("path_picker.client.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())):
            with self.assertRaises(IncompleteResponseError) as raised:
                self.structured()
        log_text = self.log.read_text(encoding="utf-8")
        self.assertNotIn("NEVER-LOG", log_text)
        self.assertNotIn("NEVER-LOG", str(raised.exception))
        self.assertLess(len(log_text), 4000)
        self.assertEqual(raised.exception.reason, "unknown")

    def test_malformed_usage_does_not_mask_incomplete_reason(self) -> None:
        raw = self.response()
        raw["usage"] = {"output_tokens": None, "output_tokens_details": "unavailable"}
        raw["output"] = None
        with patch("path_picker.client.urlopen", return_value=io.BytesIO(json.dumps(raw).encode())):
            with self.assertRaises(IncompleteResponseError) as raised:
                self.structured()
        self.assertEqual(raised.exception.reason, "max_output_tokens")
        self.assertIsNone(raised.exception.output_tokens)
        self.assertIsNone(raised.exception.reasoning_tokens)
        self.assertIn("output_tokens=unknown", str(raised.exception))
        self.assertEqual(self.records()[-1]["event"], "response")


if __name__ == "__main__":
    unittest.main()
