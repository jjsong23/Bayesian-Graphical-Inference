"""Checkpointed, fail-closed path-selection workflow."""

from __future__ import annotations

import json
import math
import re
import threading
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .client import ResponsesClient
from .database import RunDatabase, now
from .grounding import compact_evidence, quotation_excerpt, quote_is_present, retrieve, source_type, stable_id, validate_narrative
from .ingest import canonical_edge, import_agent_graphs
from .prompts import edge_research_prompt, narrative_audit_prompt, node_research_prompt, path_review_prompt, shortlist_prompt, verifier_prompt
from .schemas import NARRATIVE_AUDIT_SCHEMA, PATH_REVIEW_SCHEMA, SHORTLIST_SCHEMA, VERIFY_SCHEMA, research_schema


class Cancelled(RuntimeError):
    pass


def chunks(values: list[Any], size: int) -> Iterable[list[Any]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _url_key(url: str) -> str:
    parsed = urlsplit(url.strip())
    return urlunsplit((parsed.scheme.casefold(), parsed.netloc.casefold(), parsed.path.rstrip("/"), "", ""))


class PathPickerWorkflow:
    def __init__(
        self, *, run_directory: str | Path, input_path: str | Path,
        configuration: dict[str, Any], api_key: str | None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        self.run_directory = Path(run_directory)
        self.run_directory.mkdir(parents=True, exist_ok=True)
        self.database = RunDatabase(self.run_directory / "path_picker.sqlite3")
        saved_config = self.database.get_meta("configuration", {}, decode=True)
        if saved_config and saved_config.get("workflow_mode", "path_first") != configuration.get("workflow_mode", "path_first"):
            raise ValueError("Changing workflow mode requires a new run; use the old run as an evidence seed instead")
        self.database.set_meta("cancel_requested", "false")
        self.input_path = Path(input_path)
        self.config = configuration
        self.api_key = (api_key or "").strip()
        self.cancel_event = cancel_event or threading.Event()
        self.client: ResponsesClient | None = None
        if self.api_key:
            self.client = ResponsesClient(
                base_url=str(configuration["api_base_url"]), api_key=self.api_key,
                timeout=int(configuration.get("timeout_seconds", 300)),
                usage_log=self.run_directory / "api_usage.jsonl",
                max_output_tokens=int(configuration.get("max_output_tokens", 32000)),
                max_tool_calls=int(configuration.get("max_tool_calls", 6)),
                max_total_calls=int(configuration.get("max_total_api_calls", configuration.get("max_total_calls", 10000))),
                cancel_event=self.cancel_event,
            )
        # Deliberately exclude the API key from every persisted artifact.
        persisted = {key: value for key, value in configuration.items() if "key" not in key.casefold()}
        (self.run_directory / "configuration.json").write_text(json.dumps(persisted, indent=2) + "\n", encoding="utf-8")
        self.database.set_meta("configuration", persisted)

    def check_cancel(self) -> None:
        if self.cancel_event.is_set() or self.database.get_meta("cancel_requested") == "true":
            self.database.set_meta("status", "cancelled")
            raise Cancelled("Path Picker run cancelled by user")

    def run(self, *, inventory_only: bool = False) -> dict[str, Any]:
        self.database.set_meta("status", "running")
        inventory = self.database.get_meta("inventory", decode=True)
        if not isinstance(inventory, dict):
            inventory = import_agent_graphs(self.input_path, self.database)
            (self.run_directory / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
        if inventory_only:
            if self.config.get("workflow_mode") == "rag_path_first":
                from .rag import RagPathRunner
                RagPathRunner(self).initialize()
            self.database.set_meta("status", "inventory_complete")
            return {"status": "inventory_complete", "inventory": inventory}
        if self.client is None:
            raise ValueError("Production workflow requires an API key")
        try:
            mode = str(self.config.get("workflow_mode", "path_first"))
            if mode == "path_first":
                from .adaptive import AdaptivePathRunner
                AdaptivePathRunner(self).run()
            elif mode == "rag_path_first":
                from .rag import RagPathRunner
                RagPathRunner(self).run()
            elif mode == "evidence_first":
                self._review_nodes()
                self._mark_node_viable_paths_and_prepare_edges()
                self._review_edges()
                self._score_all_paths()
                self._deep_review_paths()
            else:
                raise ValueError("workflow_mode must be rag_path_first, path_first or evidence_first")
            result = self._synthesize()
            self.database.set_meta("status", "complete")
            self.database.set_meta("shortlist_stale", "false")
            self.database.set_meta("adaptive_stage", "complete")
            if mode == "rag_path_first":
                self.database.set_meta("rag_stage", "complete")
            self.database.event("complete", f"Completed with {len(result['cards'])} shortlisted hypotheses")
            return result
        except Cancelled:
            raise
        except Exception as exc:
            self.check_cancel()
            self.database.set_meta("status", "needs_attention")
            self.database.event("error", str(exc), level="error")
            raise
        finally:
            if self.config.get("workflow_mode", "path_first") in {"path_first", "rag_path_first"}:
                from .adaptive import export_adaptive_audit
                export_adaptive_audit(self)

    def _ensure_tasks(self, stage: str, item_type: str, item_ids: Iterable[str]) -> None:
        with self.database.connect() as db:
            for item_id in item_ids:
                db.execute(
                    """INSERT OR IGNORE INTO tasks(task_id,stage,item_type,item_id,status,updated_at)
                    VALUES (?,?,?,?,?,?)""",
                    (f"{stage}:{item_type}:{item_id}", stage, item_type, item_id, "pending", now()),
                )

    def _pending(self, stage: str, limit: int) -> list[str]:
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT item_id FROM tasks WHERE stage=? AND status IN ('pending','retryable') ORDER BY item_id LIMIT ?",
                (stage, limit),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def _task_result(self, stage: str, item_id: str) -> dict[str, Any] | None:
        with self.database.connect() as db:
            row = db.execute("SELECT result_json FROM tasks WHERE stage=? AND item_id=? AND status='complete'", (stage, item_id)).fetchone()
        return json.loads(row[0]) if row and row[0] else None

    def _set_task(self, stage: str, item_id: str, status: str, *, result: Any = None, error: str | None = None) -> None:
        with self.database.connect() as db:
            db.execute(
                """UPDATE tasks SET status=?,attempts=attempts+1,error=?,result_json=?,updated_at=?
                WHERE stage=? AND item_id=?""",
                (status, error, json.dumps(result, ensure_ascii=False) if result is not None else None, now(), stage, item_id),
            )

    def _payloads(self, table: str, id_column: str, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        with self.database.connect() as db:
            rows = db.execute(f"SELECT {id_column},payload_json FROM {table} WHERE {id_column} IN ({placeholders})", ids).fetchall()
        return {str(row[0]): json.loads(row[1]) for row in rows}

    def _review_nodes(self) -> None:
        with self.database.connect() as db:
            identifiers = [str(row[0]) for row in db.execute("SELECT node_id FROM nodes ORDER BY node_id")]
        self._ensure_tasks("node_research", "node", identifiers)
        size = int(self.config.get("node_batch_size", 2))
        while pending := self._pending("node_research", size):
            self.check_cancel()
            payloads = self._payloads("nodes", "node_id", pending)
            items = [{"item_id": identifier, "bgi": compact_evidence(payloads[identifier])} for identifier in pending]
            self._research_batch("node", items, node_research_prompt(items), pending)

    def _review_edges(self) -> None:
        size = int(self.config.get("edge_batch_size", 1))
        while pending := self._pending("edge_research", size):
            self.check_cancel()
            payloads = self._payloads("edges", "edge_id", pending)
            items = [{"item_id": identifier, "bgi": compact_evidence(payloads[identifier])} for identifier in pending]
            self._research_batch("edge", items, edge_research_prompt(items), pending)

    def _research_batch(self, kind: str, items: list[dict[str, Any]], prompt: str, expected: list[str]) -> None:
        assert self.client is not None
        stage = f"{kind}_research"
        try:
            result, raw = self.client.structured(
                model=str(self.config["research_model"]), prompt=prompt,
                schema_name=f"path_picker_{kind}_research", schema=research_schema(kind),
                reasoning_effort=str(self.config.get("research_reasoning", "medium")),
                web_search=True, require_web_search=True,
                search_context_size=str(self.config.get("web_search_context_size", "high")),
            )
            searched_urls = self.client.source_urls(raw)
            by_id = {str(item.get("item_id")): item for item in result.get("items", [])}
            for item_id in expected:
                self.check_cancel()
                if item_id not in by_id:
                    self._set_task(stage, item_id, "retryable", error="Research response omitted item")
                    continue
                reviewed = self._ground_item(kind, item_id, by_id[item_id], searched_urls)
                self._set_task(stage, item_id, "complete", result=reviewed)
        except Exception as exc:
            for item_id in expected:
                if self._task_result(stage, item_id) is None:
                    self._set_task(stage, item_id, "retryable", error=str(exc))
            raise

    def _ground_item(self, kind: str, item_id: str, review: dict[str, Any], searched_urls: set[str]) -> dict[str, Any]:
        accepted: list[str] = []
        rejected: list[dict[str, str]] = []
        search_keys = {_url_key(url) for url in searched_urls}
        for proposed in review.get("claims", []):
            self.check_cancel()
            claim_id, failure = self._ground_claim(kind, item_id, proposed, search_keys)
            if failure:
                rejected.append({"claim_id": claim_id, "reason": failure})
            else:
                accepted.append(claim_id)
        verdict = str(review.get("verdict", "uncertain"))
        with self.database.connect() as db:
            polarities = [str(row[0]) for row in db.execute(
                "SELECT polarity FROM claims WHERE item_type=? AND item_id=? AND accepted=1", (kind, item_id)
            )]
        if not accepted:
            verdict = "uncertain"
        elif kind == "edge" and verdict == "supported" and "positive" not in polarities:
            verdict = "uncertain"
        elif kind == "edge" and verdict == "contradicted" and "negative" not in polarities:
            verdict = "uncertain"
        return {**review, "verdict": verdict, "accepted_claim_ids": accepted, "rejected_claims": rejected}

    def _ground_claim(self, kind: str, item_id: str, claim: dict[str, Any], search_keys: set[str]) -> tuple[str, str | None]:
        assert self.client is not None
        url = str(claim.get("url", "")).strip()
        quotation = str(claim.get("quotation", "")).strip()[:500]
        claim_id = stable_id("CLM", kind, item_id, str(claim.get("subject")), str(claim.get("predicate")), str(claim.get("object")), url,
                             json.dumps(claim.get("context", {}), sort_keys=True), str(claim.get("polarity")), quotation)
        with self.database.connect() as db:
            existing = db.execute("""SELECT 1 FROM claims c JOIN sources s USING(source_id)
                WHERE c.claim_id=? AND c.accepted=1 AND c.quote_verified=1
                AND c.entailment_verified=1 AND c.context_verified=1
                AND s.retrieved=1 AND s.retracted=0""", (claim_id,)).fetchone()
        if existing and existing[0]:
            return claim_id, None
        source_id = stable_id("SRC", url)
        failure: str | None = None
        retrieved = None
        if _url_key(url) not in search_keys:
            failure = "URL was not returned by the hosted web search"
        else:
            try:
                retrieved = retrieve(url, timeout=int(self.config.get("source_timeout_seconds", 30)))
                if retrieved.retracted:
                    failure = "Source carries a retraction/withdrawal marker"
                elif not quote_is_present(quotation, retrieved.text):
                    failure = "Quotation was not found in the retrieved source"
            except Exception as exc:
                failure = str(exc)
        with self.database.connect() as db:
            existing_source = db.execute("SELECT * FROM sources WHERE source_id=?", (source_id,)).fetchone()
            source_metadata = json.loads(existing_source["metadata_json"]) if existing_source else {}
            source_metadata["proposed_source_type"] = claim.get("source_type")
            # A failed lookup for one new claim must not erase a previously
            # retrieved source or its accepted quotation excerpts.
            if retrieved is not None or existing_source is None:
                db.execute(
                    """INSERT OR REPLACE INTO sources
                    (source_id,url,title,source_type,pmid,doi,retracted,retrieved,http_status,text_hash,retrieved_at,metadata_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        source_id, url, (retrieved.title if retrieved else str(claim.get("title", ""))), source_type(url),
                        str(claim.get("pmid", "")), str(claim.get("doi", "")),
                        int(bool(retrieved and retrieved.retracted)), int(retrieved is not None),
                        retrieved.status if retrieved else None, retrieved.text_hash if retrieved else None,
                        now() if retrieved else None, json.dumps(source_metadata),
                    ),
                )
        verification: dict[str, Any] = {}
        if failure is None and retrieved is not None:
            try:
                verification, _ = self.client.structured(
                    model=str(self.config["verification_model"]),
                    prompt=verifier_prompt(claim, quotation_excerpt(retrieved.text, quotation),
                        {"item_type": kind, "item_id": item_id} if self.config.get("workflow_mode") == "rag_path_first" else None),
                    schema_name="path_picker_claim_verification",
                    schema=VERIFY_SCHEMA, reasoning_effort=str(self.config.get("verification_reasoning", "high")),
                )
                if not verification.get("entails_claim"):
                    failure = "Independent verifier found no entailment"
                elif not verification.get("context_matches"):
                    failure = "Independent verifier found a context mismatch"
                elif not verification.get("direction_matches"):
                    failure = "Independent verifier found a direction mismatch"
                elif not verification.get("is_primary_or_authoritative"):
                    failure = "Source was not primary research or an authoritative curated record"
            except Exception as exc:
                # Transport failures, cancellation and the spending safety cap are
                # not biological uncertainty. Stop/resume, rather than caching a
                # completed research item with its verification silently skipped.
                self.check_cancel()
                raise RuntimeError(f"Independent verification could not complete: {exc}") from exc
        accepted = int(failure is None)
        with self.database.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO claims
                (claim_id,item_type,item_id,subject,predicate,object,context_json,source_id,quotation,
                 quote_verified,entailment_verified,context_verified,accepted,polarity,failure_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    claim_id, kind, item_id, str(claim.get("subject", "")), str(claim.get("predicate", "")),
                    str(claim.get("object", "")), json.dumps(claim.get("context", {}), ensure_ascii=False),
                    source_id, quotation, int(retrieved is not None and quote_is_present(quotation, retrieved.text)),
                    int(bool(verification.get("entails_claim"))), int(bool(verification.get("context_matches"))),
                    accepted, str(claim.get("polarity", "positive")), failure,
                ),
            )
            if accepted and retrieved is not None:
                row = db.execute("SELECT metadata_json FROM sources WHERE source_id=?", (source_id,)).fetchone()
                metadata = json.loads(row[0]) if row else {}
                excerpts = metadata.setdefault("verified_excerpts", [])
                excerpt = quotation_excerpt(retrieved.text, quotation)
                if excerpt not in excerpts:
                    excerpts.append(excerpt)
                db.execute("UPDATE sources SET metadata_json=? WHERE source_id=?", (json.dumps(metadata, ensure_ascii=False), source_id))
        return claim_id, failure

    def _mark_node_viable_paths_and_prepare_edges(self) -> None:
        with self.database.connect() as db:
            reviews = {str(row[0]): json.loads(row[1]) for row in db.execute(
                "SELECT item_id,result_json FROM tasks WHERE stage='node_research' AND status='complete'"
            )}
            paths = db.execute("SELECT path_id,path_symbols FROM paths").fetchall()
            edge_ids: set[str] = set()
            for row in paths:
                symbols = [value.strip() for value in str(row["path_symbols"]).split("->")]
                blocked = [symbol for symbol in symbols[1:-1] if reviews.get(symbol, {}).get("verdict") == "nonpropagating"]
                status = "excluded_nonpropagating_node" if blocked else "node_viable"
                db.execute("UPDATE paths SET deterministic_status=? WHERE path_id=?", (status, row["path_id"]))
                if not blocked:
                    edge_ids.update(canonical_edge(left, right) for left, right in zip(symbols, symbols[1:]))
        self._ensure_tasks("edge_research", "edge", sorted(edge_ids, key=str.casefold))
        self.database.event("path_filter", f"Prepared {len(edge_ids)} unique edges from node-viable paths")

    def _score_all_paths(self) -> None:
        with self.database.connect() as db:
            edge_reviews = {str(row[0]): json.loads(row[1]) for row in db.execute(
                "SELECT item_id,result_json FROM tasks WHERE stage='edge_research' AND status='complete'"
            )}
            for row in db.execute("SELECT path_id,path_symbols,primary_score,hop_count,deterministic_status FROM paths"):
                if row["deterministic_status"] != "node_viable":
                    continue
                symbols = [value.strip() for value in str(row["path_symbols"]).split("->")]
                reviews = [edge_reviews.get(canonical_edge(a, b), {}) for a, b in zip(symbols, symbols[1:])]
                contradicted = sum(review.get("verdict") == "contradicted" for review in reviews)
                supported = sum(review.get("verdict") == "supported" for review in reviews)
                unknown = sum(review.get("verdict") == "uncertain" for review in reviews)
                base = math.log(max(float(row["primary_score"]), 1e-12))
                score = base + 0.30 * supported - 0.85 * contradicted - 0.05 * unknown - 0.025 * int(row["hop_count"])
                status = "excluded_contradicted_edge" if contradicted else "eligible"
                db.execute("UPDATE paths SET deterministic_status=?,deterministic_score=? WHERE path_id=?", (status, score, row["path_id"]))
        self.database.event("path_scoring", "Deterministically assessed every imported path")

    def _candidate_path_ids(self) -> list[str]:
        limit = max(1, int(self.config.get("max_deep_path_reviews", 50 if self.config.get("workflow_mode", "path_first") in {"path_first", "rag_path_first"} else 200)))
        with self.database.connect() as db:
            rows = db.execute(
                "SELECT path_id,query_id,deterministic_score FROM paths WHERE deterministic_status='eligible' ORDER BY deterministic_score DESC,rank ASC"
            ).fetchall()
        if self.config.get("workflow_mode", "path_first") in {"path_first", "rag_path_first"}:
            from .adaptive import target_fair_order
            return target_fair_order(rows)[:limit]
        selected: list[str] = []
        seen_queries: set[str] = set()
        for row in rows:
            if row["query_id"] not in seen_queries:
                selected.append(str(row["path_id"]))
                seen_queries.add(str(row["query_id"]))
                if len(selected) >= limit:
                    return selected
        for row in rows:
            identifier = str(row["path_id"])
            if identifier not in selected:
                selected.append(identifier)
                if len(selected) >= limit:
                    break
        return selected

    def _path_bundle(self, path_id: str) -> dict[str, Any]:
        bundle = self._base_path_bundle(path_id)
        enricher = getattr(self, "_rag_enricher", None)
        return enricher(bundle) if enricher else bundle

    def _base_path_bundle(self, path_id: str) -> dict[str, Any]:
        with self.database.connect() as db:
            row = db.execute("SELECT * FROM paths WHERE path_id=?", (path_id,)).fetchone()
            if row is None:
                raise ValueError(f"Unknown path {path_id}")
            path = json.loads(row["payload_json"])
            symbols = [value.strip() for value in str(row["path_symbols"]).split("->")]
            node_rows = db.execute(
                f"SELECT node_id,payload_json FROM nodes WHERE node_id IN ({','.join('?' for _ in symbols)})", symbols
            ).fetchall()
            edge_ids = [canonical_edge(a, b) for a, b in zip(symbols, symbols[1:])]
            edge_rows = db.execute(
                f"SELECT edge_id,payload_json FROM edges WHERE edge_id IN ({','.join('?' for _ in edge_ids)})", edge_ids
            ).fetchall()
            # A node/edge can have different BF evidence in different imported
            # sessions. Never silently show another session's highest score.
            session_payloads = {}
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_evidence'").fetchone():
                session_payloads = {(item["item_type"], item["item_id"]): json.loads(item["payload_json"])
                    for item in db.execute(f"""SELECT item_type,item_id,payload_json FROM session_evidence
                        WHERE session_id=? AND ((item_type='node' AND item_id IN ({','.join('?' for _ in symbols)}))
                        OR (item_type='edge' AND item_id IN ({','.join('?' for _ in edge_ids)})))""", [row["session_id"], *symbols, *edge_ids])}
            claim_rows = db.execute(
                f"""SELECT c.* ,s.url,s.title FROM claims c JOIN sources s USING(source_id)
                WHERE c.accepted=1 AND c.quote_verified=1 AND c.entailment_verified=1
                AND c.context_verified=1 AND s.retrieved=1 AND s.retracted=0
                AND ((c.item_type='node' AND c.item_id IN ({','.join('?' for _ in symbols)}))
                OR (c.item_type='edge' AND c.item_id IN ({','.join('?' for _ in edge_ids)})))""",
                [*symbols, *edge_ids],
            ).fetchall()
        claims = [{key: value for key, value in dict(item).items() if key not in {"quotation"}} for item in claim_rows]
        return {
            "path_id": path_id, "path": path, "path_symbols": symbols, "query_id": row["query_id"],
            "session_id": row["session_id"], "biological_context": self.config.get("biological_context", {}),
            "deterministic_score": row["deterministic_score"],
            "nodes": [compact_evidence(session_payloads.get(("node", item["node_id"]), json.loads(item["payload_json"]))) for item in node_rows],
            "edges": [compact_evidence(session_payloads.get(("edge", item["edge_id"]), json.loads(item["payload_json"]))) for item in edge_rows],
            "accepted_claims": claims,
        }

    def _deep_review_paths(self) -> None:
        assert self.client is not None
        identifiers = self._candidate_path_ids()
        self._ensure_tasks("path_review", "path", identifiers)
        # Pending reviews outside the current eligible shortlist must not drain
        # on a resumed run after a rule changes eligibility.
        with self.database.connect() as db:
            if identifiers:
                placeholders = ",".join("?" for _ in identifiers)
                db.execute(f"UPDATE tasks SET status='deferred' WHERE stage='path_review' AND status IN ('pending','retryable') AND item_id NOT IN ({placeholders})", identifiers)
                db.execute(f"UPDATE tasks SET status='pending' WHERE stage='path_review' AND status='deferred' AND item_id IN ({placeholders})", identifiers)
        while pending := self._pending("path_review", 1):
            self.check_cancel()
            path_id = pending[0]
            try:
                bundle = self._path_bundle(path_id)
                result, _ = self.client.structured(
                    model=str(self.config["reasoning_model"]), prompt=path_review_prompt(bundle),
                    schema_name="path_picker_path_review", schema=PATH_REVIEW_SCHEMA,
                    reasoning_effort=str(self.config.get("path_reasoning", "high")),
                )
                allowed = {item["claim_id"] for item in bundle["accepted_claims"]}
                referenced = set(result.get("supporting_claim_ids", [])) | set(result.get("contradicting_claim_ids", []))
                unknown = referenced - allowed
                if unknown:
                    raise RuntimeError("Path review referenced unknown claims: " + ", ".join(sorted(unknown)))
                self._set_task("path_review", path_id, "complete", result=result)
            except Exception as exc:
                self._set_task("path_review", path_id, "retryable", error=str(exc))
                raise

    def _accepted_claims(self, claim_ids: set[str] | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT c.claim_id,c.subject,c.predicate,c.object,c.context_json,c.polarity,
                c.quotation,s.url,s.title,s.pmid,s.doi FROM claims c JOIN sources s USING(source_id)
                WHERE c.accepted=1 AND c.quote_verified=1 AND c.entailment_verified=1
                AND c.context_verified=1 AND s.retrieved=1 AND s.retracted=0 ORDER BY c.claim_id"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["context"] = json.loads(item.pop("context_json"))
            if claim_ids is None or item["claim_id"] in claim_ids:
                result.append(item)
        return result

    def _synthesize(self) -> dict[str, Any]:
        assert self.client is not None
        with self.database.connect() as db:
            rows = db.execute(
                """SELECT t.item_id,t.result_json,p.query_id,p.path_symbols,p.deterministic_score,p.primary_score
                FROM tasks t JOIN paths p ON p.path_id=t.item_id
                WHERE t.stage='path_review' AND t.status='complete' AND p.deterministic_status='eligible'"""
            ).fetchall()
        candidates = []
        used_claims: set[str] = set()
        for row in rows:
            review = json.loads(row["result_json"])
            used_claims.update(review.get("supporting_claim_ids", []))
            used_claims.update(review.get("contradicting_claim_ids", []))
            candidates.append({
                "path_id": row["item_id"], "query_id": row["query_id"], "path_symbols": row["path_symbols"],
                "deterministic_score": row["deterministic_score"], "bgi_primary_score": row["primary_score"],
                "review": review,
            })
        requested_minimum = int(self.config.get("shortlist_minimum", 5))
        if not candidates:
            raise RuntimeError("No eligible paths survived; inspect the coverage and exclusion ledgers. No hypotheses were fabricated.")
        minimum = min(requested_minimum, len(candidates))
        maximum = min(int(self.config.get("shortlist_maximum", 12)), len(candidates), 12)
        minimum = min(minimum, maximum)
        if len(candidates) < requested_minimum:
            self.database.set_meta("shortlist_warning", f"Only {len(candidates)} eligible reviewed paths; fewer than the requested {requested_minimum}. Do not force unsupported hypotheses.")
        claims = self._accepted_claims(used_claims)
        draft_id = stable_id("DRAFT", json.dumps(candidates, sort_keys=True), json.dumps(claims, sort_keys=True), str(minimum), str(maximum))
        self._ensure_tasks("synthesis_draft", "draft", [draft_id])
        result = self._task_result("synthesis_draft", draft_id)
        if result is None:
            self.check_cancel()
            result, _ = self.client.structured(
                model=str(self.config["synthesis_model"]),
                prompt=shortlist_prompt(candidates, claims, minimum, maximum),
                schema_name="path_picker_shortlist", schema=SHORTLIST_SCHEMA,
                reasoning_effort=str(self.config.get("synthesis_reasoning", "high")),
            )
        accepted = {item["claim_id"] for item in claims}
        candidate_ids = {item["path_id"] for item in candidates}
        validation_errors: list[str] = []
        if not 1 <= len(result.get("cards", [])) <= maximum:
            validation_errors.append("Shortlist count is outside the available/requested range")
        if len(result.get("cards", [])) < requested_minimum:
            self.database.set_meta("shortlist_warning", f"Only {len(result.get('cards', []))} hypotheses were shortlisted; target was {requested_minimum}. No padding was required.")
        if len({card.get("path_id") for card in result.get("cards", [])}) != len(result.get("cards", [])):
            validation_errors.append("Shortlist contains duplicate paths")
        for index, card in enumerate(result.get("cards", []), start=1):
            if card.get("path_id") not in candidate_ids:
                validation_errors.append(f"Card {index} selected an unknown path")
            validation_errors.extend(f"Card {index}: {error}" for error in validate_narrative(str(card.get("abstract", "")), accepted))
            unknown = set(card.get("supporting_claim_ids", [])) - accepted
            if unknown:
                validation_errors.append(f"Card {index} lists unaccepted claims: {sorted(unknown)}")
        if validation_errors:
            raise RuntimeError("Shortlist narrative validation failed: " + " | ".join(validation_errors))
        # A failed independent audit must not erase/rebill the entire successful
        # draft and earlier audits on resume. This is NOT a finalized shortlist.
        self._set_task("synthesis_draft", draft_id, "complete", result=result)
        for card in result["cards"]:
            self._audit_narrative(card["abstract"], card, claims, self._path_bundle(card["path_id"]))
        self._audit_narrative(result["network_synthesis"], {"network_synthesis": result["network_synthesis"]}, claims, {"candidate_reviews": candidates})
        with self.database.connect() as db:
            db.execute("DELETE FROM shortlist")
            for position, card in enumerate(result["cards"], start=1):
                path = next(item for item in candidates if item["path_id"] == card["path_id"])
                score = float(path["deterministic_score"] or 0) + float(path["review"]["mechanistic_coherence"])
                db.execute("INSERT INTO shortlist(position,path_id,score,card_json) VALUES (?,?,?,?)", (position, card["path_id"], score, json.dumps(card, ensure_ascii=False)))
        handoff = {"schema_version": 1, "generated_at": now(), "cards": result["cards"], "network_synthesis": result["network_synthesis"], "accepted_claims": claims, "candidate_reviews": candidates}
        (self.run_directory / "shortlist.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (self.run_directory / "llm_handoff.json").write_text(json.dumps(handoff, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        from .report import write_report
        write_report(self.run_directory, result, candidates, claims)
        return result

    def _audit_narrative(self, text: str, card: dict[str, Any], claims: list[dict[str, Any]], context: dict[str, Any]) -> None:
        """Independent sentence-level entailment check, not just citation syntax."""
        assert self.client is not None
        sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]
        if not sentences:
            raise RuntimeError("Empty final narrative")
        audit_id = stable_id("AUDIT", json.dumps(card, sort_keys=True), json.dumps(claims, sort_keys=True), json.dumps(context, sort_keys=True))
        self._ensure_tasks("narrative_audit", "narrative", [audit_id])
        audit = self._task_result("narrative_audit", audit_id)
        if audit is None:
            self.check_cancel()
            audit, _ = self.client.structured(model=str(self.config["verification_model"]),
                prompt=narrative_audit_prompt(sentences, card, claims, context), schema_name="path_picker_narrative_audit",
                schema=NARRATIVE_AUDIT_SCHEMA, reasoning_effort=str(self.config.get("verification_reasoning", "high")))
        checks = audit.get("sentence_checks", [])
        issues = list(audit.get("issues", []))
        if sorted(item.get("sentence_index", 0) for item in checks) != list(range(1, len(sentences)+1)):
            issues.append("Independent audit did not check every sentence exactly once")
        accepted = {claim["claim_id"] for claim in claims}
        for check in checks:
            index = int(check.get("sentence_index", 0))
            sentence = sentences[index-1] if 1 <= index <= len(sentences) else ""
            referenced = set(check.get("claim_ids", []))
            if referenced - accepted:
                issues.append(f"Sentence {index}: audit cited unavailable claims")
            if any(f"[{identifier}]" not in sentence for identifier in referenced):
                issues.append(f"Sentence {index}: audit relied on a claim not cited by the sentence")
            supported = check.get("supported_by_cited_evidence") is True and bool(referenced)
            inference = check.get("clearly_labeled_defensible_inference") is True and "[INFERENCE]" in sentence
            if not supported and not inference:
                issues.append(f"Sentence {index}: {check.get('explanation', 'Unresolved support')}")
        if not audit.get("other_card_fields_grounded_or_explicit_proposals"):
            issues.append("Non-abstract card fields contain unsupported assertions")
        if issues:
            self._set_task("narrative_audit", audit_id, "retryable", result=audit, error=" | ".join(issues))
            raise RuntimeError("Independent narrative audit needs attention: " + " | ".join(issues))
        self._set_task("narrative_audit", audit_id, "complete", result=audit)

