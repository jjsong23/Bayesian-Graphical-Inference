"""End-to-end orchestrator for evidence-class-specific protocol v0.6."""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .evidence_profiles import (
    EDGE_EVIDENCE_PROFILES,
    PROFILE_IDS,
    discovery_tasks,
    profile,
)
from .postprocess import (
    build_edge_record,
    build_node_record,
    build_stream_reference_record,
    gate_evidence_item,
    write_outputs,
    write_stream_reference_outputs,
)
from .prompts import edge_generation, edge_validation, negative_node, node_generation, node_validation, pair_generation
from .responses import ResponsesClient
from .retrieval import RetrievalSession
from .schemas import (
    EDGE_GENERATION_SCHEMA,
    EDGE_VALIDATION_SCHEMA,
    NEGATIVE_NODE_SCHEMA,
    NODE_GENERATION_SCHEMA,
    NODE_VALIDATION_SCHEMA,
    PAIR_GENERATION_SCHEMA,
)


FUNCTIONAL_CLASSES = [
    "GPCRs and receptors", "G proteins and RGS proteins", "adenylyl cyclases and PDEs",
    "protein kinases", "protein phosphatases and their regulatory subunits", "AKAPs and scaffolds",
    "small GTPases, GEFs, and GAPs", "vesicle trafficking (Rabs, SNAREs, motors)",
    "cytoskeleton and actin regulators", "channels and transporters", "ubiquitin and proteasome",
    "transcription factors and coregulators", "other signaling",
]
CELL_TYPES = [
    "principal cell", "type A intercalated cell", "type B and non-A non-B intercalated cell",
    "collecting duct, cell type unspecified",
]
NEGATIVE_SEGMENTS = [
    "proximal tubule", "thin limbs", "thick ascending limb", "distal convoluted tubule",
    "glomerulus and podocytes", "renal vasculature",
]
SEGMENT_DECOYS = ["LRP2", "SLC34A1", "UMOD", "SLC12A1", "NPHS1", "NPHS2"]
PAIR_SEARCH_TASKS = discovery_tasks()


class GroundTruthCancelled(RuntimeError):
    """Raised at a safe checkpoint after the operator requests cancellation."""


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _write_json_atomic(path: Path, payload: Any) -> None:
    """Replace a JSON checkpoint only after the new file is complete."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def _symbol(value: object) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]", "", str(value or "").strip())
    return text.upper()


def _is_bio_policy_error(error: BaseException) -> bool:
    """Recognize the provider's non-retryable biology-policy response."""

    message = str(error).casefold()
    return "bio_policy" in message or "possible biological risk" in message


class GroundTruthPipeline:
    def __init__(
        self,
        config: dict[str, Any],
        run_dir: Path,
        *,
        api_key: str | None = None,
        progress: Callable[[str, float], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config
        self.run_dir = run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        key = (api_key or os.environ.get(config.get("api_key_env", "AZURE_OPENAI_API_KEY"), "")).strip()
        if not key:
            raise RuntimeError(
                f"No API key found. Set {config.get('api_key_env', 'AZURE_OPENAI_API_KEY')} "
                "for this terminal; the key is never written to disk."
            )
        self.client = ResponsesClient(base_url=config["api_base_url"], api_key=key, timeout=int(config.get("timeout_seconds", 600)))
        self.generator_model = config["deployments"]["generator"]
        self.validator_model = config["deployments"]["validator"]
        self.generator_web_search = bool(config.get("generator_web_search", True))
        self.generator_web_search_required = bool(config.get("generator_web_search_required", True))
        self.generator_web_search_context_size = str(
            config.get("generator_web_search_context_size", "low")
        ).strip().casefold()
        if self.generator_web_search_context_size not in {"low", "medium", "high"}:
            raise ValueError("generator_web_search_context_size must be low, medium, or high")
        self.generator_concurrency = int(config.get("generator_concurrency", 4))
        self.validator_concurrency = int(config.get("validator_concurrency", 4))
        if not 1 <= self.generator_concurrency <= 32:
            raise ValueError("generator_concurrency must be between 1 and 32")
        if not 1 <= self.validator_concurrency <= 16:
            raise ValueError("validator_concurrency must be between 1 and 16")
        configured_profiles = config.get("evidence_profiles", list(PROFILE_IDS))
        if not isinstance(configured_profiles, list) or not configured_profiles:
            raise ValueError("evidence_profiles must be a non-empty list")
        unknown_profiles = sorted(set(configured_profiles).difference(PROFILE_IDS))
        if unknown_profiles:
            raise ValueError(f"Unknown evidence_profiles: {', '.join(unknown_profiles)}")
        self.evidence_profiles = tuple(
            profile_id for profile_id in PROFILE_IDS if profile_id in configured_profiles
        )
        self.seed = int(config.get("seed", 20261001))
        self.usage: list[dict[str, Any]] = []
        self._usage_lock = threading.Lock()
        self._sequence_lock = threading.Lock()
        self._thread_state = threading.local()
        self.progress = progress
        self.cancel_requested = cancel_requested
        self.sequence_cache_path = self.run_dir / "reference_sequences.json"
        self.sequence_cache = json.loads(self.sequence_cache_path.read_text(encoding="utf-8")) if self.sequence_cache_path.exists() else {}

    def _checkpoint(self, message: str, fraction: float) -> None:
        if self.cancel_requested and self.cancel_requested():
            raise GroundTruthCancelled("Ground-truth curation cancelled by user")
        if self.progress:
            self.progress(message, max(0.0, min(1.0, float(fraction))))

    def _sequence(self, symbol: str, species: str) -> str:
        species_key = str(species).casefold().strip()
        taxon = {"human": 9606, "mouse": 10090, "rat": 10116}.get(species_key)
        cache_key = f"{species_key}:{symbol.casefold()}"
        with self._sequence_lock:
            cached = self.sequence_cache.get(cache_key)
        if cached is not None:
            return str(cached.get("sequence", ""))
        if not taxon or not symbol:
            return ""
        query = f"(gene_exact:{symbol} AND organism_id:{taxon} AND reviewed:true)"
        params = urlencode({"query": query, "format": "json", "fields": "accession,gene_names,sequence", "size": 5})
        request = Request(f"https://rest.uniprot.org/uniprotkb/search?{params}", headers={"User-Agent": "CD-PKA-ground-truth-curator/0.6"})
        try:
            with urlopen(request, timeout=60) as response:  # noqa: S310 - fixed UniProt endpoint
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            record = {"sequence": "", "error": str(exc)}
        else:
            rows = payload.get("results", [])
            selected = rows[0] if rows else {}
            record = {
                "sequence": selected.get("sequence", {}).get("value", ""),
                "accession": selected.get("primaryAccession", ""),
                "query": query,
            }
        with self._sequence_lock:
            # Another worker may have completed the same lookup while this one
            # was waiting on UniProt. Either result is equivalent, so keep the
            # first durable answer and never let concurrent writers truncate the
            # shared sequence cache.
            self.sequence_cache.setdefault(cache_key, record)
            _write_json_atomic(self.sequence_cache_path, self.sequence_cache)
            return str(self.sequence_cache[cache_key].get("sequence", ""))

    def _sequences_for_item(self, item: dict[str, Any]) -> dict[str, str]:
        site = item.get("site")
        if not isinstance(site, dict) or not site.get("position"):
            return {}
        symbol = str(item.get("target", "")).strip()
        source_species = str(site.get("species") or item.get("species", "")).casefold()
        species = {source_species, "human", "rat"}
        return {
            f"{name}:{symbol.casefold()}": sequence
            for name in species if (sequence := self._sequence(symbol, name))
        }

    @staticmethod
    def _map_site_by_exact_block(source: str, target: str, position: int) -> int | None:
        from difflib import SequenceMatcher
        zero_based = position - 1
        for block in SequenceMatcher(None, source, target, autojunk=False).get_matching_blocks():
            if block.a <= zero_based < block.a + block.size:
                return block.b + (zero_based - block.a) + 1
        return None

    def _annotate_site_mapping(self, item: dict[str, Any], sequences: dict[str, str]) -> None:
        site = item.get("site")
        if not item.get("site_verified") or not isinstance(site, dict):
            return
        symbol = str(item.get("target", "")).casefold()
        source_species = str(site.get("species") or item.get("species", "")).casefold()
        source = sequences.get(f"{source_species}:{symbol}", "")
        if not source:
            return
        for destination in ("human", "rat"):
            target = sequences.get(f"{destination}:{symbol}", "")
            if target:
                mapped = self._map_site_by_exact_block(source, target, int(site["position"]))
                if mapped is not None:
                    site[f"position_{destination}"] = mapped

    @staticmethod
    def _restore_retrieval(retrieval: RetrievalSession) -> None:
        log_path = retrieval.directory / "retrieval_log.json"
        if not log_path.exists():
            return
        retrieval.calls = json.loads(log_path.read_text(encoding="utf-8"))
        for call in retrieval.calls:
            result = call.get("result", {})
            pmid = str(result.get("pmid", ""))
            if pmid and result.get("text"):
                previous = retrieval.documents.get(pmid, "")
                retrieval.documents[pmid] = f"{previous}\n{result['text']}".strip()
            if pmid and result.get("metadata"):
                retrieval.metadata[pmid] = result["metadata"]

    def _structured(self, role: str, **kwargs: Any) -> dict[str, Any]:
        if self.cancel_requested and self.cancel_requested():
            raise GroundTruthCancelled("Ground-truth curation cancelled by user")
        result, response = self.client.structured(**kwargs)
        with self._usage_lock:
            self.usage.append({"role": role, "response_id": response.get("id"), "model": response.get("model"), "usage": response.get("usage")})
        self._thread_state.generation_response_audit = self._response_audit(response)
        return result

    @staticmethod
    def _response_audit(response: dict[str, Any]) -> dict[str, Any]:
        """Keep web-search provenance without duplicating private reasoning text."""

        search_calls: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        for item in response.get("output", []):
            if item.get("type") == "web_search_call":
                search_calls.append({
                    "id": item.get("id"),
                    "status": item.get("status"),
                    "action": item.get("action"),
                })
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                for annotation in content.get("annotations", []):
                    if annotation.get("type") == "url_citation":
                        citations.append(annotation)
        return {
            "response_id": response.get("id"),
            "model": response.get("model"),
            "usage": response.get("usage"),
            "web_search_calls": search_calls,
            "url_citations": citations,
        }

    def _cached_generation(
        self,
        cache_path: Path,
        *,
        task: dict[str, Any],
        role: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return a durable generation result, making the API call only once."""

        if cache_path.is_file():
            saved = json.loads(cache_path.read_text(encoding="utf-8"))
            if saved.get("task") != task:
                raise RuntimeError(f"Generation checkpoint task mismatch: {cache_path}")
            return saved["result"]
        if role in {"node_generation", "negative_node_generation", "edge_generation", "pair_generation"}:
            kwargs.setdefault("web_search", self.generator_web_search)
            kwargs.setdefault("require_web_search", self.generator_web_search_required)
            kwargs.setdefault("search_context_size", self.generator_web_search_context_size)
        self._thread_state.generation_response_audit = None
        result = self._structured(role, **kwargs)
        _write_json_atomic(cache_path, {
            "schema_version": 2,
            "task": task,
            "result": result,
            "response_audit": getattr(self._thread_state, "generation_response_audit", None),
        })
        return result

    def _parallel_generation(
        self,
        specifications: list[dict[str, Any]],
        *,
        message: str,
        progress_start: float,
        progress_end: float,
    ) -> dict[Any, dict[str, Any]]:
        """Run independent generator calls concurrently and retain every result.

        Each worker writes its own atomic response checkpoint before its future
        completes. If one call ultimately fails, successful sibling calls remain
        reusable on retry.
        """

        if not specifications:
            return {}
        worker_count = min(self.generator_concurrency, len(specifications))
        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="gt-generator")
        futures: dict[Future[dict[str, Any]], Any] = {}
        try:
            for specification in specifications:
                future = executor.submit(
                    self._cached_generation,
                    specification["cache_path"],
                    task=specification["task"],
                    role=specification["role"],
                    **specification["kwargs"],
                )
                futures[future] = specification["key"]
            results: dict[Any, dict[str, Any]] = {}
            for completed, future in enumerate(as_completed(futures), start=1):
                results[futures[future]] = future.result()
                fraction = progress_start + (progress_end - progress_start) * completed / len(futures)
                self._checkpoint(
                    f"{message} · {completed}/{len(futures)} · {worker_count} parallel workers",
                    fraction,
                )
            return results
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    def generate_nodes(self) -> dict[str, dict[str, Any]]:
        path = self.run_dir / "node_candidates.json"
        if path.exists():
            self._checkpoint("Recovered saved node candidates", 0.25)
            return json.loads(path.read_text(encoding="utf-8"))
        repeats = int(self.config.get("node_generation_repeats", 5))
        epsilon = float(self.config.get("saturation_epsilon", 0.02))
        max_rounds = int(self.config.get("max_generation_rounds", 4))
        nodes: dict[str, dict[str, Any]] = {}
        calls_per_round = len(FUNCTIONAL_CLASSES) * len(CELL_TYPES) * repeats + len(NEGATIVE_SEGMENTS)
        for round_number in range(1, max_rounds + 1):
            before = set(nodes)
            round_call = 0
            specifications: list[dict[str, Any]] = []
            for functional_class in FUNCTIONAL_CLASSES:
                for cell_type in CELL_TYPES:
                    for sample in range(repeats):
                        round_call += 1
                        task = {
                            "kind": "positive", "round": round_number, "call": round_call,
                            "functional_class": functional_class, "cell_type": cell_type,
                            "sample": sample + 1,
                        }
                        specifications.append({
                            "key": round_call,
                            "cache_path": self.run_dir / "generation" / "nodes" / f"round_{round_number:02d}" / f"call_{round_call:03d}.json",
                            "task": task,
                            "role": "node_generation",
                            "kwargs": {
                                "model": self.generator_model,
                                "prompt": node_generation(functional_class, cell_type),
                                "schema_name": "cd_node_generation", "schema": NODE_GENERATION_SCHEMA,
                                "reasoning_effort": "none", "temperature": 1.0,
                            },
                            "provenance": {
                                "round": round_number, "sample": sample + 1,
                                "query_class": functional_class, "cell_type": cell_type,
                            },
                        })
            for segment in NEGATIVE_SEGMENTS:
                round_call += 1
                task = {
                    "kind": "negative", "round": round_number, "call": round_call,
                    "segment": segment,
                }
                specifications.append({
                    "key": round_call,
                    "cache_path": self.run_dir / "generation" / "nodes" / f"round_{round_number:02d}" / f"call_{round_call:03d}.json",
                    "task": task,
                    "role": "negative_node_generation",
                    "kwargs": {
                        "model": self.generator_model,
                        "prompt": negative_node(segment),
                        "schema_name": "cd_negative_node_generation", "schema": NEGATIVE_NODE_SCHEMA,
                        "reasoning_effort": "none", "temperature": 1.0,
                    },
                    "negative_segment": segment,
                })
            progress_start = 0.02 + 0.23 * (round_number - 1) / max(max_rounds, 1)
            progress_end = 0.02 + 0.23 * round_number / max(max_rounds, 1)
            results = self._parallel_generation(
                specifications,
                message=f"Generating nodes · round {round_number}/{max_rounds}",
                progress_start=progress_start,
                progress_end=progress_end,
            )
            # Merge in task order so a concurrent run produces the same durable
            # candidates and provenance ordering as the original serial run.
            for specification in specifications:
                result = results[specification["key"]]
                for item in result["proteins"]:
                    symbol = _symbol(item["symbol"])
                    if not symbol:
                        continue
                    record = nodes.setdefault(symbol, {"symbol": symbol, "appearances": 0, "provenance": []})
                    if specification["role"] == "negative_node_generation":
                        record["negative_candidate"] = True
                        record["provenance"].append({
                            "round": round_number,
                            "negative_segment": specification["negative_segment"],
                            **item,
                        })
                    else:
                        record["appearances"] += 1
                        record["provenance"].append({**specification["provenance"], **item})
            _write_json_atomic(self.run_dir / "node_generation_checkpoint.json", {
                "schema_version": 1,
                "complete": False,
                "completed_round": round_number,
                "completed_calls": round_number * calls_per_round,
                "candidate_count": len(nodes),
            })
            new_fraction = len(set(nodes) - before) / max(len(nodes), 1)
            if round_number > 1 and new_fraction < epsilon:
                break
        _write_json_atomic(path, nodes)
        _write_json_atomic(self.run_dir / "node_generation_checkpoint.json", {
            "schema_version": 1,
            "complete": True,
            "completed_round": round_number,
            "completed_calls": round_number * calls_per_round,
            "candidate_count": len(nodes),
        })
        return nodes

    def generate_edges(self, nodes: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        path = self.run_dir / "edge_candidates.json"
        if path.exists():
            self._checkpoint("Recovered saved edge candidates", 0.42)
            return json.loads(path.read_text(encoding="utf-8"))
        checkpoint_path = self.run_dir / "edge_generation_checkpoint.json"
        repeats = int(self.config.get("edge_generation_repeats", 3))
        max_depth = int(self.config.get("snowball_depth", 2))
        queue = [(symbol, 0) for symbol in sorted(nodes) if not nodes[symbol].get("negative_candidate")]
        seen_nodes = set(nodes)
        edges: dict[tuple[str, str], dict[str, Any]] = {}
        cursor = 0
        while cursor < len(queue):
            calls_per_node = 2 * repeats
            node_batch_size = max(1, math.ceil(self.generator_concurrency / max(calls_per_node, 1)))
            batch_start = cursor
            batch_end = min(len(queue), batch_start + node_batch_size)
            batch = queue[batch_start:batch_end]
            specifications = []
            for offset, (symbol, depth) in enumerate(batch):
                node_index = batch_start + offset + 1
                node = nodes.get(symbol, {"symbol": symbol})
                for direction in ("up", "down"):
                    for sample in range(repeats):
                        task = {
                            "kind": "edge", "node_index": node_index, "symbol": symbol,
                            "depth": depth, "direction": direction, "sample": sample + 1,
                        }
                        specifications.append({
                            "key": (node_index, direction, sample + 1),
                            "cache_path": (
                                self.run_dir / "generation" / "edges"
                                / f"node_{node_index:05d}_{_symbol(symbol)}"
                                / f"{direction}_{sample + 1:02d}.json"
                            ),
                            "task": task,
                            "role": "edge_generation",
                            "kwargs": {
                                "model": self.generator_model,
                                "prompt": edge_generation(symbol, node.get("full_name", ""), node.get("aliases", []), direction),
                                "schema_name": "cd_edge_generation", "schema": EDGE_GENERATION_SCHEMA,
                                "reasoning_effort": "none", "temperature": 1.0,
                            },
                        })
            results = self._parallel_generation(
                specifications,
                message=f"Generating candidate edges · nodes {batch_start + 1}–{batch_end}/{len(queue)}",
                progress_start=0.25 + 0.17 * batch_start / max(len(queue), 1),
                progress_end=0.25 + 0.17 * batch_end / max(len(queue), 1),
            )
            for offset, (symbol, depth) in enumerate(batch):
                node_index = batch_start + offset + 1
                for direction in ("up", "down"):
                    for sample in range(repeats):
                        result = results[(node_index, direction, sample + 1)]
                        for item in result["partners"]:
                            partner = _symbol(item["partner"])
                            if not partner or partner == symbol:
                                continue
                            pair = tuple(sorted((symbol, partner)))
                            record = edges.setdefault(pair, {"pair": list(pair), "proposals": []})
                            record["proposals"].append({"query": symbol, "direction": direction, "sample": sample + 1, **item})
                            if depth < max_depth and partner not in seen_nodes:
                                seen_nodes.add(partner)
                                nodes[partner] = {"symbol": partner, "snowball": True, "appearances": 0, "provenance": []}
                                queue.append((partner, depth + 1))
                cursor += 1
                _write_json_atomic(checkpoint_path, {
                    "schema_version": 1,
                    "complete": False,
                    "completed_nodes": cursor,
                    "queue_size": len(queue),
                    "candidate_edge_count": len(edges),
                    "generator_concurrency": self.generator_concurrency,
                })
        records = list(edges.values())
        _write_json_atomic(path, records)
        _write_json_atomic(checkpoint_path, {
            "schema_version": 1,
            "complete": True,
            "completed_nodes": cursor,
            "queue_size": len(queue),
            "candidate_edge_count": len(edges),
        })
        return records

    def discover_pairs(self) -> list[dict[str, Any]]:
        """Discover PKA-responsive pairs separately by claim class and polarity."""

        path = self.run_dir / "edge_candidates.json"
        checkpoint_path = self.run_dir / "pair_generation_checkpoint.json"
        if path.exists():
            self._checkpoint("Recovered saved pair candidates", 0.42)
            return json.loads(path.read_text(encoding="utf-8"))

        calls_per_round = int(self.config.get("pair_search_calls_per_round", 24))
        max_rounds = int(self.config.get("max_pair_search_rounds", 10))
        min_rounds = int(self.config.get("min_pair_search_rounds", 3))
        saturation_rounds = int(self.config.get("pair_search_saturation_rounds", 2))
        min_new_pairs = int(self.config.get("pair_search_min_new_pairs", 10))
        max_candidates = int(self.config.get("max_pair_candidates", 500))
        minimum_per_stratum = int(
            self.config.get("minimum_candidates_per_profile_polarity", 10)
        )
        search_roster = [
            task for task in PAIR_SEARCH_TASKS
            if task["validation_profile"] in self.evidence_profiles
        ]
        if not search_roster:
            raise ValueError("No evidence-profile discovery tasks are enabled")
        edges: dict[tuple[str, str, str], dict[str, Any]] = {}
        consecutive_low_yield = 0
        completed_round = 0

        for round_number in range(1, max_rounds + 1):
            specifications: list[dict[str, Any]] = []
            for call_number in range(1, calls_per_round + 1):
                focus_index = (
                    (round_number - 1) * calls_per_round + call_number - 1
                ) % len(search_roster)
                search_task = search_roster[focus_index]
                validation_profile = search_task["validation_profile"]
                polarity_target = search_task["polarity_target"]
                focus = search_task["search_focus"]
                task = {
                    "kind": "pair_search", "round": round_number,
                    "call": call_number, "search_focus": focus,
                    "validation_profile": validation_profile,
                    "polarity_target": polarity_target,
                }
                specifications.append({
                    "key": call_number,
                    "cache_path": (
                        self.run_dir / "generation" / "pairs"
                        / f"round_{round_number:02d}" / f"call_{call_number:03d}.json"
                    ),
                    "task": task,
                    "role": "pair_generation",
                    "kwargs": {
                        "model": self.generator_model,
                        "prompt": pair_generation(
                            validation_profile,
                            polarity_target,
                            focus,
                            round_number,
                            call_number,
                        ),
                        "schema_name": "cd_pka_stream_pair_generation",
                        "schema": PAIR_GENERATION_SCHEMA,
                        "reasoning_effort": "none", "temperature": 1.0,
                    },
                })
            results = self._parallel_generation(
                specifications,
                message=f"Searching broadly for viable pairs · round {round_number}/{max_rounds}",
                progress_start=0.02 + 0.40 * (round_number - 1) / max(max_rounds, 1),
                progress_end=0.02 + 0.40 * round_number / max(max_rounds, 1),
            )
            before_count = len(edges)
            for specification in specifications:
                result = results[specification["key"]]
                for item in result.get("pairs", []):
                    validation_profile = specification["task"]["validation_profile"]
                    polarity_target = specification["task"]["polarity_target"]
                    if item.get("candidate_polarity") != polarity_target:
                        continue
                    source = _symbol(item.get("source"))
                    target = _symbol(item.get("target"))
                    if not source or not target or source == target:
                        continue
                    pair = tuple(sorted((source, target)))
                    edge_key = (validation_profile, pair[0], pair[1])
                    record = edges.setdefault(edge_key, {
                        "pair": list(pair),
                        "validation_profile": validation_profile,
                        "calibrates_streams": list(
                            profile(validation_profile)["calibrates_streams"]
                        ),
                        "proposals": [],
                    })
                    record["proposals"].append({
                        "round": round_number,
                        "sample": specification["key"],
                        "search_focus": specification["task"]["search_focus"],
                        "validation_profile": validation_profile,
                        "polarity_target": polarity_target,
                        **item,
                        "source": source,
                        "target": target,
                    })
            new_pairs = len(edges) - before_count
            completed_round = round_number
            if new_pairs < min_new_pairs:
                consecutive_low_yield += 1
            else:
                consecutive_low_yield = 0
            _write_json_atomic(checkpoint_path, {
                "schema_version": 1,
                "complete": False,
                "completed_round": round_number,
                "completed_calls": round_number * calls_per_round,
                "new_pairs_this_round": new_pairs,
                "unique_pair_count": len(edges),
                "consecutive_low_yield_rounds": consecutive_low_yield,
                "stopping_rule": {
                    "minimum_rounds": min_rounds,
                    "low_yield_rounds": saturation_rounds,
                    "minimum_new_pairs": min_new_pairs,
                },
            })
            if round_number >= min_rounds and consecutive_low_yield >= saturation_rounds:
                break

        confidence_score = {"high": 2.0, "medium": 1.0, "low": 0.0}

        def ranking_score(record: dict[str, Any]) -> tuple[float, int, tuple[str, str]]:
            proposals = record["proposals"]
            score = 4.0 * len(proposals)
            score += sum(confidence_score.get(str(item.get("confidence", "")).casefold(), 0.0) for item in proposals)
            score += 3.0 * sum(
                item.get("pka_relationship") in {"direct_pka", "pka_dependent"}
                for item in proposals
            )
            score += 1.0 * sum(item.get("shown_with_vasopressin") == "yes" for item in proposals)
            score += 1.0 * sum(item.get("shown_in_cd") == "yes" for item in proposals)
            return score, len(proposals), tuple(record["pair"])

        discovered_count = len(edges)
        ranked = sorted(edges.values(), key=ranking_score, reverse=True)
        records: list[dict[str, Any]] = []
        selected_ids: set[tuple[str, str, str]] = set()

        def record_id(record: dict[str, Any]) -> tuple[str, str, str]:
            return (
                record["validation_profile"],
                record["pair"][0],
                record["pair"][1],
            )

        # Keep explicit negatives and every evidence class from being crowded
        # out by common positive physical/regulatory pairs.
        for validation_profile in self.evidence_profiles:
            for polarity in ("positive", "negative"):
                candidates = [
                    record for record in ranked
                    if record["validation_profile"] == validation_profile
                    and any(
                        proposal.get("candidate_polarity") == polarity
                        for proposal in record["proposals"]
                    )
                ]
                for record in candidates[:minimum_per_stratum]:
                    identifier = record_id(record)
                    if identifier not in selected_ids and len(records) < max_candidates:
                        selected_ids.add(identifier)
                        records.append(record)
        for record in ranked:
            identifier = record_id(record)
            if identifier not in selected_ids and len(records) < max_candidates:
                selected_ids.add(identifier)
                records.append(record)

        # Direct phosphorylation is a mechanistically specific form of acute
        # regulation.  Preserve the strict phosphorylation validation while
        # also evaluating every retained phosphorylation pair under the broader
        # acute-regulatory claim used to calibrate OmniPath and STRING.
        inherited_acute_count = 0
        if {
            "directed_phosphorylation", "acute_regulatory_signaling"
        }.issubset(self.evidence_profiles):
            by_id = {record_id(record): record for record in records}
            for directed in list(records):
                if directed["validation_profile"] != "directed_phosphorylation":
                    continue
                acute_id = (
                    "acute_regulatory_signaling",
                    directed["pair"][0],
                    directed["pair"][1],
                )
                existing_acute = by_id.get(acute_id)
                if existing_acute is not None:
                    inherited = existing_acute.setdefault(
                        "inherited_from_profiles", []
                    )
                    if "directed_phosphorylation" not in inherited:
                        inherited.append("directed_phosphorylation")
                    continue
                inherited_record = {
                    "pair": list(directed["pair"]),
                    "validation_profile": "acute_regulatory_signaling",
                    "calibrates_streams": list(
                        profile("acute_regulatory_signaling")["calibrates_streams"]
                    ),
                    "proposals": [
                        {
                            **proposal,
                            "validation_profile": "acute_regulatory_signaling",
                            "inherited_from_profile": "directed_phosphorylation",
                        }
                        for proposal in directed.get("proposals", [])
                    ],
                    "inherited_from_profiles": ["directed_phosphorylation"],
                }
                records.append(inherited_record)
                by_id[acute_id] = inherited_record
                selected_ids.add(acute_id)
                inherited_acute_count += 1
        records.sort(key=ranking_score, reverse=True)
        _write_json_atomic(path, records)
        _write_json_atomic(checkpoint_path, {
            "schema_version": 1,
            "complete": True,
            "completed_round": completed_round,
            "completed_calls": completed_round * calls_per_round,
            "discovered_unique_pair_count": discovered_count,
            "selected_pair_count": len(records),
            "inherited_acute_regulatory_candidate_count": inherited_acute_count,
            "max_pair_candidates": max_candidates,
            "minimum_candidates_per_profile_polarity": minimum_per_stratum,
            "enabled_evidence_profiles": list(self.evidence_profiles),
            "consecutive_low_yield_rounds": consecutive_low_yield,
        })
        return records

    @staticmethod
    def nodes_from_pairs(edges: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Create the validator's node set directly from discovered pair endpoints."""

        nodes: dict[str, dict[str, Any]] = {}
        for edge in edges:
            for symbol in edge["pair"]:
                record = nodes.setdefault(symbol, {
                    "symbol": symbol, "appearances": 0, "provenance": [],
                    "pair_search_candidate": True,
                })
                proposals = [
                    proposal for proposal in edge.get("proposals", [])
                    if symbol in {proposal.get("source"), proposal.get("target")}
                ]
                record["appearances"] += len(proposals)
                record["provenance"].extend({
                    "round": proposal.get("round"),
                    "sample": proposal.get("sample"),
                    "search_focus": proposal.get("search_focus"),
                    "relation": proposal.get("relation"),
                    "paired_with": edge["pair"][1] if edge["pair"][0] == symbol else edge["pair"][0],
                } for proposal in proposals)
        return nodes

    def add_edge_decoys(self, nodes: dict[str, dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
        path = self.run_dir / "edge_candidates_with_decoys.json"
        if path.exists():
            self._checkpoint("Recovered saved decoy candidates", 0.46)
            return json.loads(path.read_text(encoding="utf-8"))
        records = list(edges)
        existing = {
            (record.get("validation_profile", "acute_regulatory_signaling"), *tuple(sorted(record["pair"])))
            for record in records
        }
        fraction = min(max(float(self.config.get("decoy_fraction", 0.10)), 0.0), 0.40)
        target_count = math.ceil(len(records) * fraction / max(1.0 - fraction, 0.01))

        def add(
            pair: tuple[str, str],
            decoy_type: str,
            validation_profile: str,
            **metadata: Any,
        ) -> bool:
            normalized = tuple(sorted(pair))
            identifier = (validation_profile, *normalized)
            if identifier in existing or normalized[0] == normalized[1]:
                return False
            existing.add(identifier)
            records.append({
                "pair": list(normalized),
                "validation_profile": validation_profile,
                "calibrates_streams": list(profile(validation_profile)["calibrates_streams"]),
                "proposals": [],
                "decoy_type": decoy_type,
                **metadata,
            })
            return True

        # A canonical vasopressin-filter decoy: biologically established but not
        # expected to qualify as vasopressin-responsive in collecting duct.
        add(("INSR", "IRS1"), "pka_scope_filter", "acute_regulatory_signaling")
        rng = random.Random(self.seed)
        symbols = sorted(nodes)
        co_mention_goal = max(0, (target_count - 1) // 2)
        co_mention_count = 0
        max_queries = min(int(self.config.get("max_co_mention_decoy_queries", 100)), max(len(symbols) * 2, 0))
        retrieval = RetrievalSession(self.run_dir / "decoy_generation", max_searches=max_queries, max_papers=0)
        attempts = 0
        while co_mention_count < co_mention_goal and attempts < max_queries and len(symbols) >= 2:
            self._checkpoint(
                f"Constructing literature co-mention decoys · query {attempts + 1}/{max_queries}",
                0.42 + 0.04 * attempts / max(max_queries, 1),
            )
            pair = tuple(sorted(rng.sample(symbols, 2)))
            attempts += 1
            if ("acute_regulatory_signaling", *pair) in existing:
                continue
            try:
                result = retrieval.search_europepmc(f'TITLE_ABS:"{pair[0]}" AND TITLE_ABS:"{pair[1]}"', max_results=3)
            except Exception:
                continue
            if int(result.get("hit_count", 0)) >= 3 and add(
                pair,
                "co_mentioned",
                "acute_regulatory_signaling",
                europe_pmc_hit_count=result["hit_count"],
            ):
                co_mention_count += 1
        kinases = [symbol for symbol in self.config.get("random_decoy_kinases", ["PRKACA", "AKT1", "MAPK1", "CSNK2A1", "GSK3B"]) if symbol]
        random_attempts = 0
        while sum(bool(record.get("decoy_type")) for record in records) < target_count and symbols and random_attempts < max(target_count * 20, 100):
            random_attempts += 1
            add(
                (rng.choice(kinases), rng.choice(symbols)),
                "random_kinase_cd_protein",
                "directed_phosphorylation",
            )
        path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return records

    def _validate_node(self, node: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        item_dir = self.run_dir / "validation" / "nodes" / node["symbol"]
        result_path = item_dir / "raw_validation.json"
        retrieval = RetrievalSession(item_dir, max_searches=8, max_papers=10)
        if result_path.exists():
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            self._restore_retrieval(retrieval)
        else:
            self._restore_retrieval(retrieval)
            raw, responses = self.client.tool_structured(
                model=self.validator_model,
                prompt=node_validation(node),
                schema_name="cd_node_validation", schema=NODE_VALIDATION_SCHEMA,
                reasoning_effort="high", retrieval=retrieval,
                cancel_requested=self.cancel_requested,
            )
            with self._usage_lock:
                self.usage.extend({"role": "node_validation", "response_id": response.get("id"), "model": response.get("model"), "usage": response.get("usage")} for response in responses)
            _write_json_atomic(result_path, raw)
        admitted, audit = [], []
        for item in [*raw.get("presence", []), *raw.get("pka_evidence", [])]:
            candidate = {**item, "target": item.get("target") or node["symbol"]}
            pmid = re.sub(r"\D", "", str(candidate.get("pmid", "")))
            if pmid and pmid not in retrieval.metadata:
                try:
                    retrieval.fetch_abstract(pmid)
                except Exception as exc:
                    audit.append({"kind": "node", "candidate": node["symbol"], "pmid": pmid, "reason": f"pubmed_metadata_fetch_failed:{exc}"})
            sequences = self._sequences_for_item(candidate)
            kept, reason = gate_evidence_item(candidate, retrieved_text_by_pmid=retrieval.documents, metadata_by_pmid=retrieval.metadata, sequence_by_species_symbol=sequences)
            if kept:
                self._annotate_site_mapping(kept, sequences)
                admitted.append(kept)
                if kept.get("site_unverified_reason"):
                    audit.append({"kind": "node", "candidate": node["symbol"], "pmid": item.get("pmid", ""), "reason": kept["site_unverified_reason"], "action": "site_downgraded_to_protein_level"})
            else:
                audit.append({"kind": "node", "candidate": node["symbol"], "pmid": item.get("pmid", ""), "reason": reason})
        return build_node_record(node["symbol"], admitted), audit

    def _validate_edge(self, edge: dict[str, Any], nodes: dict[str, dict[str, Any]], node_records: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        left, right = edge["pair"]
        validation_profile = str(edge.get("validation_profile", "")).strip()
        definition = profile(validation_profile)
        item_dir = (
            self.run_dir / "validation" / "edges"
            / validation_profile / f"{left}__{right}"
        )
        result_path = item_dir / "raw_validation.json"
        policy_path = item_dir / "policy_review_required.json"
        retrieval = RetrievalSession(item_dir, max_searches=12, max_papers=14)
        policy_review: dict[str, Any] | None = None
        if result_path.exists():
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            self._restore_retrieval(retrieval)
        elif policy_path.exists():
            policy_review = json.loads(policy_path.read_text(encoding="utf-8"))
            self._restore_retrieval(retrieval)
            raw = {
                "pair": [left, right],
                "validation_profile": validation_profile,
                "positive_queries_run": [],
                "negative_queries_run": [],
                "evidence": [],
                "notes": "Automated validation was unavailable; manual literature review is required.",
            }
        else:
            self._restore_retrieval(retrieval)
            try:
                raw, responses = self.client.tool_structured(
                    model=self.validator_model,
                    prompt=edge_validation(
                        (
                            nodes.get(left, {"symbol": left}),
                            nodes.get(right, {"symbol": right}),
                        ),
                        validation_profile,
                        self.seed + len(left) + len(right),
                    ),
                    schema_name="cd_pka_stream_edge_validation",
                    schema=EDGE_VALIDATION_SCHEMA,
                    reasoning_effort="high", retrieval=retrieval,
                    cancel_requested=self.cancel_requested,
                )
            except RuntimeError as exc:
                if not _is_bio_policy_error(exc):
                    raise
                policy_review = {
                    "schema_version": 1,
                    "pair": [left, right],
                    "status": "policy_review_required",
                    "provider_code": "bio_policy",
                    "message": "Automated retrospective literature validation was blocked by the provider safety classifier. No biological conclusion was inferred.",
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
                _write_json_atomic(policy_path, policy_review)
                raw = {
                    "pair": [left, right],
                    "validation_profile": validation_profile,
                    "positive_queries_run": [],
                    "negative_queries_run": [],
                    "evidence": [],
                    "notes": "Automated validation was unavailable; manual literature review is required.",
                }
            else:
                with self._usage_lock:
                    self.usage.extend({"role": "edge_validation", "response_id": response.get("id"), "model": response.get("model"), "usage": response.get("usage")} for response in responses)
                _write_json_atomic(result_path, raw)
        admitted, audit = [], []
        if not raw.get("positive_queries_run"):
            audit.append({
                "kind": "edge", "candidate": f"{left}|{right}",
                "validation_profile": validation_profile,
                "reason": "positive_search_not_documented",
            })
        if not raw.get("negative_queries_run"):
            audit.append({
                "kind": "edge", "candidate": f"{left}|{right}",
                "validation_profile": validation_profile,
                "reason": "negative_search_not_documented",
            })
        allowed_methods = set(definition["admissible_methods"])
        for item in raw.get("evidence", []):
            pmid = re.sub(r"\D", "", str(item.get("pmid", "")))
            if pmid and pmid not in retrieval.metadata:
                try:
                    retrieval.fetch_abstract(pmid)
                except Exception as exc:
                    audit.append({"kind": "edge", "candidate": f"{left}|{right}", "validation_profile": validation_profile, "pmid": pmid, "reason": f"pubmed_metadata_fetch_failed:{exc}"})
            if item.get("polarity") == "negative" and not str(
                item.get("negative_controls", "")
            ).strip():
                audit.append({
                    "kind": "edge", "candidate": f"{left}|{right}",
                    "validation_profile": validation_profile,
                    "pmid": item.get("pmid", ""),
                    "reason": "explicit_negative_missing_test_conditions",
                })
                continue
            sequences = self._sequences_for_item(item)
            kept, reason = gate_evidence_item(
                item,
                retrieved_text_by_pmid=retrieval.documents,
                metadata_by_pmid=retrieval.metadata,
                allowed_methods=allowed_methods,
                sequence_by_species_symbol=sequences,
            )
            if kept:
                self._annotate_site_mapping(kept, sequences)
                admitted.append(kept)
                if kept.get("site_unverified_reason"):
                    audit.append({"kind": "edge", "candidate": f"{left}|{right}", "validation_profile": validation_profile, "pmid": item.get("pmid", ""), "reason": kept["site_unverified_reason"], "action": "site_downgraded_to_protein_level"})
            else:
                audit.append({"kind": "edge", "candidate": f"{left}|{right}", "validation_profile": validation_profile, "pmid": item.get("pmid", ""), "reason": reason})
        # Node-level PKA responses are deliberately not converted into edge
        # evidence. Every H1/H0 edge must be supported by a pair-specific assay.
        record = build_stream_reference_record(
            (left, right), validation_profile, admitted
        )
        record["candidate_polarities"] = sorted({
            proposal.get("candidate_polarity", "")
            for proposal in edge.get("proposals", [])
            if proposal.get("candidate_polarity")
        })
        record["proposals"] = edge.get("proposals", [])
        record["decoy_type"] = edge.get("decoy_type", "")
        if policy_review is not None:
            record["validation_status"] = "policy_review_required"
            record["reference_label"] = "UNLABELED"
            record["h1_reference"] = False
            record["h0_reference"] = False
            audit.append({
                "kind": "edge",
                "candidate": f"{left}|{right}",
                "validation_profile": validation_profile,
                "decoy_type": edge.get("decoy_type", ""),
                "reason": "policy_review_required",
                "provider_code": policy_review.get("provider_code", "bio_policy"),
                "action": "manual_literature_review",
            })
        elif record["reference_label"] not in {"H1_POSITIVE", "H0_EXPLICIT_NEGATIVE"}:
            audit.append({"kind": "edge", "candidate": f"{left}|{right}", "validation_profile": validation_profile, "decoy_type": edge.get("decoy_type", ""), "reason": f"reference_label:{record['reference_label']}"})
        elif edge.get("decoy_type"):
            audit.append({"kind": "edge", "candidate": f"{left}|{right}", "validation_profile": validation_profile, "decoy_type": edge["decoy_type"], "reason": "decoy_resolved_requires_manual_review"})
        return record, audit

    def _parallel_validation(
        self,
        items: list[dict[str, Any]],
        worker: Callable[[dict[str, Any]], tuple[dict[str, Any], list[dict[str, Any]]]],
        *,
        label: str,
        progress_start: float,
        progress_end: float,
    ) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
        """Validate independent hypotheses concurrently in stable input order."""

        if not items:
            return []
        worker_count = min(self.validator_concurrency, len(items))
        executor = ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="gt-validator")
        futures: dict[Future[tuple[dict[str, Any], list[dict[str, Any]]]], int] = {}
        try:
            for index, item in enumerate(items):
                futures[executor.submit(worker, item)] = index
            results: dict[int, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
            for completed, future in enumerate(as_completed(futures), start=1):
                results[futures[future]] = future.result()
                fraction = progress_start + (progress_end - progress_start) * completed / len(futures)
                self._checkpoint(
                    f"{label} · {completed}/{len(futures)} · {worker_count} parallel workers",
                    fraction,
                )
            return [results[index] for index in range(len(items))]
        except BaseException:
            for future in futures:
                future.cancel()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

    def run(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc).isoformat()
        self._checkpoint("Starting evidence-class-specific PKA pair searches", 0.01)
        discovered_edges = self.discover_pairs()
        nodes = self.nodes_from_pairs(discovered_edges)
        edges = self.add_edge_decoys(nodes, discovered_edges)
        # Decoys can introduce endpoints that were not proposed by the pair bot.
        # They still need ordinary node validation so edge validation receives the
        # same context as every discovered candidate.
        for edge in edges:
            for symbol in edge["pair"]:
                nodes.setdefault(symbol, {
                    "symbol": symbol,
                    "appearances": 0,
                    "provenance": [],
                    "decoy_endpoint": True,
                })
        for decoy in SEGMENT_DECOYS:
            nodes.setdefault(decoy, {"symbol": decoy, "decoy_type": "segment_marker", "provenance": []})
        node_records, edge_records, audit = [], [], []
        node_items = list(nodes.values())
        for record, rows in self._parallel_validation(
            node_items,
            self._validate_node,
            label="Validating nodes",
            progress_start=0.47,
            progress_end=0.70,
        ):
            node_records.append(record)
            audit.extend(rows)
        node_record_map = {record["symbol"]: record for record in node_records}
        for record, rows in self._parallel_validation(
            edges,
            lambda edge: self._validate_edge(edge, nodes, node_record_map),
            label="Validating edges",
            progress_start=0.70,
            progress_end=0.98,
        ):
            edge_records.append(record)
            audit.extend(rows)
        write_stream_reference_outputs(self.run_dir, edge_records, audit)
        (self.run_dir / "nodes.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in node_records), encoding="utf-8"
        )
        quote_failures = sum(row.get("reason") == "quote_match_below_0.95" for row in audit)
        eligibility_drops = sum(row.get("reason") in {"method_not_experimental", "not_primary_research"} for row in audit)
        decoys = [record for record in edge_records if record.get("decoy_type")]
        profile_counts = {
            profile_id: {
                "candidates": sum(
                    record["validation_profile"] == profile_id
                    for record in edge_records
                ),
                "h1_positive": sum(
                    record["validation_profile"] == profile_id
                    and record["h1_reference"]
                    for record in edge_records
                ),
                "h0_explicit_negative": sum(
                    record["validation_profile"] == profile_id
                    and record["h0_reference"]
                    for record in edge_records
                ),
                "conflict": sum(
                    record["validation_profile"] == profile_id
                    and record["reference_label"] == "CONFLICT"
                    for record in edge_records
                ),
            }
            for profile_id in self.evidence_profiles
        }
        qa = {
            "quote_match_failure_count": quote_failures,
            "experimental_eligibility_drop_count": eligibility_drops,
            "decoy_count": len(decoys),
            "decoy_h1_count": sum(record["h1_reference"] for record in decoys),
            "decoy_h0_count": sum(record["h0_reference"] for record in decoys),
            "reference_counts_by_profile": profile_counts,
            "known_positive_checks": {
                "AQP2_POS_NATIVE": node_record_map.get("AQP2", {}).get("label") == "POS_NATIVE",
                "AVPR2_POS_NATIVE": node_record_map.get("AVPR2", {}).get("label") == "POS_NATIVE",
                "ANY_DIRECT_PKA_H1": any(
                    record["h1_reference"]
                    and any(
                        item.get("pka_relationship") == "direct_pka"
                        for item in record.get("positive_evidence", [])
                    )
                    for record in edge_records
                ),
            },
            "manual_audit_precision": "pending 100-node/200-edge hand audit",
            "cohens_kappa_5_percent_rerun": "pending rerun sample",
            "generator_recall": "requires manual adjudication of positive decoys",
        }
        (self.run_dir / "qa_metrics.json").write_text(json.dumps(qa, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        manifest = {
            "protocol_version": "0.6", "started_at": started,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "api_base_url": self.config["api_base_url"], "api_version": "Responses API v1",
            "deployments": self.config["deployments"],
            "parallelism": {
                "generator_concurrency": self.generator_concurrency,
                "validator_concurrency": self.validator_concurrency,
            },
            "observed_model_versions": sorted({str(item.get("model")) for item in self.usage if item.get("model")}),
            "reasoning": {"generation": "none", "validation": "high"},
            "prompt_hashes": {
                "P1_pair_generation": _sha(pair_generation(
                    "physical_interaction", "positive", "SEARCH_FOCUS", 1, 1
                )),
                "P2_node_validation": _sha(node_validation({"symbol": "SYMBOL"})),
                "P3_edge_validation": _sha(edge_validation(
                    ({"symbol": "A"}, {"symbol": "B"}),
                    "physical_interaction",
                    self.seed,
                )),
            },
            "parameters": {key: value for key, value in self.config.items() if key not in {"api_key", "api_key_env"}},
            "counts": {
                "node_candidates": len(nodes),
                "edge_profile_candidates": len(edges),
                "unique_unordered_pairs": len({tuple(record["unordered_pair"]) for record in edge_records}),
                "decoys": len(decoys),
                "h1_positive_references": sum(record["h1_reference"] for record in edge_records),
                "h0_explicit_negative_references": sum(record["h0_reference"] for record in edge_records),
                "conflicts": sum(record["reference_label"] == "CONFLICT" for record in edge_records),
                "audit_rows": len(audit),
            },
            "usage": self.usage,
        }
        (self.run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self._checkpoint("Ground-truth curation complete", 1.0)
        return manifest


def dry_run(config: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Validate configuration and write a cost/scope preview without any API call."""
    calls_per_round = int(config.get("pair_search_calls_per_round", 24))
    minimum_rounds = int(config.get("min_pair_search_rounds", 3))
    maximum_rounds = int(config.get("max_pair_search_rounds", 10))
    configured_profiles = config.get("evidence_profiles", list(PROFILE_IDS))
    preview = {
        "protocol_version": "0.6", "mode": "dry_run", "api_base_url": config.get("api_base_url"),
        "generator_web_search": bool(config.get("generator_web_search", True)),
        "generator_web_search_required": bool(config.get("generator_web_search_required", True)),
        "generator_web_search_context_size": config.get("generator_web_search_context_size", "low"),
        "generator_concurrency": int(config.get("generator_concurrency", 4)),
        "validator_concurrency": int(config.get("validator_concurrency", 4)),
        "deployments": config.get("deployments"),
        "pair_search_calls_per_round": calls_per_round,
        "minimum_pair_generation_calls": calls_per_round * minimum_rounds,
        "maximum_pair_generation_calls": calls_per_round * maximum_rounds,
        "maximum_pair_candidates": int(config.get("max_pair_candidates", 500)),
        "evidence_profiles": configured_profiles,
        "profile_count": len(configured_profiles),
        "polarity_targets_per_profile": 2,
        "minimum_candidates_per_profile_polarity": int(
            config.get("minimum_candidates_per_profile_polarity", 10)
        ),
        "stopping_rule": (
            f"at least {minimum_rounds} rounds; then stop after "
            f"{int(config.get('pair_search_saturation_rounds', 2))} consecutive rounds "
            f"with fewer than {int(config.get('pair_search_min_new_pairs', 10))} new pairs"
        ),
        "validation_calls": (
            "one node validator per endpoint and one edge validator per retained "
            "unordered-pair/evidence-profile combination"
        ),
        "warning": (
            "Every profile alternates positive and explicit-negative discovery. "
            "Discovery is bounded by the maximum rounds and profile-pair cap. "
            "No paid API call was made."
        ),
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "dry_run.json").write_text(json.dumps(preview, indent=2) + "\n", encoding="utf-8")
    return preview
