"""Reproducible OFFLINE path-first cost model; never calls a paid API.

Tokenize every saved path and its complete compact per-stream evidence. Adaptive
search decisions and generated/reasoning tokens do not exist until a real run:
they are deliberately separate scenario assumptions, not measured usage.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import importlib.util
import json
import math
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

from path_picker.grounding import compact_evidence
from path_picker.ingest import canonical_edge, path_id
from path_picker.prompts import (
    adaptive_screen_prompt, edge_research_prompt, exclusion_verifier_prompt,
    node_research_prompt, path_review_prompt, verifier_prompt,
)
from path_picker.schemas import (
    ADAPTIVE_SCREEN_SCHEMA, EXCLUSION_VERIFY_SCHEMA, PATH_REVIEW_SCHEMA,
    VERIFY_SCHEMA, research_schema,
)


PRICES = {
    "gpt-6-luna": {"input": .10, "output": .50},
    "gpt-6-sol": {"input": 2., "output": 10.},
}
MODEL_CONFIGURATIONS = {
    "luna_screen_search__sol_verify_deep_abstract": {
        "screening": "gpt-6-luna", "research": "gpt-6-luna",
        "claim_verification": "gpt-6-sol", "exclusion_verification": "gpt-6-sol",
        "deep_review": "gpt-6-sol", "synthesis": "gpt-6-sol", "narrative_audit": "gpt-6-sol",
    },
    "all_sol": {role: "gpt-6-sol" for role in (
        "screening", "research", "claim_verification", "exclusion_verification", "deep_review", "synthesis", "narrative_audit")},
    "all_luna_illustrative_not_quality_validated": {role: "gpt-6-luna" for role in (
        "screening", "research", "claim_verification", "exclusion_verification", "deep_review", "synthesis", "narrative_audit")},
}
SCENARIOS = {
    "lean": dict(research_fraction=.25, node_batch=3, edge_batch=2,
        reassessment_fraction=.10, screen_output=1200, retained_claim_input_per_screen=500,
        search_context=3000, research_output=2000, searches_per_research=1,
        claims_per_item=1, verifier_claim_excerpt_input=2200, verifier_output=500,
        exclusion_calls=10, exclusion_added_input=1000, exclusion_output=600,
        deep_claim_input=4000, deep_output=1400, synthesis_input=25000, synthesis_output=4000,
        narrative_audit_input=4000, narrative_audit_output=500),
    "planning": dict(research_fraction=.60, node_batch=2, edge_batch=2,
        reassessment_fraction=.25, screen_output=2000, retained_claim_input_per_screen=1500,
        search_context=6000, research_output=4000, searches_per_research=2,
        claims_per_item=1.5, verifier_claim_excerpt_input=3500, verifier_output=1000,
        exclusion_calls=40, exclusion_added_input=2000, exclusion_output=1000,
        deep_claim_input=8000, deep_output=2200, synthesis_input=75000, synthesis_output=5000,
        narrative_audit_input=8000, narrative_audit_output=1000),
    "heavy": dict(research_fraction=1., node_batch=1, edge_batch=1,
        reassessment_fraction=1., screen_output=3500, retained_claim_input_per_screen=4000,
        search_context=12000, research_output=6000, searches_per_research=4,
        claims_per_item=3, verifier_claim_excerpt_input=4500, verifier_output=1800,
        exclusion_calls=150, exclusion_added_input=4000, exclusion_output=1800,
        deep_claim_input=16000, deep_output=3500, synthesis_input=180000, synthesis_output=8000,
        narrative_audit_input=16000, narrative_audit_output=1800),
}


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def stats(values: list[int]) -> dict[str, int | float]:
    ordered = sorted(values)
    return {"count": len(values), "minimum": min(values, default=0),
        "median": median(values) if values else 0,
        "mean": mean(values) if values else 0,
        "p90": ordered[max(0, math.ceil(.9 * len(ordered)) - 1)] if values else 0,
        "maximum": max(values, default=0), "total": sum(values)}


def read_session(directory: Path) -> dict[str, Any]:
    """Use the same V1 ledger conversion as the actual agent export writer."""
    export_path = Path(__file__).resolve().parents[1] / "graphical_bayesian_inference/gui/agent_graph_export.py"
    spec = importlib.util.spec_from_file_location("v1_agent_export", export_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load V1 ledger converter: {export_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    paths = rows(directory / "ranked_paths.tsv")
    with gzip.open(directory / "all_returned_path_evidence_ledgers.json.gz", "rt", encoding="utf-8") as handle:
        ledgers = json.load(handle)
    nodes: dict[str, Any] = {}
    edges: dict[str, Any] = {}
    node_frequency: Counter[str] = Counter()
    edge_frequency: Counter[str] = Counter()
    for record in ledgers:
        ledger = module._ledger_record(record)
        if record["kind"] == "node":
            symbol = record["symbol"]
            nodes[symbol] = {"id": symbol, "label": symbol, "name": record.get("name"),
                "classes": record.get("classes"), "evidence_ledger": ledger,
                "posterior_probability": record.get("stored_posterior_probability")}
        else:
            key = canonical_edge(record["node_a"], record["node_b"])
            edges[key] = {"id": key, "node_a": record["node_a"], "node_b": record["node_b"],
                "edge_probability": record.get("stored_posterior_probability"),
                "evidence_ledger": ledger, "directionality": None}
    missing_nodes: set[str] = set()
    missing_edges: set[str] = set()
    for path in paths:
        symbols = [value.strip() for value in path["path_symbols"].split("->")]
        node_frequency.update(set(symbols))
        for symbol in symbols:
            if symbol not in nodes:
                missing_nodes.add(symbol)
                nodes[symbol] = {"id": symbol, "label": symbol,
                    "name": "Missing saved node evidence ledger; no scores invented", "evidence_ledger": {"evidence": []}}
        for left, right in zip(symbols, symbols[1:]):
            key = canonical_edge(left, right)
            edge_frequency[key] += 1
            if key not in edges:
                missing_edges.add(key)
                edges[key] = {"id": key, "node_a": left, "node_b": right, "evidence_ledger": {"evidence": []}}
    # Restrict to THIS saved path union, not a different archive or all scored pairs.
    nodes = {key: value for key, value in nodes.items() if key in node_frequency}
    edges = {key: value for key, value in edges.items() if key in edge_frequency}
    return {"paths": paths, "nodes": nodes, "edges": edges,
        "node_frequency": node_frequency, "edge_frequency": edge_frequency,
        "missing_nodes": sorted(missing_nodes), "missing_edges": sorted(missing_edges),
        "ledger_records": len(ledgers),
        "full_graph": json.loads((directory / "full_graph_statistics_summary.json").read_text(encoding="utf-8"))}


def select_finalists(paths: list[dict[str, str]], count: int) -> list[int]:
    """Cost proxy: one path/query before global rank, not a biological selection."""
    selected, seen = [], set()
    for index, path in enumerate(paths):
        if path["query_id"] not in seen:
            selected.append(index)
            seen.add(path["query_id"])
            if len(selected) == count:
                return selected
    selected.extend(index for index in range(len(paths)) if index not in set(selected))
    return selected[:count]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path, help="Saved HTML or directory containing its adjacent evidence exports")
    parser.add_argument("--tokenizer-path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--deep-reviews", type=int, nargs="+", default=[50, 200])
    parser.add_argument("--web-price-per-thousand", type=float, default=14.)
    args = parser.parse_args()
    if args.tokenizer_path:
        sys.path.insert(0, str(args.tokenizer_path))
    import tiktoken
    encoder = tiktoken.get_encoding("o200k_base")
    def tokens(value: Any) -> int:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return len(encoder.encode(text))
    def request_tokens(prompt: str, schema: Any) -> int:
        return tokens(prompt) + tokens(schema) + 30

    directory = args.session.parent if args.session.is_file() else args.session
    directory = directory.resolve()
    data = read_session(directory)
    paths, nodes, edges = data["paths"], data["nodes"], data["edges"]
    context = {"organism": "mouse", "cell_type": "kidney collecting duct", "stimulus": "acute vasopressin/dDAVP–PKA signaling"}
    def research_item(key: str, value: dict[str, Any]) -> dict[str, Any]:
        return {"item_id": key, "bgi": compact_evidence(value), "biological_context": context,
            "question": "Investigate the acute mechanism, relevant biological context, and direction; seek independent primary experimental support and contradiction.",
            "scope": "Investigate both support and contradiction; for edges cover BOTH directions and alternative acute mechanisms."}
    bundles = []
    screen_tokens, deep_tokens, exclusion_tokens = [], [], []
    for path in paths:
        symbols = [value.strip() for value in path["path_symbols"].split("->")]
        edge_ids = [canonical_edge(a, b) for a, b in zip(symbols, symbols[1:])]
        bundle = {"path_id": path_id(directory.name, path["query_id"], path["path_symbols"]),
            "path": path, "deterministic_score": math.log(max(float(path.get("primary_path_score") or 1e-12), 1e-12)),
            "nodes": [compact_evidence(nodes[key]) for key in symbols],
            "edges": [compact_evidence(edges[key]) for key in edge_ids], "accepted_claims": [],
            "path_symbols": symbols, "query_id": path["query_id"], "biological_context": context,
            "available_research_items": {"node": symbols, "edge": edge_ids}, "completed_research": []}
        bundles.append(bundle)
        screen_tokens.append(request_tokens(adaptive_screen_prompt(bundle), ADAPTIVE_SCREEN_SCHEMA))
        deep_tokens.append(request_tokens(path_review_prompt(bundle), PATH_REVIEW_SCHEMA))
        exclusion_tokens.append(request_tokens(exclusion_verifier_prompt({}, bundle, []), EXCLUSION_VERIFY_SCHEMA))
    verifier_fixed = request_tokens(verifier_prompt({}, ""), VERIFY_SCHEMA)
    singleton_research = {}
    for kind, records, prompt_fn in (("node", nodes, node_research_prompt), ("edge", edges, edge_research_prompt)):
        singleton_research[kind] = stats([request_tokens(prompt_fn([research_item(key, value)]), research_schema(kind)) for key, value in records.items()])

    results = {}
    for deep_count_requested in args.deep_reviews:
        if deep_count_requested < 1:
            raise ValueError("Deep-review count must be positive")
        finalist_indices = select_finalists(paths, min(deep_count_requested, len(paths)))
        final_nodes = {key for index in finalist_indices for key in bundles[index]["path_symbols"]}
        final_edges = {canonical_edge(a, b) for index in finalist_indices for a, b in zip(bundles[index]["path_symbols"], bundles[index]["path_symbols"][1:])}
        scenario_results = {}
        for scenario_name, assumption in SCENARIOS.items():
            researched = {}
            research_inputs = []
            for kind, records, required, prompt_fn in (("node", nodes, final_nodes, node_research_prompt), ("edge", edges, final_edges, edge_research_prompt)):
                count = max(len(required), math.ceil(len(records) * assumption["research_fraction"]))
                ordered = sorted(records, key=lambda key: (-data[f"{kind}_frequency"][key], key))
                chosen = sorted(required)
                chosen.extend(key for key in ordered if key not in required)
                chosen = chosen[:count]
                researched[kind] = len(chosen)
                batch = assumption[f"{kind}_batch"]
                for offset in range(0, len(chosen), batch):
                    items = [research_item(key, records[key]) for key in chosen[offset:offset + batch]]
                    research_inputs.append(request_tokens(prompt_fn(items), research_schema(kind)))
            reassessments = math.ceil(len(paths) * assumption["reassessment_fraction"])
            verification_calls = math.ceil(sum(researched.values()) * assumption["claims_per_item"])
            calls = {"screening": len(paths) + reassessments, "research": len(research_inputs),
                "claim_verification": verification_calls, "exclusion_verification": assumption["exclusion_calls"],
                "deep_review": len(finalist_indices), "synthesis": 1, "narrative_audit": 13}
            role_tokens = {
                "screening": {"input": sum(screen_tokens) + round(mean(screen_tokens) * reassessments) + calls["screening"] * assumption["retained_claim_input_per_screen"], "output": calls["screening"] * assumption["screen_output"]},
                "research": {"input": sum(research_inputs) + calls["research"] * assumption["search_context"], "output": calls["research"] * assumption["research_output"]},
                "claim_verification": {"input": verification_calls * (verifier_fixed + assumption["verifier_claim_excerpt_input"]), "output": verification_calls * assumption["verifier_output"]},
                "exclusion_verification": {"input": round(calls["exclusion_verification"] * (mean(exclusion_tokens) + assumption["exclusion_added_input"])), "output": calls["exclusion_verification"] * assumption["exclusion_output"]},
                "deep_review": {"input": sum(deep_tokens[index] for index in finalist_indices) + len(finalist_indices) * assumption["deep_claim_input"], "output": len(finalist_indices) * assumption["deep_output"]},
                "synthesis": {"input": assumption["synthesis_input"], "output": assumption["synthesis_output"]},
                "narrative_audit": {"input": calls["narrative_audit"] * assumption["narrative_audit_input"], "output": calls["narrative_audit"] * assumption["narrative_audit_output"]},
            }
            web_transactions = calls["research"] * assumption["searches_per_research"]
            web_cost = web_transactions * args.web_price_per_thousand / 1000
            matrix = {}
            for configuration_name, assignments in MODEL_CONFIGURATIONS.items():
                costs = {role: (counts["input"] * PRICES[assignments[role]]["input"] + counts["output"] * PRICES[assignments[role]]["output"]) / 1e6 for role, counts in role_tokens.items()}
                token_cost = sum(costs.values())
                matrix[configuration_name] = {"models": assignments,
                    "model_tokens_usd": round(token_cost, 2), "web_search_usd": round(web_cost, 2),
                    "total_usd": round(token_cost + web_cost, 2),
                    "total_with_10pct_model_rate_uplift_usd": round(token_cost * 1.1 + web_cost, 2),
                    "role_model_costs_usd": {role: round(cost, 2) for role, cost in costs.items()}}
            scenario_results[scenario_name] = {"assumptions": assumption, "researched_unique_items": researched,
                "minimum_finalist_coverage": {"node": len(final_nodes), "edge": len(final_edges)},
                "calls": calls, "total_calls": sum(calls.values()), "web_transactions": web_transactions,
                "role_tokens": role_tokens, "input_tokens": sum(item["input"] for item in role_tokens.values()),
                "output_tokens_including_reasoning": sum(item["output"] for item in role_tokens.values()), "costs": matrix}
        results[str(deep_count_requested)] = scenario_results
    source_files = {}
    for filename in ("ranked_paths.tsv", "all_returned_path_evidence_ledgers.json.gz", "all_returned_path_evidence_ledger_summary.json", "full_graph_statistics_summary.json"):
        path = directory / filename
        source_files[filename] = {"bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    report = {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "paid_api_calls_made": 0, "source_session": str(directory), "source_files": source_files,
        "scope": {"saved_paths": len(paths), "distinct_queries": len({p['query_id'] for p in paths}),
            "path_union_nodes": len(nodes), "path_union_edges": len(edges), "saved_ledger_count": data["ledger_records"],
            "missing_node_ledgers": data["missing_nodes"], "missing_edge_ledgers": data["missing_edges"],
            "full_scored_graph_summary_not_llm_scope": data["full_graph"],
            "important": "All saved paths measured, not a 200-path screening sample. Not the separate 418-node/3296-edge archive. Full scored graph is NOT sent to the LLM."},
        "measurement": {"tokenizer": "tiktoken o200k_base; proxy, not exact GPT-6 billing tokenizer",
            "request_overhead_tokens": 30, "method": "Actual new prompts/schema plus complete compact per-stream ledgers, reconstructed using V1 export converter. No histogram/calibration arrays. Initial bundles have no literature claims; subsequent literature counted separately as assumptions. Legacy edge orientation unavailable in this export is left null, not inferred.",
            "initial_all_path_screen_input": stats(screen_tokens), "all_path_deep_input_baseline": stats(deep_tokens),
            "node_research_singleton_input": singleton_research["node"], "edge_research_singleton_input": singleton_research["edge"]},
        "pricing": {"as_of": "2026-11-09", "usd_per_million_tokens": PRICES,
            "web_usd_per_thousand_transactions": args.web_price_per_thousand,
            "sources": ["https://developers.openai.com/api/docs/models/gpt-6-sol", "https://developers.openai.com/api/docs/models/gpt-6-luna", "https://azure.microsoft.com/en-us/blog/gpt-6-astra-sol-and-luna-for-production-agents-in-microsoft-foundry/", "https://www.microsoft.com/en-us/bing/apis", "https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/web-search"],
            "provider_note": "Azure Global Standard model rates, with Azure Grounding with Bing transaction assumption. Confirm deployment/region invoice rates; OpenAI-direct web-search prices differ. The 10% uplift column is an explicit sensitivity, not an assertion about this resource."},
        "assumption_notes": [
            "Offline planning estimate, NOT a measured paid pilot, quote, or strict upper bound.",
            "All saved paths get an initial screen: no assumed savings from excluding unevaluated paths. Up to one additional adaptive screen/path is possible; scenarios use 10/25/100%. These are assumptions, not measured decisions. Deep finalists see fresh research without an extra screening call.",
            "Research sets are cost proxies chosen by path frequency, augmented to cover every node/edge of cost-proxy finalists; actual adaptive requests may differ. Finalists are balanced by query then rank solely to size prompts.",
            "Every searched constituent is cached globally; search is not repeated per path. Typical batch occupancy is explicitly smaller than maximum node6/edge3 to allow on-demand requests.",
            "Claim-verification assumptions cover only claims with retrievable sources and literal quotes. Exclusion auditing is a separate independent call, counted here.",
            "Thirteen independent final narrative factual-grounding audits are included (up to12 abstracts plus the network synthesis). Research question length uses a fixed generic placeholder; actual generated questions vary.",
            "Output token allowances INCLUDE hidden reasoning and visible JSON/prose. Actual high-reasoning use can exceed these assumptions.",
            "Web retrieved/context tokens and paid Bing transactions are separate charges. A model call is not necessarily one transaction. Actual Azure usage should use tool_usage.web_search.num_requests.",
            "No input cache savings, retries, taxes, negotiated discounts, network hosting, or human review time assumed. No claim of zero hallucination risk.",
            "Pricing uses short-context rates. All scenario single-request estimates are below272k input; cumulative run tokens do not trigger per-request long-context pricing. Unusually long live claims may change that.",
            f"Missing saved node ledgers: {', '.join(data['missing_nodes']) or 'none'}. They remain explicit unscored placeholders. No invented Bayes factors."],
        "scenario_results_by_final_deep_review_cap": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Path-first cost estimate", "", "Offline estimate — no paid API calls were made.", "",
            f"Measured **{len(paths):,} saved paths**, **{len(nodes)} unique nodes**, **{len(edges)} unique edges** from `{directory.name}`.",
            f"The full scored graph has {data['full_graph']['node_count']:,} nodes and {data['full_graph']['edge_count']:,} edges, but is not the review scope. This is not the separate 418-node/3,296-edge archive.", "",
            f"Initial all-path screening payload: **{sum(screen_tokens):,} proxy input tokens**, median **{median(screen_tokens):,.0f}/path**. Every stream's compact evidence is retained; histogram arrays are omitted.", "",
            "| Final deep reviews | Workload assumption | Luna screen/search + Sol judgment | All Sol | All Luna (illustrative) |", "|---:|---|---:|---:|---:|"]
        for cap, scenarios in results.items():
            for name, scenario in scenarios.items():
                cost = scenario["costs"]
                values = [cost[key]["total_usd"] for key in MODEL_CONFIGURATIONS]
                lines.append(f"| {cap} | {name} | ${values[0]:,.2f} | ${values[1]:,.2f} | ${values[2]:,.2f} |")
        lines += ["", "All figures include the assumed Azure web-search transaction charges. Global Standard reference rates; regional/contract rates may differ.", "", "## What is assumed, not measured", ""]
        lines.extend(f"- {item}" for item in report["assumption_notes"])
        lines += ["", "## Scenario details", "", "| Assumption | Lean | Planning | Heavy |", "|---|---:|---:|---:|"]
        labels = [("Unique constituents researched (minimum; finalists may add more)", "research_fraction"), ("Additional screening fraction", "reassessment_fraction"), ("Output/reasoning tokens per screen", "screen_output"), ("Claims independently verified per researched item", "claims_per_item"), ("Search transactions per research call", "searches_per_research"), ("Research context tokens per call", "search_context")]
        for label, key in labels:
            lines.append("| " + label + " | " + " | ".join(str(SCENARIOS[name][key]) for name in SCENARIOS) + " |")
        lines += ["", "Detailed per-stage input/output counts, calls, assumptions and hashes are in [the JSON report](path_first_cost_estimate.json).", "", "## Pricing sources", ""]
        lines.extend(f"- [{url.split('/')[2]}]({url})" for url in report["pricing"]["sources"])
        lines += ["", "## Reproduce locally", "", "Requires `tiktoken` and the adjacent V1 evidence exports, not just a standalone HTML. From the Path Picker directory:", "", "```text", "python estimate_path_first_cost.py PATH_TO_SAVED_SESSION --output docs/path_first_cost_estimate.json --report docs/path_first_cost_estimate.md --deep-reviews 50 200", "```", "", "If tiktoken is in a separate directory, pass `--tokenizer-path PATH_TO_DEPENDENCIES`. The estimate makes no model API requests. Change `--web-price-per-thousand` to your actual provider price."]
        args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "paths": len(paths), "nodes": len(nodes), "edges": len(edges),
        "initial_screen_input_tokens": sum(screen_tokens), "costs": {cap: {name: {configuration: value["total_usd"] for configuration, value in scenario["costs"].items()} for name, scenario in scenarios.items()} for cap, scenarios in results.items()}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
