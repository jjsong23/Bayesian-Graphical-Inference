"""Read-only estimates from the append-only, secret-free API usage ledger.

``summarize_usage(path, config)`` accepts a full run configuration (``pricing``
key) or a pricing object. It never submits API requests. ``tokens`` are sums of
reported fields; a field is ``None`` when responses exist but no value is known.
``cost.known_estimated_usd`` is the subtotal of priceable components, NOT a bill
or an upper bound. ``cost.complete`` means the ledger has no unknown components
or outcomes, not that provider invoices are final. Each ``models`` row has the
same tokens/cost structures plus model, pricing_model and response_count.
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


_TOKEN_FIELDS = ("input", "cached_input", "cache_write", "uncached_input", "output", "reasoning_included_in_output")


def default_pricing() -> dict[str, Any]:
    """Return a fresh editable standard-tier reference card, denominated in USD.

    Explicit null rates stay unknown. No dated model or deployment aliases are
    guessed; set ``model_aliases`` or add exact names to ``models``. Optional
    per-model ``service_tiers`` may supply rates for a nondefault tier. Set
    ``cache_write_separately_billed=False`` only after checking the deployment's
    policy; otherwise missing cache-write usage remains unknown.
    """
    return {
        "currency": "USD", "checked_at": "2026-11-09",
        "models": {
            "gpt-6-luna": {"input_per_million": .10, "cached_input_per_million": .01,
                           "cache_write_per_million": .125, "output_per_million": .50,
                           "cache_write_separately_billed": True},
            "gpt-6-sol": {"input_per_million": 2., "cached_input_per_million": .20,
                          "cache_write_per_million": 2.50, "output_per_million": 10.,
                          "cache_write_separately_billed": True},
        },
        "model_aliases": {},
        "long_context": {"threshold_input_tokens": 272000, "input_multiplier": 2., "output_multiplier": 1.5},
        "search": {"azure_per_thousand": 14., "azure_rate_verified": False,
                   "openai_per_thousand": 10., "openai_rate_verified": False},
        "sources": [
            "https://developers.openai.com/api/docs/models/gpt-6-luna",
            "https://developers.openai.com/api/docs/models/gpt-6-sol",
            "https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api",
            "https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/web-search",
        ],
    }


def _merge(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _provider(config: dict[str, Any]) -> str | None:
    host = (urlsplit(str(config.get("api_base_url", ""))).hostname or "").casefold()
    if host == "api.openai.com":
        return "openai"
    if host.endswith((".openai.azure.com", ".services.ai.azure.com")):
        return "azure"
    return None


def _bucket() -> dict[str, Any]:
    return {"response_count": 0, "tokens": {field: None for field in _TOKEN_FIELDS},
            "cost": {"known_estimated_usd": 0., "token_estimated_usd": 0.,
                     "search_estimated_usd": 0., "complete": True,
                     "token_complete": True, "search_complete": True}}


def summarize_usage(log_path: str | Path, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Summarize one live JSONL snapshot; torn lines are reported, never fatal.

    Top-level keys also include attempts/failed_attempts/unfinished_attempts,
    duplicate_responses, malformed_lines, log_exists, last_event_at, search
    counters, warnings (strings), billing_caveats (strings), and pricing. Search
    action counts are observational only, never used as billable transactions.
    Azure reference search estimates remain flagged unverified. OpenAI search
    is unpriced unless both a reported transaction count and an explicitly
    verified OpenAI rate are present. Failed requests may still incur charges.
    """
    config = config if isinstance(config, dict) else {}
    overrides = config.get("pricing", config)
    pricing = default_pricing()
    if isinstance(overrides, dict):
        _merge(pricing, {key: value for key, value in overrides.items() if key in pricing})
    log_path = Path(log_path)
    result = {
        "schema_version": 1, "currency": "USD", "log_exists": log_path.exists(), "last_event_at": None,
        "attempts": 0, "failed_attempts": 0, "unfinished_attempts": 0,
        "duplicate_responses": 0, "malformed_lines": 0, "missing_usage_responses": 0,
        **_bucket(), "search": {"reported_requests": 0, "observed_actions": 0, "unknown_response_count": 0},
        "models": [], "warnings": [], "pricing": pricing,
        "billing_caveats": [
            "Estimate only, not the final provider bill or a spending cap. Unknown charges are excluded from the known subtotal.",
            "Reference token rates are standard-tier rates. Azure region/deployment, processing tier, negotiated rates, taxes and other services can change the bill.",
            "Reasoning tokens are already included in output; cached reads and cache writes are separated from ordinary input, never added twice.",
            "Long-context multipliers apply per request above 272,000 input tokens, not to cumulative run tokens.",
            "Azure searches use tool_usage.web_search.num_requests. Output search actions are not billable transaction counts. The $14/1,000 Azure rate is an unverified editable estimate.",
            "In-flight requests, retries, interrupted responses and failures may incur charges even when usage is missing.",
        ],
    }
    warnings: set[str] = set()
    attempts: set[Any] = set()
    outcomes: set[Any] = set()
    failures: set[Any] = set()
    responses: list[dict[str, Any]] = []
    response_ids: set[str] = set()
    try:
        if result["log_exists"]:
            with log_path.open(encoding="utf-8") as handle:
                for index, line in enumerate(handle):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                        if not isinstance(row, dict):
                            raise ValueError("not an object")
                    except (ValueError, TypeError):
                        result["malformed_lines"] += 1
                        continue
                    if isinstance(row.get("at"), str):
                        result["last_event_at"] = row["at"]
                    attempt = row.get("attempt_number")
                    attempt = attempt if type(attempt) is int else ("line", index)
                    if row.get("event") == "attempt":
                        attempts.add(attempt)
                    elif row.get("event") in {"failure", "attempt_failure"}:
                        failures.add(attempt)
                        outcomes.add(attempt)
                    elif row.get("event") == "response":
                        outcomes.add(attempt)
                        response_id = row.get("response_id")
                        if isinstance(response_id, str) and response_id:
                            if response_id in response_ids:
                                result["duplicate_responses"] += 1
                                continue
                            response_ids.add(response_id)
                        responses.append(row)
    except (OSError, UnicodeError):
        warnings.add("Usage log could not be fully read; billing usage is unknown.")
        result["cost"]["complete"] = False
        result["cost"]["token_complete"] = False
        result["cost"]["search_complete"] = False
    result["attempts"] = len(attempts)
    result["failed_attempts"] = len(failures)
    result["unfinished_attempts"] = len(attempts - outcomes)
    for count, label in ((result["unfinished_attempts"], "attempt(s) have no recorded outcome; usage is unknown, not free"),
                         (result["failed_attempts"], "attempt(s) failed without usage; charges are unknown"),
                         (result["malformed_lines"], "malformed or partial ledger line(s); usage may be missing")):
        if count:
            warnings.add(f"{count} {label}.")
            result["cost"]["complete"] = False
            result["cost"]["token_complete"] = False
            result["cost"]["search_complete"] = False
    model_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in responses:
        model = str(row.get("actual_model") or row.get("model") or "unknown")
        requested_model = str(row.get("model") or "")
        aliases = pricing.get("model_aliases") or {}
        models = pricing.get("models") or {}
        # Deployment prices explicitly configured under the requested name are
        # authoritative even if the provider reports a dated model snapshot.
        requested_configured = ((isinstance(models, dict) and requested_model in models)
                                or (isinstance(aliases, dict) and requested_model in aliases))
        price_name = requested_model if requested_configured else model
        pricing_model = aliases.get(price_name, price_name) if isinstance(aliases, dict) else price_name
        if not isinstance(pricing_model, str):
            pricing_model = price_name
        tier = str(row.get("service_tier") or row.get("requested_service_tier") or "default")
        group = model_rows.setdefault((model, pricing_model, tier), {
            "model": model, "requested_model": requested_model, "pricing_model": pricing_model,
            "service_tier": tier, **_bucket()})
        group["response_count"] += 1
        rates = models.get(pricing_model, {}) if isinstance(models, dict) else {}
        rates = rates if isinstance(rates, dict) else {}
        complete = True
        if not rates:
            warnings.add(f"No pricing for model/deployment '{model}'; configure its exact name or an explicit alias.")
            complete = False
        if tier not in {"default", "standard"}:
            tiers = rates.get("service_tiers") or {}
            tier_rates = tiers.get(tier) if isinstance(tiers, dict) else None
            if isinstance(tier_rates, dict):
                rates = {**rates, **tier_rates}
            else:
                warnings.add(f"No pricing for service tier '{tier}' on '{model}'.")
                rates = {}
                complete = False
        if not row.get("service_tier"):
            warnings.add("Some responses omit the actual service tier; requested/default-tier reference rates are assumed.")
        if not row.get("actual_model"):
            warnings.add("Some responses omit the actual model; the requested model/deployment name is used without alias inference.")
        elif pricing_model != model:
            warnings.add(f"Actual model '{model}' is estimated with the explicitly configured deployment/alias rate '{pricing_model}'.")
        token = {
            "input": _count(row.get("input_tokens")), "cached_input": _count(row.get("cached_input_tokens")),
            "cache_write": _count(row.get("cache_write_input_tokens")),
            "output": _count(row.get("output_tokens")),
            "reasoning_included_in_output": _count(row.get("reasoning_tokens_included_in_output")),
            "uncached_input": None,
        }
        if token["input"] == 0:
            token["cached_input"] = token["cached_input"] if token["cached_input"] is not None else 0
            token["cache_write"] = token["cache_write"] if token["cache_write"] is not None else 0
        if rates.get("cache_write_separately_billed") is False and token["cache_write"] is None:
            token["cache_write"] = 0
        if token["input"] is None or token["output"] is None:
            result["missing_usage_responses"] += 1
            warnings.add(f"Missing or invalid token usage for '{model}'; missing tokens are unknown, not zero.")
            complete = False
        valid_split = all(token[key] is not None for key in ("input", "cached_input", "cache_write"))
        if token["cache_write"] and row.get("cache_write_included_in_input") is not True:
            warnings.add(f"Cache-write accounting for '{model}' does not state whether tokens are included in input; input pricing is unknown.")
            valid_split = False
        if valid_split:
            ordinary = token["input"] - token["cached_input"] - token["cache_write"]
            if ordinary < 0:
                valid_split = False
                warnings.add(f"Overlapping or invalid cache token counts for '{model}'; input pricing is unknown.")
            else:
                token["uncached_input"] = ordinary
        if not valid_split:
            warnings.add(f"Input/cache usage breakdown is missing or ambiguous for '{model}'; no input charge is inferred.")
            complete = False
        if token["reasoning_included_in_output"] is not None and token["output"] is not None and token["reasoning_included_in_output"] > token["output"]:
            warnings.add(f"Reasoning count exceeds output for '{model}'; reported output total is used only once.")
            complete = False
        long = pricing.get("long_context") or {}
        threshold = _number(long.get("threshold_input_tokens")) if isinstance(long, dict) else None
        long_request = token["input"] is not None and threshold is not None and token["input"] > threshold
        input_factor = _number(long.get("input_multiplier")) if long_request else 1.
        output_factor = _number(long.get("output_multiplier")) if long_request else 1.
        if token["input"] is None or threshold is None:
            output_factor = None  # Cannot decide whether the long-context rate applies.
        token_cost = 0.
        components = [("output", "output_per_million", output_factor)]
        if valid_split:
            components += [("uncached_input", "input_per_million", input_factor),
                           ("cached_input", "cached_input_per_million", input_factor),
                           ("cache_write", "cache_write_per_million", input_factor)]
        for field, rate_name, factor in components:
            count = token[field]
            rate = _number(rates.get(rate_name))
            if count == 0:
                continue
            if count is None or rate is None or factor is None:
                complete = False
                warnings.add(f"Unknown {field.replace('_', ' ')} charge for '{model}': usage or applicable rate is missing.")
            else:
                token_cost += count * rate * factor / 1_000_000
        token_complete = complete
        provider = row.get("provider") or _provider(config)
        search_count = _count(row.get("billable_search_requests", row.get("bing_requests")))
        observed = _count(row.get("openai_search_actions")) or 0
        result["search"]["observed_actions"] += observed
        search_cost = 0.
        search_unknown = False
        if search_count is None and row.get("web_search_requested") is False and observed == 0:
            search_count = 0
        if search_count is not None:
            result["search"]["reported_requests"] += search_count
        if search_count == 0:
            pass
        elif search_count is None:
            search_unknown = True
        else:
            search_rates = pricing.get("search") or {}
            rate = _number(search_rates.get(f"{provider}_per_thousand")) if isinstance(search_rates, dict) else None
            if provider == "azure" and rate is not None:
                search_cost = search_count * rate / 1000
                if search_rates.get("azure_rate_verified") is not True:
                    warnings.add("Azure search cost uses an unverified reference rate; confirm your deployment's billable search price.")
            elif provider == "openai" and rate is not None and search_rates.get("openai_rate_verified") is True and row.get("search_usage_source") == "tool_usage.web_search.num_requests":
                search_cost = search_count * rate / 1000
            else:
                search_unknown = True
        if search_unknown:
            result["search"]["unknown_response_count"] += 1
            warnings.add("Search charges are unknown for some responses: billable usage, provider, or verified applicable pricing is missing; output actions are not substituted.")
            complete = False
        for key, value in token.items():
            if value is not None:
                group["tokens"][key] = (group["tokens"][key] or 0) + value
        group["cost"]["token_estimated_usd"] += token_cost
        group["cost"]["search_estimated_usd"] += search_cost
        group["cost"]["known_estimated_usd"] += token_cost + search_cost
        group["cost"]["complete"] = group["cost"]["complete"] and complete
        group["cost"]["token_complete"] = group["cost"]["token_complete"] and token_complete
        group["cost"]["search_complete"] = group["cost"]["search_complete"] and not search_unknown
    for model in sorted(model_rows):
        group = model_rows[model]
        result["models"].append(group)
        result["response_count"] += group["response_count"]
        for key, value in group["tokens"].items():
            if value is not None:
                result["tokens"][key] = (result["tokens"][key] or 0) + value
        for key in ("known_estimated_usd", "token_estimated_usd", "search_estimated_usd"):
            result["cost"][key] += group["cost"][key]
        result["cost"]["complete"] = result["cost"]["complete"] and group["cost"]["complete"]
        result["cost"]["token_complete"] = result["cost"]["token_complete"] and group["cost"]["token_complete"]
        result["cost"]["search_complete"] = result["cost"]["search_complete"] and group["cost"]["search_complete"]
    if not result["response_count"] and not result["attempts"] and not result["malformed_lines"]:
        result["tokens"] = {key: 0 for key in _TOKEN_FIELDS}
    result["warnings"] = sorted(warnings)
    return result
