"""Minimal Azure/OpenAI Responses API client with deterministic tool dispatch."""

from __future__ import annotations

import json
import random
import re
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .retrieval import RetrievalSession, TOOL_DEFINITIONS


def responses_url(base_url: str) -> str:
    raw = base_url.strip().rstrip("/")
    parsed = urlsplit(raw)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme != "https" or not (
        host == "api.openai.com"
        or host.endswith(".openai.azure.com")
        or host.endswith(".services.ai.azure.com")
    ):
        raise ValueError("base_url must be an official HTTPS OpenAI or Azure OpenAI endpoint")
    path = parsed.path.rstrip("/")
    if not path:
        raise ValueError("base_url must include /v1 or /openai/v1")
    if not path.endswith("/responses"):
        path += "/responses"
    return urlunsplit(("https", parsed.netloc, path, "", ""))


class ResponsesClient:
    RETRYABLE_HTTP_CODES = {408, 409, 429, 500, 502, 503, 504}

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: int = 300,
        max_retries: int = 12,
        max_content_retries: int = 2,
        retry_base_seconds: float = 2.0,
        retry_max_seconds: float = 300.0,
        rate_limit_base_seconds: float = 15.0,
        max_retry_elapsed_seconds: float = 1800.0,
    ) -> None:
        self.url = responses_url(base_url)
        self.api_key = api_key.strip()
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.max_content_retries = max(0, int(max_content_retries))
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self.retry_max_seconds = max(self.retry_base_seconds, float(retry_max_seconds))
        self.rate_limit_base_seconds = max(self.retry_base_seconds, float(rate_limit_base_seconds))
        self.max_retry_elapsed_seconds = max(0.0, float(max_retry_elapsed_seconds))
        self._cooldown_lock = threading.Lock()
        self._cooldown_until = 0.0
        self._request_slot_lock = threading.Lock()
        self._next_request_at = 0.0
        self._adaptive_request_interval = 0.0
        if not self.api_key:
            raise ValueError("An API key is required")

    def _retry_delay(self, attempt: int, headers: Any = None) -> float:
        if headers is not None:
            retry_after_ms = headers.get("x-ms-retry-after-ms")
            if retry_after_ms:
                try:
                    return max(0.0, float(retry_after_ms) / 1000.0)
                except (TypeError, ValueError):
                    pass
            retry_after = headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except (TypeError, ValueError):
                    pass
            reset_tokens = headers.get("x-ratelimit-reset-tokens")
            if reset_tokens:
                parsed = self._duration_seconds(str(reset_tokens))
                if parsed is not None:
                    return parsed
        delay = min(self.retry_max_seconds, self.retry_base_seconds * (2 ** attempt))
        return delay * (1.0 + 0.25 * random.random()) if delay else 0.0

    @staticmethod
    def _duration_seconds(value: str) -> float | None:
        """Parse rate-limit reset durations such as ``1m30s`` or ``250ms``."""

        text = value.strip().casefold()
        if not text:
            return None
        matches = re.findall(r"([0-9]+(?:\.[0-9]+)?)(ms|s|m|h)", text)
        if not matches or "".join(number + unit for number, unit in matches) != text:
            return None
        scales = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
        return sum(float(number) * scales[unit] for number, unit in matches)

    def _rate_limit_delay(self, attempt: int, headers: Any = None) -> float:
        hinted = self._retry_delay(attempt, headers)
        fallback = min(self.retry_max_seconds, self.rate_limit_base_seconds * (2 ** attempt))
        delay = max(hinted, fallback)
        return delay * (1.0 + 0.15 * random.random())

    @staticmethod
    def _is_retryable_429(detail: str) -> bool:
        """Do not loop on billing/quota failures that require human action."""

        try:
            error = json.loads(detail).get("error", {})
        except (json.JSONDecodeError, AttributeError):
            return True
        code = str(error.get("code", "")).casefold()
        return code not in {
            "credit_balance_exhausted",
            "organization_spend_limit_exceeded",
            "insufficient_quota",
            "billing_hard_limit_reached",
        }

    def _wait_for_shared_cooldown(self) -> None:
        """Make parallel workers respect the longest active server cooldown."""

        with self._cooldown_lock:
            delay = self._cooldown_until - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def _publish_shared_cooldown(self, delay: float) -> None:
        with self._cooldown_lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + max(0.0, delay))

    def _wait_for_request_slot(self) -> None:
        """Stagger worker wakeups after a 429 instead of releasing a new burst."""

        with self._request_slot_lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + self._adaptive_request_interval
        if delay:
            time.sleep(delay)

    def _tighten_rate_limit(self) -> None:
        with self._request_slot_lock:
            self._adaptive_request_interval = min(
                15.0, max(1.0, self._adaptive_request_interval * 2.0)
            )

    def _relax_rate_limit(self) -> None:
        with self._request_slot_lock:
            if self._adaptive_request_interval:
                self._adaptive_request_interval = max(
                    0.25, self._adaptive_request_interval * 0.9
                )

    def request(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None
        retry_started = time.monotonic()
        for attempt in range(self.max_retries + 1):
            self._wait_for_shared_cooldown()
            self._wait_for_request_slot()
            request = Request(
                self.url,
                data=encoded,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - validated URL
                    result = json.loads(response.read().decode("utf-8"))
                    self._relax_rate_limit()
                    return result
            except HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:4000]
                retryable = exc.code in self.RETRYABLE_HTTP_CODES
                if exc.code == 429:
                    retryable = self._is_retryable_429(detail)
                if not retryable or attempt >= self.max_retries:
                    raise RuntimeError(f"Responses API HTTP {exc.code}: {detail}") from exc
                last_error = exc
                if exc.code == 429:
                    self._tighten_rate_limit()
                    delay = self._rate_limit_delay(attempt, exc.headers)
                else:
                    delay = self._retry_delay(attempt, exc.headers)
                if (
                    self.max_retry_elapsed_seconds
                    and time.monotonic() - retry_started + delay > self.max_retry_elapsed_seconds
                ):
                    raise RuntimeError(
                        f"Responses API HTTP {exc.code} remained retryable beyond "
                        f"the {self.max_retry_elapsed_seconds:g}-second recovery window: {detail}"
                    ) from exc
                self._publish_shared_cooldown(delay)
                time.sleep(delay)
            except (URLError, TimeoutError, ConnectionError) as exc:
                if attempt >= self.max_retries:
                    raise RuntimeError(
                        f"Responses API connection failed after {attempt + 1} attempts: {exc}"
                    ) from exc
                last_error = exc
                time.sleep(self._retry_delay(attempt))
        raise RuntimeError(f"Responses API request failed after retries: {last_error}")

    @staticmethod
    def output_text(response: dict[str, Any]) -> str:
        texts: list[str] = []
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") in {"output_text", "text"}:
                    text = str(content.get("text", ""))
                    if text.strip():
                        texts.append(text)
        if texts:
            return "".join(texts)
        details = {
            "response_id": response.get("id"),
            "status": response.get("status"),
            "incomplete_details": response.get("incomplete_details"),
            "error": response.get("error"),
            "output_types": [item.get("type") for item in response.get("output", [])],
        }
        raise RuntimeError(
            "Response contained no non-empty output text: "
            + json.dumps(details, ensure_ascii=False)
        )

    def _structured_result(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Retry successful HTTP responses that contain no valid JSON result."""

        last_error: Exception | None = None
        for attempt in range(self.max_content_retries + 1):
            response = self.request(payload)
            try:
                return json.loads(self.output_text(response)), response
            except (json.JSONDecodeError, RuntimeError) as exc:
                last_error = exc
                if attempt >= self.max_content_retries:
                    raise RuntimeError(
                        "Responses API returned no valid structured output after "
                        f"{attempt + 1} successful HTTP responses: {exc}"
                    ) from exc
                time.sleep(self._retry_delay(attempt))
        raise RuntimeError(f"Responses API structured-output recovery failed: {last_error}")

    def structured(
        self,
        *,
        model: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        reasoning_effort: str,
        temperature: float | None = None,
        web_search: bool = False,
        search_context_size: str = "low",
        require_web_search: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        conversation: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {
            "model": model,
            "store": False,
            "input": conversation,
            "reasoning": {"effort": reasoning_effort},
            "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if web_search:
            if search_context_size not in {"low", "medium", "high"}:
                raise ValueError("search_context_size must be low, medium, or high")
            payload["tools"] = [{"type": "web_search", "search_context_size": search_context_size}]
            payload["include"] = ["web_search_call.action.sources"]
            if require_web_search:
                # With web_search as the only tool, required guarantees that candidate
                # generation actually consults the web rather than merely having the
                # option available.
                payload["tool_choice"] = "required"
        return self._structured_result(payload)

    def tool_structured(
        self,
        *,
        model: str,
        prompt: str,
        schema_name: str,
        schema: dict[str, Any],
        reasoning_effort: str,
        retrieval: RetrievalSession,
        max_tool_rounds: int = 24,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        conversation: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
        payload: dict[str, Any] = {
            "model": model,
            "store": False,
            "input": conversation,
            "reasoning": {"effort": reasoning_effort},
            "tools": TOOL_DEFINITIONS,
            "tool_choice": "auto",
            "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
        }
        responses: list[dict[str, Any]] = []
        for _ in range(max_tool_rounds):
            if cancel_requested and cancel_requested():
                raise RuntimeError("Ground-truth curation cancelled by user")
            response = self.request(payload)
            responses.append(response)
            calls = [item for item in response.get("output", []) if item.get("type") == "function_call"]
            if not calls:
                return json.loads(self.output_text(response)), responses
            outputs = []
            for call in calls:
                if cancel_requested and cancel_requested():
                    raise RuntimeError("Ground-truth curation cancelled by user")
                arguments = json.loads(call.get("arguments") or "{}")
                try:
                    result = retrieval.call(str(call.get("name", "")), arguments)
                except Exception as exc:  # tool errors are returned for model recovery and logged
                    result = {"error": str(exc)}
                outputs.append({
                    "type": "function_call_output",
                    "call_id": call.get("call_id"),
                    "output": json.dumps(result, ensure_ascii=False),
                })
            conversation.extend(response.get("output", []))
            conversation.extend(outputs)
            payload = {
                "model": model,
                "store": False,
                "input": conversation,
                "reasoning": {"effort": reasoning_effort},
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto",
                "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
            }
        raise RuntimeError(f"Validator exceeded {max_tool_rounds} tool rounds")
