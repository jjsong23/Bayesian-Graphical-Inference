"""Shared-RAG whole-path research, with bounded follow-ups and fail-closed claims."""
from __future__ import annotations

import hashlib
import copy
import json
import math
from pathlib import Path
from typing import Any

from .adaptive import AdaptivePathRunner, adaptive_progress, export_adaptive_audit, target_fair_order
from .database import now
from .grounding import stable_id
from .ingest import canonical_edge
from .knowledge import CLAIM_FIELDS, SharedKnowledgeBase
from .rag_prompts import rag_review_prompt, rag_review_schema


POLICY = "shared-rag-v1"


class RagPathRunner(AdaptivePathRunner):
    def __init__(self, workflow) -> None:
        super().__init__(workflow)
        default = Path(__file__).resolve().parents[1] / "runtime" / "shared_knowledge" / "evidence.sqlite3"
        self.knowledge = SharedKnowledgeBase(self.config.get("rag_kb_path") or default)
        self.max_rounds = int(self.config.get("rag_max_rounds", 2))
        if not 1 <= self.max_rounds <= 2:
            raise ValueError("rag_max_rounds must be between 1 and 2")
        workflow._rag_enricher = self.enrich_bundle

    def initialize(self) -> None:
        super().initialize()
        signature = stable_id("RAG", POLICY, str(self.knowledge.path.resolve()), json.dumps(self.context, sort_keys=True))
        previous = self.database.get_meta("rag_policy_fingerprint")
        if previous and previous != signature:
            raise ValueError("RAG context/library policy changed; create a new run to preserve checkpoint provenance")
        self.database.set_meta("rag_policy_fingerprint", signature)
        seed = str(self.config.get("rag_seed_run") or "").strip()
        if seed:
            source = Path(seed)
            if source.is_dir():
                source /= "path_picker.sqlite3"
            if not source.is_file():
                raise ValueError(f"RAG evidence seed not found: {source}")
            summary = self.knowledge.import_run(source)
            self.database.set_meta("rag_seed_summary", summary)
        self.knowledge.import_run(self.database.path)
        self._update_progress("ready")
        self.export_audit()

    def enrich_bundle(self, bundle: dict[str, Any]) -> dict[str, Any]:
        symbols = bundle["path_symbols"]
        edge_ids = [canonical_edge(a, b) for a, b in zip(symbols, symbols[1:])]
        result = self.knowledge.retrieve(
            symbols=symbols, edge_ids=edge_ids, context=self.context,
            query=" ".join(symbols) + " acute signaling direction phosphorylation scaffolding trafficking " + json.dumps(self.context),
            limit=int(self.config.get("rag_claim_limit", 40)),
            max_chars=int(self.config.get("rag_context_chars", 24000)),
        )
        retrieved_ids = [c["claim_id"] for c in result["accepted_claims"]]
        self.knowledge.hydrate_run(retrieved_ids, self.database)
        # Hydration may reject a local conflict. Only locally valid, retrieved IDs
        # may reach the prompt or its per-request enum.
        local_ids = {c["claim_id"] for c in self.workflow._accepted_claims(set(retrieved_ids))}
        with self.database.connect() as db:
            local = {r["claim_id"]: dict(r) for r in db.execute("SELECT c.*,s.url,s.text_hash FROM claims c JOIN sources s USING(source_id)")}
        # Matching IDs alone are not enough: hydration intentionally preserves
        # local decisions, so conflicting local content must not be replaced by
        # a shared-library version in the prompt.
        local_ids = {c["claim_id"] for c in result["accepted_claims"] if c["claim_id"] in local_ids
                     and all(local[c["claim_id"]].get(k) == c.get(k) for k in CLAIM_FIELDS)
                     and local[c["claim_id"]]["url"] == c["url"]
                     and local[c["claim_id"]]["text_hash"] == c.get("source_text_hash")}
        bundle["accepted_claims"] = [c for c in result["accepted_claims"] if c["claim_id"] in local_ids]
        for claim in bundle["accepted_claims"]:
            claim["status_support_eligible"] = self._status_support_eligible(claim, symbols)
        bundle["biological_context"] = self.context
        bundle["available_research_items"] = {"node": symbols, "edge": edge_ids}
        bundle["rag"] = {
            "policy": POLICY, "revision": result.get("revision", result.get("stats", {}).get("revision")),
            "stats": result.get("stats", {}),
            "passages": [p for p in result.get("passages", []) if p.get("claim_id") in local_ids],
            "scoped_investigations": result.get("coverage", []),
            "warning": "Only atomic accepted claims are verified; retrieval similarity and surrounding prose are not evidence strength.",
        }
        return bundle

    def _bundle(self, path_id: str) -> dict[str, Any]:
        # Deliberately do not use AdaptivePathRunner._bundle: that contains
        # provisional researcher summaries, even when all their claims failed.
        return self.workflow._path_bundle(path_id)

    def run(self) -> None:
        self.initialize()
        try:
            self._update_progress("reviewing_paths")
            self._process_proposals()
            while True:
                self.workflow.check_cancel()
                with self.database.connect() as db:
                    rows = db.execute("""SELECT p.* FROM paths p JOIN path_assessments a USING(path_id)
                        WHERE a.status='pending' ORDER BY p.rank,p.query_id,p.path_id""").fetchall()
                pending = target_fair_order(rows)[:max(1, int(self.config.get("path_round_size", 10)))]
                if not pending:
                    break
                for path_id in pending:
                    self._review_path(path_id)
                self._process_proposals()
                self.export_audit()
            self._update_progress("auditing_finalists")
            self.database.set_meta("adaptive_finalist_ids", self.workflow._candidate_path_ids())
            # Independent finalist audit uses fresh bounded retrieval rather than
            # the knowledge snapshot available to the early screening calls.
            self.workflow._deep_review_paths()
            self._update_progress("synthesizing")
        finally:
            self.export_audit()

    def _review_path(self, path_id: str) -> None:
        wf = self.workflow
        if wf._task_result("path_screen", path_id) is not None:
            return
        latest = None
        total_new = 0
        for round_number in range(1, self.max_rounds + 1):
            wf.check_cancel()
            key = f"{path_id}:round{round_number}"
            wf._ensure_tasks("rag_round_response", "path", [key])
            saved = wf._task_result("rag_round_response", key)
            if saved is None:
                bundle = self._bundle(path_id)
                with self.database.connect() as db:
                    used = int(db.execute("SELECT COUNT(*) FROM tasks WHERE stage='rag_investigation' AND status='complete'").fetchone()[0])
                allowed_new = max(0, int(self.config.get("max_research_items", 2000)) - used)
                bundle["research_budget"] = {"round": round_number, "max_rounds": self.max_rounds,
                    "web_search_allowed": allowed_new > 0, "remaining_new_investigations": allowed_new,
                    "max_tool_calls": int(self.config.get("max_tool_calls", 6))}
                # The final follow-up can still discover evidence, but it cannot
                # cite it as accepted until a later review actually sees it.
                prompt = rag_review_prompt(bundle)
                result, raw = wf.client.structured(
                    model=str(self.config.get("rag_model", "gpt-6-sol")), prompt=prompt,
                    schema_name="path_picker_rag_review", schema=rag_review_schema(bundle,
                        max_claims=int(self.config.get("rag_max_new_claims", 12)) if allowed_new else 0,
                        max_investigations=min(allowed_new, int(self.config.get("rag_max_investigations", 8)))),
                    reasoning_effort=str(self.config.get("rag_reasoning", "medium")),
                    web_search=allowed_new > 0, require_web_search=False,
                    search_context_size=str(self.config.get("web_search_context_size", "high")),
                )
                saved = {"result": result, "bundle": bundle, "searched_urls": sorted(wf.client.source_urls(raw)),
                         "web_search_performed": any(x.get("type") == "web_search_call" for x in raw.get("output", [])),
                         "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "saved_at": now()}
                # Save BEFORE validation/verification. A failed validator must not
                # destroy the paid response or force completed research to rerun.
                wf._set_task("rag_round_response", key, "complete", result=saved)
            try:
                self._validate_response(path_id, saved)
            except Exception as exc:
                wf._set_task("rag_round_response", key, "invalid", result=saved, error=str(exc))
                self._diagnostic(key, saved, str(exc))
                raise
            wf._ensure_tasks("rag_round_verification", "path", [key])
            checked = wf._task_result("rag_round_verification", key)
            if checked is None:
                checked = self._verify_new_claims(key, saved)
                wf._set_task("rag_round_verification", key, "complete", result=checked)
            result = saved["result"]
            assessment = copy.deepcopy(result["assessment"])
            scoped_claims = {c["claim_id"]: c for c in saved["bundle"]["accepted_claims"]}
            adjustments = []
            for section in ("steps", "node_roles"):
                for item in assessment[section]:
                    if item["evidence_status"] == "uncertain":
                        continue
                    polarity = "positive" if item["evidence_status"] == "supported" else "negative"
                    identifier = canonical_edge(item["upstream"], item["downstream"]) if section == "steps" else item["node"]
                    if not any(scoped_claims[i].get("status_support_eligible") and scoped_claims[i]["item_id"] == identifier
                               and scoped_claims[i]["polarity"] == polarity for i in item["claim_ids"]):
                        item["evidence_status"] = "uncertain"
                        item["scope_caveat"] = "Accepted context exists, but exact endpoint/direction/context applicability is not established by the conservative metadata gate."
                        adjustments.append({"item_id": identifier, "reason": item["scope_caveat"]})
            latest = {**assessment, "scope_adjustments": adjustments, "research_requests": [], "remaining_gaps": result["remaining_gaps"],
                      "rag_round": round_number, "rag_revision": saved["bundle"]["rag"].get("revision"),
                      "rag_new_claims": checked, "rag_budget_exhausted": False}
            total_new += len(checked["accepted_claim_ids"])
            needs_followup = bool(checked["accepted_claim_ids"] or result["needs_more_research"])
            if round_number == self.max_rounds:
                latest["rag_budget_exhausted"] = needs_followup
                if checked["accepted_claim_ids"]:
                    latest["remaining_gaps"] = [*latest["remaining_gaps"],
                        "New claims were verified after the last review; they are available for later paths and finalist audit, not incorporated into this assessment."]
                break
            if not needs_followup:
                break
        assert latest is not None
        with self.database.connect() as db:
            row = db.execute("SELECT primary_score,hop_count FROM paths WHERE path_id=?", (path_id,)).fetchone()
            score = float(latest["mechanistic_coherence"]) + .05 * math.log(max(float(row["primary_score"]), 1e-12)) - .005 * int(row["hop_count"])
            status = "directly_reviewed" if latest["assessment"] == "plausible" else "uncertain"
            db.execute("UPDATE path_assessments SET status=?,review_json=?,updated_at=? WHERE path_id=?",
                       (status, json.dumps(latest, ensure_ascii=False), now(), path_id))
            db.execute("UPDATE paths SET deterministic_status='eligible',deterministic_score=? WHERE path_id=?", (score, path_id))
        wf._set_task("path_screen", path_id, "complete", result=latest)
        self.database.event("rag_path", f"Reviewed {path_id}; {latest['rag_round']} round(s), {total_new} newly verified claims",
                            details={"path_id": path_id, "assessment": latest["assessment"], "remaining_gaps": latest["remaining_gaps"]})
        self._update_progress("reviewing_paths")

    @staticmethod
    def _status_support_eligible(claim: dict[str, Any], symbols: list[str]) -> bool:
        """A conservative metadata gate, not a replacement for scientific review.

        Aliases/phrase objects and other-context facts stay in the bundle, but do
        not automatically earn a supported/contradicted *step* label.
        """
        if claim.get("context_match", {}).get("status") != "exact_fields":
            return False
        subject, obj = (str(claim.get(k, "")).strip().casefold() for k in ("subject", "object"))
        if claim.get("item_type") == "node":
            return subject == str(claim["item_id"]).casefold()
        for a, b in zip(symbols, symbols[1:]):
            if claim.get("item_id") == canonical_edge(a, b) and (subject, obj) == (a.casefold(), b.casefold()):
                return any(x.get("requested") == [a, b] and x.get("forward_mentioned") and not x.get("opposite_mentioned")
                           for x in claim.get("direction_match", {}).get("requested_edges", []))
        return False

    def _validate_response(self, path_id: str, saved: dict[str, Any]) -> None:
        result, bundle = saved["result"], saved["bundle"]
        assessment = result["assessment"]
        coherence = assessment["mechanistic_coherence"]
        if isinstance(coherence, bool) or not isinstance(coherence, (int, float)) or not math.isfinite(coherence) or not 0 <= coherence <= 1:
            raise RuntimeError("RAG mechanistic coherence must be a finite value between zero and one")
        allowed = {c["claim_id"]: c for c in bundle["accepted_claims"]}
        referenced = {i for part in ("steps", "node_roles", "exclusion_proposals")
                      for entry in assessment.get(part, []) for i in entry.get("claim_ids", [])}
        if referenced - allowed.keys():
            raise RuntimeError(f"RAG screen {path_id} cited unaccepted or out-of-bundle IDs: {', '.join(sorted(referenced - allowed.keys()))}; allowed={len(allowed)}. Rejected response saved.")
        self._validate_screen(path_id, assessment, bundle)
        for section in ("steps", "node_roles"):
            for item in assessment[section]:
                expected_kind = "edge" if section == "steps" else "node"
                expected_id = canonical_edge(item["upstream"], item["downstream"]) if section == "steps" else item["node"]
                state = item.get("evidence_status")
                if state not in {"supported", "contradicted", "uncertain"}:
                    raise RuntimeError("RAG step/role lacks an evidence status")
                if state != "uncertain":
                    polarity = "positive" if state == "supported" else "negative"
                    if not any(allowed[i]["polarity"] == polarity and allowed[i].get("item_type") == expected_kind
                               and allowed[i].get("item_id") == expected_id for i in item.get("claim_ids", [])):
                        raise RuntimeError(f"RAG {state} step/role lacks accepted {polarity} evidence; response saved")
        proposals = result.get("new_claims", [])
        investigations = result.get("investigations", [])
        budget = bundle["research_budget"]
        if len(proposals) > int(self.config.get("rag_max_new_claims", 12)):
            raise RuntimeError("RAG proposed-claim budget exceeded")
        if len(investigations) > min(budget["remaining_new_investigations"], int(self.config.get("rag_max_investigations", 8))):
            raise RuntimeError("RAG investigation budget exceeded")
        if (proposals or investigations) and not saved["web_search_performed"]:
            raise RuntimeError("New RAG claims/investigations require an actual hosted web search")
        if (proposals or investigations) and not budget["web_search_allowed"]:
            raise RuntimeError("RAG research budget exhausted; no new investigations permitted")
        for entry in [*proposals, *investigations]:
            kind, identifier = entry.get("item_type"), entry.get("item_id")
            if identifier not in bundle["available_research_items"].get(kind, []):
                raise RuntimeError("RAG research referred to an item outside the reviewed path")
        covered_claims = set()
        for entry in investigations:
            direction = entry["direction"]
            if entry["item_type"] == "edge" and (len(direction) != 2 or canonical_edge(*direction) != entry["item_id"]):
                raise RuntimeError("RAG investigation direction does not match its edge")
            if entry["item_type"] == "node" and direction not in ([], [entry["item_id"]]):
                raise RuntimeError("RAG node investigation has unrelated direction")
            if not entry["question"].strip() or not entry["mechanism"].strip():
                raise RuntimeError("RAG investigations require an explicit question and mechanism")
            for index in entry["claim_indices"]:
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(proposals):
                    raise RuntimeError("RAG investigation has an invalid proposed-claim index")
                if (proposals[index]["item_type"], proposals[index]["item_id"]) != (entry["item_type"], entry["item_id"]):
                    raise RuntimeError("RAG investigation claimed evidence for a different item")
                covered_claims.add(index)
        if covered_claims != set(range(len(proposals))):
            raise RuntimeError("Each new RAG claim must belong to a scoped investigation")

    def _verify_new_claims(self, key: str, saved: dict[str, Any]) -> dict[str, Any]:
        from .workflow import _url_key
        accepted, rejected, records = [], [], []
        for entry in saved["result"]["new_claims"]:
            self.workflow.check_cancel()
            claim_id, failure = self.workflow._ground_claim(entry["item_type"], entry["item_id"], entry["claim"],
                                                           {_url_key(u) for u in saved["searched_urls"]})
            records.append({"claim_id": claim_id, "failure": failure})
            if failure:
                rejected.append({"claim_id": claim_id, "reason": failure})
            else:
                accepted.append(claim_id)
            self.knowledge.import_run(self.database.path)
        for entry in saved["result"]["investigations"]:
            payload = {**entry, "context": self.context, "policy_version": POLICY,
                       "run_id": self.workflow.run_directory.name, "review_key": key,
                       "accepted_claim_ids": [records[i]["claim_id"] for i in entry["claim_indices"] if not records[i]["failure"]],
                       "reusable": True, "status": "searched", "biological_negative": False}
            if not payload["accepted_claim_ids"]:
                payload["outcome"] = "inconclusive"
            coverage_key = stable_id("COV", POLICY, json.dumps(self.context, sort_keys=True), entry["item_type"],
                entry["item_id"], json.dumps(entry["direction"]), entry["mechanism"].strip().casefold(), entry["question"].strip().casefold())
            self.knowledge.record_coverage(coverage_key, payload)
            self.workflow._ensure_tasks("rag_investigation", entry["item_type"], [coverage_key])
            self.workflow._set_task("rag_investigation", coverage_key, "complete", result=payload)
        return {"accepted_claim_ids": accepted, "rejected_claims": rejected}

    def _diagnostic(self, key: str, saved: dict[str, Any], error: str) -> None:
        directory = self.workflow.run_directory / "rejected_responses"
        directory.mkdir(exist_ok=True)
        name = stable_id("DIAG", key, saved["saved_at"])
        (directory / f"{name}.json").write_text(json.dumps({"key": key, "error": error, **saved}, ensure_ascii=False, indent=2), encoding="utf-8")
        self.database.event("rag_validation", error, level="error", details={"checkpoint": key, "diagnostic": f"rejected_responses/{name}.json"})

    def _update_progress(self, stage: str) -> None:
        self.database.set_meta("rag_stage", stage)
        self.database.set_meta("adaptive_stage", stage)
        with self.database.connect() as db:
            calls = db.execute("SELECT COUNT(*) FROM tasks WHERE stage='rag_round_response' AND status='complete'").fetchone()[0]
            investigated = db.execute("SELECT COUNT(*) FROM tasks WHERE stage='rag_investigation' AND status='complete'").fetchone()[0]
        self.database.set_meta("rag_progress", {**adaptive_progress(self.database), "completed_review_rounds": calls,
                                                "completed_scoped_investigations": investigated})
        self.database.set_meta("rag_knowledge_summary", self.knowledge.summary())

    def export_audit(self) -> None:
        export_adaptive_audit(self.database)
        with self.database.connect() as db:
            rows = [dict(r) for r in db.execute("SELECT path_id,status,review_json,updated_at FROM path_assessments WHERE review_json IS NOT NULL ORDER BY path_id")]
            rounds = [dict(r) for r in db.execute("SELECT item_id,status,error,updated_at FROM tasks WHERE stage='rag_round_response' ORDER BY item_id")]
        audit = {"schema_version": 1, "policy": POLICY, "exported_at": now(), "knowledge": self.knowledge.summary(),
                 "seed_import": self.database.get_meta("rag_seed_summary", {}, decode=True),
                 "progress": self.database.get_meta("rag_progress", {}, decode=True), "rounds": rounds,
                 "limits": {"max_rounds_per_path": self.max_rounds, "max_new_claims_per_round": self.config.get("rag_max_new_claims", 12)},
                 "caveats": ["Model-verified evidence is not proof of biological truth.",
                     "Each review records its knowledge revision; early paths are not silently reranked after later discoveries.",
                     "Finalist audit retrieves current evidence; unresolved gaps are never negative evidence.",
                     "RAG lookups are local; hosted searches, reasoning, verification and finalist audits may incur charges."]}
        (self.workflow.run_directory / "rag_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        with (self.workflow.run_directory / "rag_reviews.jsonl").open("w", encoding="utf-8") as handle:
            for row in rows:
                row["assessment"] = json.loads(row.pop("review_json"))
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
