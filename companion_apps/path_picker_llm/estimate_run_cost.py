"""Estimate Path Picker token volume from an imported inventory database.

This is an offline estimator. It tokenizes the actual BGI evidence prompts and
keeps web-search context, model reasoning/output, and claim-verification volume
as explicit scenario assumptions because those values only exist after a paid
Responses API run.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path
from statistics import mean, median
from typing import Any

from path_picker.grounding import compact_evidence
from path_picker.ingest import canonical_edge
from path_picker.prompts import edge_research_prompt, node_research_prompt, path_review_prompt
from path_picker.schemas import PATH_REVIEW_SCHEMA, VERIFY_SCHEMA, research_schema


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def stats(values: list[int]) -> dict[str, float | int]:
    return {
        "count": len(values),
        "minimum": min(values, default=0),
        "median": median(values) if values else 0,
        "mean": mean(values) if values else 0,
        "p90": percentile(values, 0.90),
        "maximum": max(values, default=0),
        "total": sum(values),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--tokenizer-path", type=Path)
    parser.add_argument("--deep-reviews", type=int, default=200)
    args = parser.parse_args()

    if args.tokenizer_path:
        sys.path.insert(0, str(args.tokenizer_path))
    try:
        import tiktoken  # type: ignore
    except ImportError as exc:
        raise SystemExit("Install tiktoken or pass --tokenizer-path") from exc
    encoding = tiktoken.get_encoding("o200k_base")

    def tokens(value: Any) -> int:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return len(encoding.encode(text))

    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    nodes = {
        str(row["node_id"]): json.loads(row["payload_json"])
        for row in connection.execute("SELECT node_id,payload_json FROM nodes ORDER BY node_id")
    }
    edges = {
        str(row["edge_id"]): json.loads(row["payload_json"])
        for row in connection.execute("SELECT edge_id,payload_json FROM edges ORDER BY edge_id")
    }

    node_prompt_tokens: list[int] = []
    node_schema_tokens = tokens(research_schema("node"))
    for identifiers in chunks(list(nodes), 6):
        items = [{"item_id": identifier, "bgi": compact_evidence(nodes[identifier])} for identifier in identifiers]
        node_prompt_tokens.append(tokens(node_research_prompt(items)) + node_schema_tokens + 30)

    edge_prompt_tokens: list[int] = []
    edge_schema_tokens = tokens(research_schema("edge"))
    for identifiers in chunks(list(edges), 3):
        items = [{"item_id": identifier, "bgi": compact_evidence(edges[identifier])} for identifier in identifiers]
        edge_prompt_tokens.append(tokens(edge_research_prompt(items)) + edge_schema_tokens + 30)

    paths = list(connection.execute(
        "SELECT * FROM paths ORDER BY primary_score DESC,rank ASC,path_id ASC"
    ))
    selected: list[sqlite3.Row] = []
    selected_ids: set[str] = set()
    queries: set[str] = set()
    for row in paths:
        if str(row["query_id"]) not in queries:
            selected.append(row)
            selected_ids.add(str(row["path_id"]))
            queries.add(str(row["query_id"]))
            if len(selected) >= args.deep_reviews:
                break
    if len(selected) < args.deep_reviews:
        for row in paths:
            if str(row["path_id"]) not in selected_ids:
                selected.append(row)
                selected_ids.add(str(row["path_id"]))
                if len(selected) >= args.deep_reviews:
                    break

    path_prompt_tokens: list[int] = []
    path_schema_tokens = tokens(PATH_REVIEW_SCHEMA)
    for row in selected:
        symbols = [value.strip() for value in str(row["path_symbols"]).split("->")]
        edge_ids = [canonical_edge(a, b) for a, b in zip(symbols, symbols[1:])]
        bundle = {
            "path_id": str(row["path_id"]),
            "path": json.loads(row["payload_json"]),
            "deterministic_score": math.log(max(float(row["primary_score"]), 1e-12)),
            "nodes": [compact_evidence(nodes[symbol]) for symbol in symbols if symbol in nodes],
            "edges": [compact_evidence(edges[identifier]) for identifier in edge_ids if identifier in edges],
            "accepted_claims": [],
        }
        path_prompt_tokens.append(tokens(path_review_prompt(bundle)) + path_schema_tokens + 30)

    verifier_fixed_tokens = tokens(VERIFY_SCHEMA) + 30
    exact_node_input = sum(node_prompt_tokens)
    exact_edge_input = sum(edge_prompt_tokens)
    exact_path_input = sum(path_prompt_tokens)
    scenarios = {
        "lean": {
            "edge_fraction_after_node_filter": 0.75,
            "web_context_tokens_per_research_call": 3000,
            "bing_transactions_per_research_call": 1,
            "research_output_tokens_per_call": 1800,
            "verified_claims_per_researched_item": 0.5,
            "verifier_excerpt_and_claim_input_tokens": 2000,
            "verifier_output_tokens_per_call": 350,
            "literature_claim_tokens_added_to_all_path_reviews": 200000,
            "path_review_output_tokens_per_call": 800,
            "synthesis_input_tokens": 100000,
            "synthesis_output_tokens": 3000,
        },
        "typical": {
            "edge_fraction_after_node_filter": 1.0,
            "web_context_tokens_per_research_call": 6000,
            "bing_transactions_per_research_call": 2,
            "research_output_tokens_per_call": 3000,
            "verified_claims_per_researched_item": 1.0,
            "verifier_excerpt_and_claim_input_tokens": 3200,
            "verifier_output_tokens_per_call": 800,
            "literature_claim_tokens_added_to_all_path_reviews": 500000,
            "path_review_output_tokens_per_call": 1200,
            "synthesis_input_tokens": 200000,
            "synthesis_output_tokens": 5000,
        },
        "heavy": {
            "edge_fraction_after_node_filter": 1.0,
            "web_context_tokens_per_research_call": 12000,
            "bing_transactions_per_research_call": 4,
            "research_output_tokens_per_call": 5000,
            "verified_claims_per_researched_item": 2.0,
            "verifier_excerpt_and_claim_input_tokens": 3300,
            "verifier_output_tokens_per_call": 1500,
            "literature_claim_tokens_added_to_all_path_reviews": 2000000,
            "path_review_output_tokens_per_call": 2500,
            "synthesis_input_tokens": 350000,
            "synthesis_output_tokens": 8000,
        },
    }
    prices = {
        "gpt-6-luna": {"input_per_million": 0.10, "output_per_million": 0.50, "long_input_per_million": 0.20, "long_output_per_million": 0.75},
        "gpt-6-sol": {"input_per_million": 2.00, "output_per_million": 10.00, "long_input_per_million": 4.00, "long_output_per_million": 15.00},
        "gpt-6-astra": {"input_per_million": 10.00, "output_per_million": 50.00, "long_input_per_million": 20.00, "long_output_per_million": 75.00},
    }
    configurations = {
        "all_luna": {"research": "gpt-6-luna", "verification": "gpt-6-luna", "path_review": "gpt-6-luna", "synthesis": "gpt-6-luna"},
        "luna_research_and_verification__sol_path_and_synthesis": {"research": "gpt-6-luna", "verification": "gpt-6-luna", "path_review": "gpt-6-sol", "synthesis": "gpt-6-sol"},
        "luna_research__sol_all_judging": {"research": "gpt-6-luna", "verification": "gpt-6-sol", "path_review": "gpt-6-sol", "synthesis": "gpt-6-sol"},
        "all_sol": {"research": "gpt-6-sol", "verification": "gpt-6-sol", "path_review": "gpt-6-sol", "synthesis": "gpt-6-sol"},
        "all_astra": {"research": "gpt-6-astra", "verification": "gpt-6-astra", "path_review": "gpt-6-astra", "synthesis": "gpt-6-astra"},
    }

    scenario_results: dict[str, Any] = {}
    for scenario_name, assumption in scenarios.items():
        edge_fraction = float(assumption["edge_fraction_after_node_filter"])
        edge_calls = math.ceil(len(edge_prompt_tokens) * edge_fraction)
        edge_items = math.ceil(len(edges) * edge_fraction)
        research_calls = len(node_prompt_tokens) + edge_calls
        verification_calls = math.ceil(
            (len(nodes) + edge_items) * float(assumption["verified_claims_per_researched_item"])
        )
        role_tokens = {
            "research": {
                "input": round(exact_node_input + exact_edge_input * edge_fraction + research_calls * float(assumption["web_context_tokens_per_research_call"])),
                "output": round(research_calls * float(assumption["research_output_tokens_per_call"])),
            },
            "verification": {
                "input": round(verification_calls * (verifier_fixed_tokens + float(assumption["verifier_excerpt_and_claim_input_tokens"]))),
                "output": round(verification_calls * float(assumption["verifier_output_tokens_per_call"])),
            },
            "path_review": {
                "input": exact_path_input + int(assumption["literature_claim_tokens_added_to_all_path_reviews"]),
                "output": len(path_prompt_tokens) * int(assumption["path_review_output_tokens_per_call"]),
            },
            "synthesis": {
                "input": int(assumption["synthesis_input_tokens"]),
                "output": int(assumption["synthesis_output_tokens"]),
            },
        }
        bing_transactions = round(research_calls * float(assumption["bing_transactions_per_research_call"]))
        bing_cost = bing_transactions * 14.0 / 1000.0
        cost_matrix: dict[str, Any] = {}
        for configuration_name, assignment in configurations.items():
            role_costs: dict[str, float] = {}
            for role, counts in role_tokens.items():
                model = assignment[role]
                # Only synthesis is a single request whose estimated input can
                # cross the published 272K long-context price threshold. Other
                # role totals are sums of many much smaller calls.
                long_context = role == "synthesis" and counts["input"] > 272000
                input_rate = prices[model]["long_input_per_million" if long_context else "input_per_million"]
                output_rate = prices[model]["long_output_per_million" if long_context else "output_per_million"]
                role_costs[role] = (
                    counts["input"] / 1_000_000 * input_rate
                    + counts["output"] / 1_000_000 * output_rate
                )
            token_cost = sum(role_costs.values())
            cost_matrix[configuration_name] = {
                "models": assignment,
                "model_token_cost_global_standard_usd": round(token_cost, 2),
                "grounding_with_bing_cost_usd": round(bing_cost, 2),
                "estimated_total_global_standard_usd": round(token_cost + bing_cost, 2),
                "estimated_total_us_data_zone_usd": round(token_cost * 1.10 + bing_cost, 2),
                "role_model_costs_usd": {key: round(value, 2) for key, value in role_costs.items()},
            }
        scenario_results[scenario_name] = {
            "assumptions": assumption,
            "derived_call_counts": {
                "research_calls": research_calls,
                "verification_calls": verification_calls,
                "deep_path_review_calls": len(path_prompt_tokens),
                "synthesis_calls": 1,
                "bing_transactions": bing_transactions,
            },
            "estimated_tokens_by_role": role_tokens,
            "costs": cost_matrix,
        }

    inventory = {
        "database": str(args.database.resolve()),
        "tokenizer": "o200k_base (offline approximation for GPT-6 family)",
        "coverage": {
            "nodes": len(nodes),
            "edges_upper_bound": len(edges),
            "paths": len(paths),
            "node_research_calls": len(node_prompt_tokens),
            "edge_research_calls_upper_bound": len(edge_prompt_tokens),
            "deep_path_review_calls": len(path_prompt_tokens),
            "synthesis_calls": 1,
        },
        "actual_bgi_prompt_tokens": {
            "node_research": stats(node_prompt_tokens),
            "edge_research_upper_bound": stats(edge_prompt_tokens),
            "deep_path_review_without_literature_claims": stats(path_prompt_tokens),
            "verifier_schema_and_framing_per_call": verifier_fixed_tokens,
        },
        "pricing_basis_usd_per_million_tokens": prices,
        "grounding_with_bing_price_usd_per_1000_transactions": 14.0,
        "scenario_estimates": scenario_results,
        "warning": "Research output, hidden reasoning, returned web context, successful claim verification, Bing request counts, and post-node-filter edge counts are unknown until a paid run; scenario values are explicit assumptions, not measured usage.",
    }
    output = json.dumps(inventory, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
