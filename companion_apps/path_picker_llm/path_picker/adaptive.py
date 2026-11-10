"""Path-first triage with complete coverage and conservative, reversible rules.

Screening is not verification. A low score, missing source, endpoint label or
positive observation in the reverse direction can never create an exclusion.
Original BGI payloads/scores are immutable; this module stores a separate ledger.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

from .database import RunDatabase, now
from .grounding import compact_evidence, stable_id
from .ingest import canonical_edge
from .prompts import adaptive_screen_prompt, edge_research_prompt, exclusion_verifier_prompt, node_research_prompt
from .schemas import ADAPTIVE_SCREEN_SCHEMA, EXCLUSION_VERIFY_SCHEMA


SCHEMA = """
CREATE TABLE IF NOT EXISTS path_assessments (
 path_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending',
 review_json TEXT, rule_id TEXT, updated_at TEXT NOT NULL,
 FOREIGN KEY(path_id) REFERENCES paths(path_id)
);
CREATE TABLE IF NOT EXISTS exclusion_rules (
 rule_id TEXT PRIMARY KEY, scope TEXT NOT NULL, target_json TEXT NOT NULL,
 context_json TEXT NOT NULL, claim_ids_json TEXT NOT NULL, rationale TEXT NOT NULL,
 status TEXT NOT NULL, impact_count INTEGER NOT NULL DEFAULT 0,
 validation_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rule_paths (
 rule_id TEXT NOT NULL, path_id TEXT NOT NULL, PRIMARY KEY(rule_id,path_id),
 FOREIGN KEY(rule_id) REFERENCES exclusion_rules(rule_id),
 FOREIGN KEY(path_id) REFERENCES paths(path_id)
);
"""


def ensure_adaptive_schema(database: RunDatabase) -> None:
    with database.connect() as db:
        db.executescript(SCHEMA)


def adaptive_progress(database: RunDatabase) -> dict[str, int]:
    ensure_adaptive_schema(database)
    with database.connect() as db:
        total = int(db.execute("SELECT COUNT(*) FROM paths").fetchone()[0])
        counts = dict(db.execute("SELECT status,COUNT(*) FROM path_assessments GROUP BY status"))
        reviewed = int(db.execute("SELECT COUNT(*) FROM path_assessments WHERE review_json IS NOT NULL").fetchone()[0])
        rules = dict(db.execute("SELECT status,COUNT(*) FROM exclusion_rules GROUP BY status"))
        researched = int(db.execute("SELECT COUNT(*) FROM tasks WHERE stage IN ('node_research','edge_research') AND status='complete'").fetchone()[0])
    excluded = int(counts.get("excluded_by_rule", 0))
    accounted = sum(counts.values())
    return {"total_paths": total, "directly_reviewed": reviewed,
            "excluded_by_rule": excluded, "pending": int(counts.get("pending", 0)) + max(0, total - accounted),
            "uncertain": int(counts.get("uncertain", 0)), "active_rules": int(rules.get("active", 0)),
            "held_rules": int(rules.get("held_for_review", 0)), "research_items_completed": researched}


def list_exclusions(database: RunDatabase) -> list[dict[str, Any]]:
    ensure_adaptive_schema(database)
    with database.connect() as db:
        rows = db.execute("SELECT * FROM exclusion_rules ORDER BY created_at,rule_id").fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for name in ("target", "context", "claim_ids", "validation"):
            item[name] = json.loads(item.pop(name + "_json"))
        result.append(item)
    return result


def export_adaptive_audit(workflow_or_database: Any) -> dict[str, str]:
    database = workflow_or_database if isinstance(workflow_or_database, RunDatabase) else workflow_or_database.database
    ensure_adaptive_schema(database)
    directory = database.path.parent
    with database.connect() as db:
        rows = db.execute("""SELECT p.path_id,p.session_id,p.query_id,p.path_symbols,p.primary_score,
            p.deterministic_score,COALESCE(a.status,'pending') AS status,a.review_json
            FROM paths p LEFT JOIN path_assessments a USING(path_id) ORDER BY p.query_id,p.rank,p.path_id""").fetchall()
        links = defaultdict(list)
        for row in db.execute("""SELECT rp.path_id,r.rule_id FROM rule_paths rp JOIN exclusion_rules r USING(rule_id)
                                 WHERE r.status='active'"""):
            links[row["path_id"]].append(row["rule_id"])
    paths = []
    for row in rows:
        item = dict(row)
        review = json.loads(item.pop("review_json") or "null")
        item.update(directly_reviewed=review is not None, review=review, active_exclusion_rule_ids=links[item["path_id"]])
        paths.append(item)
    coverage = directory / "path_coverage.json"
    ledger = directory / "exclusion_ledger.json"
    coverage.write_text(json.dumps({"schema_version": 1, "generated_at": now(), "counts": adaptive_progress(database),
        "status_semantics": {"pending": "Not yet screened", "directly_reviewed": "Screened plausible, not independently established",
            "uncertain": "Screened but unresolved or concerned; NOT excluded", "excluded_by_rule": "Excluded by an active, reversible, verified applicability rule"},
        "ranking_note": "deterministic_score is a triage heuristic, not a posterior or calibrated probability; primary_score remains the imported BGI score",
        "paths": paths}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ledger.write_text(json.dumps({"schema_version": 1, "generated_at": now(), "rules": list_exclusions(database)}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"path_coverage": str(coverage), "exclusion_ledger": str(ledger)}


def _review_status(review: dict[str, Any] | None) -> str:
    return "pending" if review is None else ("directly_reviewed" if review.get("assessment") == "plausible" else "uncertain")


def revoke_exclusion(database: RunDatabase, rule_id: str) -> dict[str, int]:
    """Called only while idle. Preserve all reviews and all other active rules."""
    ensure_adaptive_schema(database)
    if database.get_meta("status") == "running":
        raise ValueError("Pause/cancel the worker before revoking an exclusion")
    with database.connect() as db:
        row = db.execute("SELECT status FROM exclusion_rules WHERE rule_id=?", (rule_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown exclusion rule")
        db.execute("UPDATE exclusion_rules SET status='revoked' WHERE rule_id=?", (rule_id,))
        for path in db.execute("SELECT path_id FROM rule_paths WHERE rule_id=?", (rule_id,)).fetchall():
            other = db.execute("""SELECT r.rule_id FROM rule_paths rp JOIN exclusion_rules r USING(rule_id)
                WHERE rp.path_id=? AND r.status='active' LIMIT 1""", (path[0],)).fetchone()
            assessment = db.execute("SELECT review_json FROM path_assessments WHERE path_id=?", (path[0],)).fetchone()
            review = json.loads(assessment[0]) if assessment and assessment[0] else None
            status = "excluded_by_rule" if other else _review_status(review)
            db.execute("UPDATE path_assessments SET status=?,rule_id=?,updated_at=? WHERE path_id=?",
                       (status, other[0] if other else None, now(), path[0]))
            db.execute("UPDATE paths SET deterministic_status=? WHERE path_id=?",
                       ("excluded_verified_rule" if other else ("eligible" if review else "pending"), path[0]))
            if not other and not review:
                db.execute("UPDATE tasks SET status='pending' WHERE stage='path_screen' AND item_id=?", (path[0],))
    database.event("rule_revoked", "Revoked scoped exclusion " + rule_id)
    # Old synthesis must not silently remain authoritative after eligibility changes.
    database.set_meta("shortlist_stale", "true")
    export_adaptive_audit(database)
    return adaptive_progress(database)


deactivate_exclusion = revoke_exclusion


def target_fair_order(rows: Iterable[Any]) -> list[str]:
    """Round robin across source→target queries, preserving each supplied ranking."""
    groups: dict[str, deque[str]] = {}
    for row in rows:
        groups.setdefault(str(row["query_id"]), deque()).append(str(row["path_id"]))
    result = []
    while groups:
        for query in list(groups):
            result.append(groups[query].popleft())
            if not groups[query]:
                del groups[query]
    return result


def _symbols(path: Any) -> list[str]:
    return [value.strip() for value in str(path["path_symbols"]).split("->") if value.strip()]


def rule_matches_path(rule: dict[str, Any], path: Any, review: dict[str, Any] | None) -> bool:
    """No mechanism wildcard: unresolved/unreviewed paths never inherit broad bans."""
    target = rule["target"]
    if str(path["query_id"]) != str(target["query_id"]):
        return False
    symbols, nodes = _symbols(path), target["nodes"]
    scope = rule["scope"]
    if scope == "path":
        return str(path["path_id"]) == target["origin_path_id"]
    if not review or not target.get("mechanism") or target["mechanism"].casefold() in {"unresolved", "unknown", "association"}:
        return False
    mechanism = target["mechanism"].strip().casefold()
    if scope == "intermediate":
        return len(nodes) == 1 and nodes[0] in symbols[1:-1] and any(
            item.get("node") == nodes[0] and str(item.get("mechanism", "")).strip().casefold() == mechanism
            for item in review.get("node_roles", []))
    if scope not in {"directed_edge", "motif"} or not any(symbols[i:i + len(nodes)] == nodes for i in range(len(symbols) - len(nodes) + 1)):
        return False
    steps = {(item.get("upstream"), item.get("downstream")): str(item.get("mechanism", "")).strip().casefold() for item in review.get("steps", [])}
    return all(steps.get((a, b)) == mechanism for a, b in zip(nodes, nodes[1:]))


class AdaptivePathRunner:
    def __init__(self, workflow: Any) -> None:
        self.workflow = workflow
        self.database = workflow.database
        self.config = workflow.config
        ensure_adaptive_schema(self.database)
        self.context = self.config.get("biological_context") or {
            "organism": "mouse", "cell_type": "kidney collecting duct", "stimulus": "acute vasopressin/dDAVP–PKA signaling"}

    def initialize(self) -> None:
        fingerprint = stable_id("CTX", json.dumps(self.context, sort_keys=True), "adaptive-v1")
        previous = self.database.get_meta("adaptive_context_fingerprint")
        if previous and previous != fingerprint:
            raise ValueError("Biological context changed; start a separate run so cached claims/exclusions cannot leak across contexts")
        self.database.set_meta("adaptive_context_fingerprint", fingerprint)
        self.database.set_meta("adaptive_biological_context", self.context)
        with self.database.connect() as db:
            ids = [str(row[0]) for row in db.execute("SELECT path_id FROM paths")]
            db.executemany("INSERT OR IGNORE INTO path_assessments(path_id,status,updated_at) VALUES (?,'pending',?)", [(identifier, now()) for identifier in ids])
        self.workflow._ensure_tasks("path_screen", "path", ids)

    def run(self) -> None:
        self.initialize()
        self.database.set_meta("adaptive_stage", "screening_all_paths")
        self._process_proposals()
        round_size = max(1, int(self.config.get("path_round_size", 10)))
        while True:
            self.workflow.check_cancel()
            with self.database.connect() as db:
                rows = db.execute("""SELECT p.* FROM paths p JOIN path_assessments a USING(path_id)
                    WHERE a.status='pending' ORDER BY p.rank,p.query_id,p.path_id""").fetchall()
            pending = target_fair_order(rows)[:round_size]
            if not pending:
                break
            for path_id in pending:
                self._screen(path_id)
            # No path can recursively trigger an unbounded exclusion/research loop.
            # All rule activations and affected statuses are committed at boundaries.
            self._process_proposals()
            export_adaptive_audit(self.database)
            self.database.event("path_screen", "Completed a target-fair screening round", details=adaptive_progress(self.database))
        self.database.set_meta("adaptive_stage", "researching_finalists")
        finalists = self.workflow._candidate_path_ids()
        self.database.set_meta("adaptive_finalist_ids", finalists)
        required: dict[tuple[str, str], str] = {}
        for path_id in finalists:
            with self.database.connect() as db:
                row = db.execute("SELECT path_symbols FROM paths WHERE path_id=?", (path_id,)).fetchone()
            symbols = _symbols(row)
            for symbol in symbols:
                required[("node", symbol)] = "Finalist completeness: assess endpoint/intermediate roles, supporting and conflicting primary experiments."
            for a, b in zip(symbols, symbols[1:]):
                required[("edge", canonical_edge(a, b))] = "Finalist completeness: research BOTH directions, all plausible acute mechanisms, supporting and contradicting experiments."
        self.research_items(required)
        self.database.set_meta("adaptive_stage", "deep_reviewing_finalists")
        self.workflow._deep_review_paths()
        self.database.set_meta("adaptive_stage", "synthesizing")
        export_adaptive_audit(self.database)

    def _bundle(self, path_id: str) -> dict[str, Any]:
        bundle = self.workflow._path_bundle(path_id)
        with self.database.connect() as db:
            row = db.execute("SELECT path_symbols,query_id FROM paths WHERE path_id=?", (path_id,)).fetchone()
        symbols = _symbols(row)
        available = {"node": symbols, "edge": [canonical_edge(a, b) for a, b in zip(symbols, symbols[1:])]}
        completed = []
        for kind, ids in available.items():
            for identifier in ids:
                result = self.workflow._task_result(kind + "_research", identifier)
                if result:
                    completed.append({"kind": kind, "item_id": identifier, "verdict": result.get("verdict"), "mechanism": result.get("mechanism"),
                        "reasoning": result.get("reasoning"), "accepted_claim_ids": result.get("accepted_claim_ids", []),
                        "unresolved_questions": result.get("unresolved_questions", [])})
        bundle.update(path_symbols=symbols, query_id=row["query_id"], biological_context=self.context,
                      available_research_items=available, completed_research=completed)
        return bundle

    def _screen(self, path_id: str) -> None:
        wf = self.workflow
        wf.check_cancel()
        # Initial response is checkpointed before research; a retry never reruns a
        # paid successful screen merely because a later source lookup failed.
        result = wf._task_result("path_screen_initial", path_id)
        wf._ensure_tasks("path_screen_initial", "path", [path_id])
        if result is None:
            initial_bundle = self._bundle(path_id)
            result = self._screen_call(initial_bundle)
            self._validate_screen(path_id, result, initial_bundle)
            completed = {(item["kind"], item["item_id"]) for item in initial_bundle["completed_research"]}
            result["_needs_refinement"] = any((item.get("kind"), item.get("item_id")) not in completed for item in result.get("research_requests", []))
            wf._set_task("path_screen_initial", path_id, "complete", result=result)
        bundle = self._bundle(path_id)
        requested: dict[tuple[str, str], str] = {}
        for item in result.get("research_requests", []):
            kind, identifier = item.get("kind"), str(item.get("item_id", ""))
            if identifier not in bundle["available_research_items"].get(kind, []):
                raise RuntimeError("Screen requested an item outside its path")
            requested[(kind, identifier)] = str(item.get("question", ""))
        # Even on resume, the original requests justify one post-research screen;
        # completed cached research costs no additional source call.
        self.research_items(requested)
        if requested and result.get("_needs_refinement", True):
            refined = wf._task_result("path_screen_refined", path_id)
            wf._ensure_tasks("path_screen_refined", "path", [path_id])
            if refined is None:
                refined_bundle = self._bundle(path_id)
                refined = self._screen_call(refined_bundle)
                self._validate_screen(path_id, refined, refined_bundle)
                wf._set_task("path_screen_refined", path_id, "complete", result=refined)
            result = refined
        self._validate_screen(path_id, result)
        with self.database.connect() as db:
            row = db.execute("SELECT primary_score,hop_count FROM paths WHERE path_id=?", (path_id,)).fetchone()
            # Explicitly a heuristic for triage, NOT a recalibrated posterior.
            score = float(result.get("mechanistic_coherence", 0)) + .05 * math.log(max(float(row["primary_score"]), 1e-12)) - .005 * int(row["hop_count"])
            db.execute("UPDATE path_assessments SET status=?,review_json=?,updated_at=? WHERE path_id=?",
                       (_review_status(result), json.dumps(result, ensure_ascii=False), now(), path_id))
            db.execute("UPDATE paths SET deterministic_status='eligible',deterministic_score=? WHERE path_id=?", (score, path_id))
        wf._set_task("path_screen", path_id, "complete", result=result)

    def _screen_call(self, bundle: dict[str, Any]) -> dict[str, Any]:
        self.workflow.check_cancel()
        result, _ = self.workflow.client.structured(
            model=str(self.config.get("screening_model", self.config["research_model"])),
            prompt=adaptive_screen_prompt(bundle), schema_name="path_picker_adaptive_screen", schema=ADAPTIVE_SCREEN_SCHEMA,
            reasoning_effort=str(self.config.get("screening_reasoning", "medium")))
        return result

    def _validate_screen(self, path_id: str, result: dict[str, Any], bundle: dict[str, Any] | None = None) -> None:
        bundle = bundle or self._bundle(path_id)
        allowed = {item["claim_id"] for item in bundle["accepted_claims"]}
        symbols = bundle["path_symbols"]
        expected = list(zip(symbols, symbols[1:]))
        steps = [(item.get("upstream"), item.get("downstream")) for item in result.get("steps", [])]
        if steps != expected:
            raise RuntimeError("Screen did not account for every directed traversal in order")
        if sorted(item.get("node", "") for item in result.get("node_roles", [])) != sorted(symbols[1:-1]):
            raise RuntimeError("Screen did not account for every intermediate")
        referenced = {claim for step in result.get("steps", []) for claim in step.get("claim_ids", [])}
        referenced.update(claim for proposal in result.get("exclusion_proposals", []) for claim in proposal.get("claim_ids", []))
        if referenced - allowed:
            raise RuntimeError("Screen referenced claims absent from the accepted ledger")
        for item in result.get("research_requests", []):
            if item.get("item_id") not in bundle["available_research_items"].get(item.get("kind"), []):
                raise RuntimeError("Screen requested an item outside its path")

    def research_items(self, requested: dict[tuple[str, str], str]) -> None:
        wf = self.workflow
        missing = [(kind, identifier, question) for (kind, identifier), question in requested.items()
                   if wf._task_result(kind + "_research", identifier) is None]
        limit = int(self.config.get("max_research_items", 2000))
        completed = adaptive_progress(self.database)["research_items_completed"]
        if completed + len(missing) > limit:
            raise RuntimeError(f"Research safety cap ({limit} unique items) would be exceeded. Checkpoints are saved; increase the cap deliberately to resume.")
        for kind in ("node", "edge"):
            items = [(identifier, question) for this_kind, identifier, question in missing if this_kind == kind]
            size = max(1, int(self.config.get(kind + "_batch_size", 2 if kind == "node" else 1)))
            for offset in range(0, len(items), size):
                wf.check_cancel()
                batch = items[offset:offset + size]
                ids = [identifier for identifier, _ in batch]
                wf._ensure_tasks(kind + "_research", kind, ids)
                payloads = wf._payloads("nodes" if kind == "node" else "edges", kind + "_id", ids)
                if set(ids) - payloads.keys():
                    raise RuntimeError("Research item missing from imported graph")
                packed = [{"item_id": identifier, "bgi": compact_evidence(payloads[identifier]), "biological_context": self.context,
                           "question": question, "scope": "Investigate both support and contradiction; for edges cover BOTH directions and alternative acute mechanisms."}
                          for identifier, question in batch]
                prompt = node_research_prompt(packed) if kind == "node" else edge_research_prompt(packed)
                wf._research_batch(kind, packed, prompt, ids)
                if any(wf._task_result(kind + "_research", identifier) is None for identifier in ids):
                    raise RuntimeError("Research response omitted requested items; incomplete tasks remain retryable")

    def _process_proposals(self) -> None:
        with self.database.connect() as db:
            rows = db.execute("SELECT p.*,a.review_json FROM paths p JOIN path_assessments a USING(path_id) WHERE a.review_json IS NOT NULL").fetchall()
        for row in rows:
            review = json.loads(row["review_json"])
            for proposal in review.get("exclusion_proposals", []):
                self._consider_rule(row, proposal)
        self._apply_active_rules()

    def _consider_rule(self, path: Any, proposal: dict[str, Any]) -> None:
        scope, nodes = proposal.get("scope"), proposal.get("nodes", [])
        symbols = _symbols(path)
        if scope == "path":
            nodes = symbols
        elif scope == "intermediate":
            if len(nodes) != 1 or nodes[0] not in symbols[1:-1]:
                return
        elif scope in {"directed_edge", "motif"}:
            if len(nodes) < 2 or (scope == "directed_edge" and len(nodes) != 2) or not any(symbols[i:i+len(nodes)] == nodes for i in range(len(symbols)-len(nodes)+1)):
                return
        else:
            return
        target = {"nodes": nodes, "mechanism": str(proposal.get("mechanism", "")), "origin_path_id": path["path_id"], "query_id": path["query_id"]}
        ids = sorted(set(proposal.get("claim_ids", [])))
        rule_id = stable_id("RULE", scope, json.dumps(target, sort_keys=True), *ids)
        with self.database.connect() as db:
            if db.execute("SELECT 1 FROM exclusion_rules WHERE rule_id=?", (rule_id,)).fetchone():
                return
        claims = self.workflow._accepted_claims(set(ids))
        negative = [claim for claim in claims if claim.get("polarity") == "negative"]
        status, verification = "rejected", {"explanation": "No accepted explicit negative experimental claim; unknown evidence cannot exclude"}
        if ids and len(claims) == len(ids) and negative:
            self.workflow.check_cancel()
            verification, _ = self.workflow.client.structured(
                model=str(self.config["verification_model"]),
                prompt=exclusion_verifier_prompt({**proposal, "target": target}, self._bundle(path["path_id"]), negative),
                schema_name="path_picker_exclusion_verification", schema=EXCLUSION_VERIFY_SCHEMA,
                reasoning_effort=str(self.config.get("verification_reasoning", "high")))
            flags = ("explicit_negative", "context_matches", "direction_matches", "mechanism_matches", "alternative_mechanisms_considered", "exclusion_justified")
            if all(verification.get(flag) is True for flag in flags):
                status = "active"
        rule = {"scope": scope, "target": target}
        with self.database.connect() as db:
            paths = db.execute("SELECT p.*,a.review_json FROM paths p LEFT JOIN path_assessments a USING(path_id)").fetchall()
            affected = [row["path_id"] for row in paths if rule_matches_path(rule, row, json.loads(row["review_json"]) if row["review_json"] else None)]
            # Risk is potential topological reach, not just currently reviewed matches.
            potential = sum(self._potential_match(rule, row) for row in paths)
            max_paths = max(0, int(self.config.get("exclusion_max_paths", 50)))
            max_fraction = max(0.0, min(1.0, float(self.config.get("exclusion_max_fraction", .1))))
            if status == "active" and (potential > max_paths or potential > len(paths) * max_fraction):
                status = "held_for_review"
            db.execute("""INSERT INTO exclusion_rules(rule_id,scope,target_json,context_json,claim_ids_json,rationale,status,impact_count,validation_json,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""", (rule_id, scope, json.dumps(target), json.dumps(self.context), json.dumps(ids),
                str(proposal.get("rationale", "")), status, potential, json.dumps({**verification, "mechanism_matched_path_count": len(affected)}), now()))
        self.database.event("exclusion_rule", f"{status}: {scope} rule {rule_id}", details={"potential_impact": potential, "mechanism_matches": len(affected)})

    @staticmethod
    def _potential_match(rule: dict[str, Any], path: Any) -> bool:
        target = rule["target"]
        if str(path["query_id"]) != str(target["query_id"]):
            return False
        symbols, nodes = _symbols(path), target["nodes"]
        if rule["scope"] == "path":
            return path["path_id"] == target["origin_path_id"]
        if rule["scope"] == "intermediate":
            return nodes[0] in symbols[1:-1]
        return any(symbols[i:i+len(nodes)] == nodes for i in range(len(symbols)-len(nodes)+1))

    def _apply_active_rules(self) -> None:
        rules = [rule for rule in list_exclusions(self.database) if rule["status"] == "active"]
        with self.database.connect() as db:
            paths = db.execute("SELECT p.*,a.review_json FROM paths p JOIN path_assessments a USING(path_id)").fetchall()
            for path in paths:
                review = json.loads(path["review_json"]) if path["review_json"] else None
                matched = [rule for rule in rules if rule_matches_path(rule, path, review)]
                if not matched:
                    continue
                for rule in matched:
                    db.execute("INSERT OR IGNORE INTO rule_paths(rule_id,path_id) VALUES (?,?)", (rule["rule_id"], path["path_id"]))
                db.execute("UPDATE path_assessments SET status='excluded_by_rule',rule_id=?,updated_at=? WHERE path_id=?", (matched[0]["rule_id"], now(), path["path_id"]))
                db.execute("UPDATE paths SET deterministic_status='excluded_verified_rule' WHERE path_id=?", (path["path_id"],))
                if review is None:
                    db.execute("UPDATE tasks SET status='excluded_by_rule' WHERE stage='path_screen' AND item_id=?", (path["path_id"],))
