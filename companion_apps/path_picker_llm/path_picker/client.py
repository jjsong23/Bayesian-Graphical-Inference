"""Small Responses API client for Azure/OpenAI structured web research."""

from __future__ import annotations

import json
import random
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


_INCOMPLETE_REASONS = frozenset({"max_output_tokens", "content_filter"})
_RESPONSE_STATUSES = frozenset({"completed", "incomplete", "failed", "cancelled", "queued", "in_progress"})
_REASONING_EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh"})


def _reported_category(value: Any, allowed: frozenset[str]) -> str:
    """Keep provider diagnostics bounded without persisting arbitrary text."""
    if value is None:
        return "not_reported"
    return value if isinstance(value, str) and value in allowed else "unknown"


def _incomplete_reason(response: dict[str, Any]) -> str:
    details = response.get("incomplete_details")
    if details is None:
        return "not_reported"
    if not isinstance(details, dict):
        return "unknown"
    return _reported_category(details.get("reason"), _INCOMPLETE_REASONS)


def _token_count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _diagnostic_label(value: Any) -> str:
    # These are caller-specified model/schema identifiers, never response text.
    return value if isinstance(value, str) and re.fullmatch(r"[\w./:-]{1,128}", value, re.ASCII) else "unknown"


class IncompleteResponseError(RuntimeError):
    """A billed response stopped before completion; no partial JSON is usable."""

    def __init__(self, response: dict[str, Any], payload: dict[str, Any]) -> None:
        self.reason = _incomplete_reason(response)
        self.stage = _diagnostic_label(_mapping(_mapping(payload.get("text")).get("format")).get("name"))
        self.model = _diagnostic_label(payload.get("model"))
        usage = _mapping(response.get("usage"))
        self.output_tokens = _token_count(usage.get("output_tokens"))
        self.reasoning_tokens = _token_count(_mapping(usage.get("output_tokens_details")).get("reasoning_tokens"))
        self.max_output_tokens = _token_count(payload.get("max_output_tokens"))
        self.max_tool_calls = _token_count(payload.get("max_tool_calls"))
        self.output_usage = {"output_tokens": self.output_tokens, "reasoning_tokens": self.reasoning_tokens}
        self.max_limits = {"max_output_tokens": self.max_output_tokens, "max_tool_calls": self.max_tool_calls}
        self.reasoning_effort = _reported_category(_mapping(payload.get("reasoning")).get("effort"), _REASONING_EFFORTS)
        count = lambda value: str(value) if value is not None else "unknown"
        details = (f"output_tokens={count(self.output_tokens)}, reasoning_tokens={count(self.reasoning_tokens)} "
                   f"(included in output_tokens), requested max_output_tokens={count(self.max_output_tokens)}, "
                   f"requested max_tool_calls={count(self.max_tool_calls)}, reasoning_effort={self.reasoning_effort}")
        if self.reason == "max_output_tokens":
            hint = ("The output-token budget includes reasoning tokens. Review the requested output cap, "
                    "reasoning effort, or task size before explicitly resuming.")
        elif self.reason == "content_filter":
            hint = "The API reported content filtering; review the request before explicitly resuming."
        else:
            hint = "The API did not report a recognized cause; token or tool limits cannot be assumed."
        super().__init__(f"Response incomplete: reason={self.reason}; stage={self.stage}; model={self.model}; "
                         f"{details}. Usage recorded when a usage ledger is configured; no partial claims accepted. "
                         f"{hint} No automatic retry or limit increase was performed.")


def responses_url(base_url: str) -> str:
    parsed = urlsplit(base_url.strip().rstrip("/"))
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (
        host == "api.openai.com" or host.endswith(".openai.azure.com") or host.endswith(".services.ai.azure.com")
    ):
        raise ValueError("Endpoint must be an official HTTPS OpenAI or Azure OpenAI endpoint")
    path = parsed.path.rstrip("/")
    if not path or not (path.endswith("/v1") or "/openai/v1" in path or path.endswith("/responses")):
        raise ValueError("Endpoint must include /v1 or /openai/v1")
    if not path.endswith("/responses"):
        path += "/responses"
    return urlunsplit(("https", parsed.netloc, path, "", ""))


class ResponsesClient:
    def __init__(self, *, base_url: str, api_key: str, timeout: int = 300, max_retries: int = 2,
                 usage_log: str | Path | None = None, max_output_tokens: int = 8000,
                 max_tool_calls: int = 6, max_total_calls: int = 10000,
                 cancel_event: threading.Event | None = None) -> None:
        self.url = responses_url(base_url)
        self.provider = "openai" if urlsplit(self.url).hostname == "api.openai.com" else "azure"
        self.api_key = api_key.strip()
        self.timeout = int(timeout)
        self.max_retries = int(max_retries)
        self.usage_log = Path(usage_log) if usage_log else None
        self.max_output_tokens = max(16, int(max_output_tokens))
        self.max_tool_calls = max(1, int(max_tool_calls))
        self.max_total_calls = max(1, int(max_total_calls))
        self.cancel_event = cancel_event or threading.Event()
        self.attempts = 0
        if self.usage_log and self.usage_log.exists():
            with self.usage_log.open(encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    try:
                        self.attempts += int(json.loads(line).get("event") == "attempt")
                    except json.JSONDecodeError:
                        # A torn final write is conservatively counted as an attempt.
                        self.attempts += 1
        if not self.api_key:
            raise ValueError("API key is required")

    def _record(self, value: dict[str, Any]) -> None:
        if self.usage_log:
            self.usage_log.parent.mkdir(parents=True, exist_ok=True)
            # Never record prompts, response text, headers, URLs, or credentials.
            with self.usage_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), **value}) + "\n")

    def _wait(self, delay: float) -> None:
        if self.cancel_event.wait(delay):
            raise RuntimeError("Cancelled during API retry wait; completed work remains saved")

    def _record_usage(self, response: dict[str, Any], payload: dict[str, Any], attempt: int) -> None:
        usage = _mapping(response.get("usage"))
        input_details = _mapping(usage.get("input_tokens_details"))
        bing = _mapping(_mapping(response.get("tool_usage")).get("web_search")).get("num_requests")
        cache_write = input_details.get("cache_write_tokens")
        output = response.get("output")
        output_items = [item for item in output if isinstance(item, dict)] if isinstance(output, list) else []
        self._record({
            "event": "response", "attempt_number": attempt,
            "response_id": response.get("id"), "model": payload.get("model"),
            "actual_model": response.get("model"), "provider": self.provider,
            "service_tier": response.get("service_tier"),
            "requested_service_tier": payload.get("service_tier", "default"),
            **self._request_diagnostics(payload),
            "web_search_requested": self._web_search_requested(payload),
            "stage": payload.get("text", {}).get("format", {}).get("name"),
            "status": _reported_category(response.get("status"), _RESPONSE_STATUSES),
            "incomplete_reason": _incomplete_reason(response),
            "input_tokens": usage.get("input_tokens"),
            "cached_input_tokens": input_details.get("cached_tokens"),
            "cache_write_input_tokens": cache_write,
            # A nested input breakdown is a subset of input_tokens. Top-level
            # provider-specific cache counters are not assumed to be subsets.
            "cache_write_included_in_input": True if cache_write is not None else None,
            "output_tokens": usage.get("output_tokens"),
            "reasoning_tokens_included_in_output": _mapping(usage.get("output_tokens_details")).get("reasoning_tokens"),
            "bing_requests": bing,
            "billable_search_requests": bing,
            "search_usage_source": "tool_usage.web_search.num_requests" if bing is not None else None,
            "web_search_call_count": sum(item.get("type") == "web_search_call" for item in output_items),
            "openai_search_actions": sum(item.get("type") == "web_search_call" and
                                          _mapping(item.get("action")).get("type") == "search"
                                          for item in output_items),
            "billing_note": "Azure billable searches use bing_requests, never output action count; missing usage is unknown, not zero.",
        })

    @staticmethod
    def _request_diagnostics(payload: dict[str, Any]) -> dict[str, Any]:
        return {"requested_max_output_tokens": _token_count(payload.get("max_output_tokens")),
                "requested_max_tool_calls": _token_count(payload.get("max_tool_calls")),
                "requested_reasoning_effort": _reported_category(
                    _mapping(payload.get("reasoning")).get("effort"), _REASONING_EFFORTS)}

    @staticmethod
    def _web_search_requested(payload: dict[str, Any]) -> bool:
        return any(isinstance(tool, dict) and str(tool.get("type", "")).startswith("web_search")
                   for tool in payload.get("tools", []))

    def _record_failure(self, payload: dict[str, Any], attempt: int, *, kind: str,
                        terminal: bool, http_status: int | None = None) -> None:
        # Exception bodies can contain prompt text, URLs, or credentials. Keep
        # only bounded, structured diagnostic categories in the usage ledger.
        self._record({"event": "failure", "attempt_number": attempt,
                      "model": payload.get("model"), "provider": self.provider,
                      "stage": payload.get("text", {}).get("format", {}).get("name"),
                      "web_search_requested": self._web_search_requested(payload),
                      "failure_kind": kind, "http_status": http_status,
                      "terminal": terminal, "usage_unknown": True})

    @staticmethod
    def _delay(attempt: int, headers: Any = None) -> float:
        if headers is not None:
            milliseconds = headers.get("x-ms-retry-after-ms")
            seconds = headers.get("Retry-After")
            try:
                if milliseconds:
                    return max(0.0, float(milliseconds) / 1000.0)
                if seconds:
                    return max(0.0, float(seconds))
            except ValueError:
                pass
        return min(300.0, 2.0 ** attempt) * (1.0 + 0.2 * random.random())

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        for attempt in range(self.max_retries + 1):
            if self.cancel_event.is_set():
                raise RuntimeError("Cancelled before API request")
            if self.attempts >= self.max_total_calls:
                raise RuntimeError("API attempt limit reached; raise max_total_api_calls to resume. This is not a dollar cap.")
            self.attempts += 1
            attempt_number = self.attempts
            self._record({"event": "attempt", "attempt_number": attempt_number,
                          "stage": payload.get("text", {}).get("format", {}).get("name"),
                          "model": payload.get("model"), "provider": self.provider,
                          "requested_service_tier": payload.get("service_tier", "default"),
                          **self._request_diagnostics(payload),
                          "web_search_requested": self._web_search_requested(payload),
                          "retry_index": attempt})
            request = Request(
                self.url, data=body, method="POST",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - endpoint validated
                    result = json.loads(response.read().decode("utf-8"))
                if not isinstance(result, dict):
                    raise ValueError("Response must be an object")
                self._record_usage(result, payload, attempt_number)
                return result
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:4000]
                terminal = exc.code not in {408, 409, 429, 500, 502, 503, 504} or attempt >= self.max_retries
                self._record_failure(payload, attempt_number, kind="http", terminal=terminal, http_status=exc.code)
                if terminal:
                    raise RuntimeError(f"Responses API HTTP {exc.code}: {detail}") from exc
                self._wait(self._delay(attempt, exc.headers))
            except (URLError, TimeoutError, ConnectionError) as exc:
                self._record_failure(payload, attempt_number, kind="connection", terminal=attempt >= self.max_retries)
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Responses API connection failed after {attempt + 1} attempts: {exc}") from exc
                self._wait(self._delay(attempt))
            except (ValueError, UnicodeError) as exc:
                self._record_failure(payload, attempt_number, kind="invalid_response", terminal=True)
                raise RuntimeError("Responses API returned an unreadable response; billing usage is unknown") from exc
        raise RuntimeError("Responses API request exhausted retries")

    @staticmethod
    def output_text(response: dict[str, Any]) -> str:
        output: list[str] = []
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    output.append(str(content["text"]))
        if not output:
            raise RuntimeError(f"Response had no output text (status={response.get('status')!r})")
        return "".join(output)

    @staticmethod
    def source_urls(response: dict[str, Any]) -> set[str]:
        urls: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, dict):
                for key, child in value.items():
                    if key in {"url", "link"} and isinstance(child, str) and child.startswith(("http://", "https://")):
                        urls.add(child)
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        # Do not trust URLs echoed in generated JSON as proof a source was visited.
        for item in response.get("output", []):
            if item.get("type") == "web_search_call":
                walk(item.get("action", {}))
            elif item.get("type") == "message":
                for content in item.get("content", []):
                    for annotation in content.get("annotations", []):
                        if annotation.get("type") == "url_citation":
                            walk(annotation)
        return urls

    def structured(
        self, *, model: str, prompt: str, schema_name: str, schema: dict[str, Any],
        reasoning_effort: str = "high", web_search: bool = False,
        require_web_search: bool = False, search_context_size: str = "high",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": model,
            "max_output_tokens": self.max_output_tokens,
            "store": False,
            "input": [{"role": "user", "content": prompt}],
            "reasoning": {"effort": reasoning_effort},
            "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        }
        if web_search:
            payload["max_tool_calls"] = self.max_tool_calls
            payload["tools"] = [{"type": "web_search", "search_context_size": search_context_size}]
            payload["include"] = ["web_search_call.action.sources"]
            if require_web_search:
                payload["tool_choice"] = "required"
        response = self.request(payload)
        if response.get("status") == "incomplete":
            raise IncompleteResponseError(response, payload)
        if response.get("status") != "completed":
            status = _reported_category(response.get("status"), _RESPONSE_STATUSES)
            raise RuntimeError(f"Response not completed (status={status}); usage recorded when a usage ledger "
                               "is configured, no partial claims accepted. No automatic retry was performed.")
        if require_web_search and not any(item.get("type") == "web_search_call" for item in response.get("output", [])):
            raise RuntimeError("Required hosted web search was not performed")
        try:
            result = json.loads(self.output_text(response))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Structured response was not valid JSON: {exc}") from exc
        return result, response


def citation_ids(text: str) -> set[str]:
    return set(re.findall(r"\[(CLM-[0-9A-Fa-f-]+)\]", text))

