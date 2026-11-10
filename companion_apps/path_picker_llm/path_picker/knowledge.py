"""Shared, provenance-preserving local evidence retrieval.

Facts are accepted, quoted claims, never researcher summaries. Retrieval uses
TF-IDF plus local truncated-SVD/LSA (not neural embeddings), after exact item
gating. The optional NumPy dependency has an explicit lexical-only fallback.
Importing a run always uses a read-only SQLite connection.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlsplit

try:
    import numpy as np
except ImportError:  # Keep offline lookup usable, and report the limitation.
    np = None

from .database import RunDatabase, now
from .grounding import normalize_text, quote_is_present


SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO knowledge_meta VALUES ('revision','0');
CREATE TABLE IF NOT EXISTS knowledge_claims(
 claim_id TEXT PRIMARY KEY,item_type TEXT NOT NULL,item_id TEXT NOT NULL,
 claim_json TEXT NOT NULL,source_json TEXT NOT NULL,content_hash TEXT NOT NULL,
 quarantined INTEGER NOT NULL DEFAULT 0,imported_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS knowledge_items ON knowledge_claims(item_type,item_id,quarantined);
CREATE TABLE IF NOT EXISTS knowledge_origins(
 claim_id TEXT NOT NULL,origin_run TEXT NOT NULL,PRIMARY KEY(claim_id,origin_run));
CREATE TABLE IF NOT EXISTS knowledge_rejections(
 audit_id TEXT PRIMARY KEY,claim_id TEXT NOT NULL,origin_run TEXT NOT NULL,
 claim_json TEXT NOT NULL,source_json TEXT NOT NULL,reasons_json TEXT NOT NULL,recorded_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_coverage(
 coverage_key TEXT PRIMARY KEY,payload_json TEXT NOT NULL,updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS knowledge_source_revocations(
 revocation_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,url TEXT NOT NULL,
 source_json TEXT NOT NULL,reasons_json TEXT NOT NULL,origin_run TEXT NOT NULL,recorded_at TEXT NOT NULL);
"""

CLAIM_FIELDS = ("claim_id", "item_type", "item_id", "subject", "predicate", "object",
                "context_json", "source_id", "quotation", "quote_verified", "entailment_verified",
                "context_verified", "accepted", "polarity", "failure_reason")
SOURCE_FIELDS = ("source_id", "url", "title", "source_type", "pmid", "doi", "retracted",
                 "retrieved", "http_status", "text_hash", "retrieved_at", "metadata_json")
SECRET_FIELD = re.compile(r"api.?key|authorization|password|credential|secret|access.?token", re.I)
TOKEN = re.compile(r"[^\W_]+(?:[-_][^\W_]+)*", re.UNICODE)
STOP = frozenset("a an and are as at be been by does for from has have in into is it of on or that the their this to was were with not".split())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Any, default: Any) -> Any:
    if not isinstance(value, str):
        return value if isinstance(value, type(default)) else default
    try:
        result = json.loads(value)
        return result if isinstance(result, type(default)) else default
    except (ValueError, TypeError):
        return default


def _clean(value: Any) -> Any:
    """Never copy configuration or secret-bearing object fields into the library."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items() if not SECRET_FIELD.search(str(k))}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", value)
    return value


def _digest(*values: Any) -> str:
    return hashlib.sha256("\0".join(_json(v) for v in values).encode("utf-8")).hexdigest()


@contextmanager
def _readonly(path: str | Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    try:
        yield connection
    finally:
        connection.close()


def _pair(value: str) -> tuple[str, str] | None:
    parts = [s.strip().casefold() for s in value.split("--")]
    return tuple(sorted(parts)) if len(parts) == 2 and all(parts) else None


def coverage_key(*, context: dict[str, Any], item_type: str = "", item_id: str = "",
                 ordered_edge: list[str] | tuple[str, ...] = (), mechanism: str = "",
                 question: str = "", policy_version: str = "rag-v1") -> str:
    """Exact research scope. In particular, swapping an edge changes this key."""
    return "COV-" + _digest(_clean(context), item_type, item_id, list(ordered_edge),
                            mechanism.strip(), question.strip(), policy_version)


def _source(row: dict[str, Any]) -> dict[str, Any]:
    source = {key: row.get(key) for key in SOURCE_FIELDS}
    metadata = _decode(source.get("metadata_json"), {})
    excerpts = metadata.get("verified_excerpts", [])
    excerpts = excerpts if isinstance(excerpts, list) else []
    source["metadata_json"] = _json({"verified_excerpts": list(dict.fromkeys(
        text for text in excerpts if isinstance(text, str) and text.strip()))})
    # Credentialed URLs cannot become facts or leak credentials into audit rows.
    url = str(source.get("url") or "")
    try:
        parsed = urlsplit(url)
        if parsed.username or parsed.password or any(SECRET_FIELD.search(k) or k.casefold() in {"key", "token", "sig"}
                                                    for k, _ in parse_qsl(parsed.query)):
            source["url"] = "[REDACTED credential-bearing URL]"
    except ValueError:
        source["url"] = "[invalid URL]"
    return _clean(source)


def _source_errors(source: dict[str, Any]) -> list[str]:
    errors = []
    if source.get("retracted") != 0:
        errors.append("source is retracted or retraction status is missing")
    if source.get("retrieved") != 1:
        errors.append("source was not retrieved")
    status = source.get("http_status")
    if not isinstance(status, int) or not 200 <= status < 300:
        errors.append("successful source HTTP status is missing")
    url = str(source.get("url") or "")
    try:
        valid_url = urlsplit(url).scheme in {"http", "https"} and bool(urlsplit(url).hostname)
    except ValueError:
        valid_url = False
    if not source.get("source_id") or not valid_url:
        errors.append("source provenance is missing or unsafe")
    return errors


def _acceptance_errors(claim: dict[str, Any], source: dict[str, Any]) -> list[str]:
    errors = _source_errors(source)
    for field in ("accepted", "quote_verified", "entailment_verified", "context_verified"):
        if claim.get(field) != 1:
            errors.append(field + " is not true")
    if claim.get("failure_reason"):
        errors.append("claim has a recorded failure: " + str(claim["failure_reason"]))
    if source.get("source_id") != claim.get("source_id"):
        errors.append("claim/source identity mismatch")
    quotation = str(claim.get("quotation") or "")
    if len(quotation.strip()) < 20 or len(quotation.split()) < 5:
        errors.append("usable verified quotation is missing")
    context = _decode(claim.get("context_json"), {})
    if not context or any(not str(context.get(field) or "").strip()
                          for field in ("organism", "cell_type", "stimulus", "assay", "directionality")):
        errors.append("declared biological context is incomplete")
    if claim.get("item_type") not in {"node", "edge"} or not claim.get("item_id"):
        errors.append("exact item identity is missing")
    if any(not str(claim.get(field) or "").strip() for field in ("claim_id", "subject", "predicate", "object")):
        errors.append("atomic claim identity is incomplete")
    if claim.get("polarity") not in {"positive", "negative"}:
        errors.append("claim polarity is missing")
    return errors


def _context_annotation(actual: dict[str, Any], wanted: dict[str, Any]) -> dict[str, Any]:
    actual = actual if isinstance(actual, dict) else {}
    wanted = wanted if isinstance(wanted, dict) else {}
    matched, mismatched, unknown = [], [], []
    organism_groups = ({"mouse", "murine", "mus musculus"}, {"human", "homo sapiens"}, {"rat", "rattus norvegicus"})
    for field, expected in wanted.items():
        if not expected or field in {"assay", "directionality"}:
            continue
        left, right = normalize_text(str(actual.get(field, ""))), normalize_text(str(expected))
        if left and left == right:
            matched.append(field)
        elif field == "organism" and any(left in group and right in group for group in organism_groups):
            matched.append(field)
        elif field == "organism" and any(left in group for group in organism_groups) and any(right in group for group in organism_groups):
            mismatched.append(field)
        else:
            # Different wording may be a synonym, a broader context, or a mismatch.
            # Never upgrade this string comparison to a biological applicability claim.
            unknown.append(field)
    status = "mismatch" if mismatched else ("not_established" if unknown else ("exact_fields" if matched else "unspecified"))
    return {"status": status, "matched_fields": matched, "mismatched_fields": mismatched,
            "unestablished_fields": unknown, "note": "Metadata comparison only; not proof in the requested biological context."}


def _direction_annotation(claim: dict[str, Any], symbols: list[str]) -> dict[str, Any]:
    context = _decode(claim.get("context_json"), {})
    reported = str(context.get("directionality", ""))
    ordered = [(a, b) for a, b in zip(symbols, symbols[1:]) if _pair(claim["item_id"]) == tuple(sorted((a.casefold(), b.casefold())))] if claim["item_type"] == "edge" else []
    normalized = reported.casefold().replace("→", "->").replace("⇒", "->")
    orientations = []
    for a, b in ordered:
        def appears(left: str, right: str) -> bool:
            return bool(re.search(r"(?<!\w)" + re.escape(left.casefold()) + r"\s*->\s*" + re.escape(right.casefold()) + r"(?!\w)", normalized))
        forward, reverse = appears(a, b), appears(b, a)
        orientations.append({"requested": [a, b], "forward_mentioned": forward, "opposite_mentioned": reverse})
    return {"reported": reported, "requested_edges": orientations,
            "status": "opposite_direction_mentioned" if any(x["opposite_mentioned"] for x in orientations) else "requires_review",
            "note": "Mentions may be negated or qualified; an undirected item key establishes no signaling direction."}


def _tokens(text: str) -> list[str]:
    return [word for word in TOKEN.findall(normalize_text(text)) if word not in STOP and len(word) > 1]


def _rank(documents: list[str], query: str) -> tuple[list[float], dict[str, Any]]:
    """A bounded, deterministic TF-IDF/LSA index built over the gated corpus."""
    counts = [Counter(_tokens(text)) for text in documents]
    q = Counter(_tokens(query))
    frequency: Counter[str] = Counter()
    for terms in counts:
        frequency.update(terms.keys())
    vocabulary = sorted(frequency, key=lambda word: (-frequency[word], word))[:4096]
    idf = {word: math.log((1 + len(counts)) / (1 + frequency[word])) + 1 for word in vocabulary}
    vectors = [{word: (1 + math.log(count)) * idf[word] for word, count in terms.items() if word in idf} for terms in counts]
    query_vector = {word: (1 + math.log(count)) * idf[word] for word, count in q.items() if word in idf}
    q_norm = math.sqrt(sum(v * v for v in query_vector.values())) or 1
    lexical = [sum(value * query_vector.get(word, 0) for word, value in vector.items()) /
               ((math.sqrt(sum(v * v for v in vector.values())) or 1) * q_norm) for vector in vectors]
    stats = {"method": "tfidf_lsa", "semantic_applied": False, "lsa_dimensions": 0, "vocabulary_size": len(vocabulary), "warning": None}
    if np is None:
        stats.update(method="lexical_tfidf", warning="NumPy is unavailable; semantic LSA is disabled and retrieval is lexical only.")
        return lexical, stats
    if len(documents) < 3 or len(vocabulary) < 3 or not query_vector:
        stats.update(method="tfidf", warning="Insufficient local corpus/query overlap for useful truncated-SVD/LSA; using TF-IDF.")
        return lexical, stats
    try:
        # NumPy-only randomized truncated SVD. Small low-rank projection avoids
        # a full expensive SVD on the entire document/term matrix.
        terms = {word: index for index, word in enumerate(vocabulary)}
        matrix = np.zeros((len(documents), len(vocabulary)), dtype=np.float32)
        q_array = np.zeros(len(vocabulary), dtype=np.float32)
        for row, vector in enumerate(vectors):
            norm = math.sqrt(sum(value * value for value in vector.values())) or 1
            for word, value in vector.items():
                matrix[row, terms[word]] = value / norm
        for word, value in query_vector.items():
            q_array[terms[word]] = value / q_norm
        dimensions = min(32, len(documents) - 1, len(vocabulary) - 1)
        width = min(dimensions + 6, min(matrix.shape))
        random = np.random.default_rng(0).standard_normal((matrix.shape[1], width)).astype(np.float32)
        projected = matrix @ random
        for _ in range(2):
            projected, _ = np.linalg.qr(projected, mode="reduced")
            projected = matrix @ (matrix.T @ projected)
        basis, _ = np.linalg.qr(projected, mode="reduced")
        _, singular, right = np.linalg.svd(basis.T @ matrix, full_matrices=False)
        dimensions = min(dimensions, int(np.sum(singular > 1e-6)))
        latent = matrix @ right[:dimensions].T
        latent_query = q_array @ right[:dimensions].T
        denominator = np.linalg.norm(latent, axis=1) * max(float(np.linalg.norm(latent_query)), 1e-12)
        semantic = np.divide(latent @ latent_query, denominator, out=np.zeros(len(documents)), where=denominator > 1e-12)
        scores = [.55 * lexical[index] + .45 * max(0.0, float(value)) for index, value in enumerate(semantic)]
        stats.update(semantic_applied=dimensions > 0, lsa_dimensions=dimensions)
        return scores, stats
    except (ValueError, MemoryError, np.linalg.LinAlgError) as exc:
        stats.update(method="lexical_tfidf", warning=f"Local LSA unavailable ({type(exc).__name__}); using lexical TF-IDF.")
        return lexical, stats


def knowledge_summary(path: str | Path) -> dict[str, Any]:
    """Read-only UI inspection; does not create the library or run migrations."""
    result = {"path": str(Path(path).resolve()), "exists": Path(path).is_file(), "accepted_claims": 0,
              "rejected_claims": 0, "audit_records": 0, "quarantined_claims": 0, "sources": 0,
              "coverage_records": 0, "revoked_sources": 0, "revision": 0, "retrieval_method": "tfidf_lsa" if np is not None else "lexical_tfidf",
              "warning": None if np is not None else "NumPy is unavailable; lexical retrieval only."}
    if not result["exists"]:
        return result
    try:
        with _readonly(path) as db:
            result.update(accepted_claims=db.execute("SELECT COUNT(*) FROM knowledge_claims WHERE quarantined=0").fetchone()[0],
                          quarantined_claims=db.execute("SELECT COUNT(*) FROM knowledge_claims WHERE quarantined=1").fetchone()[0],
                          rejected_claims=db.execute("SELECT COUNT(DISTINCT claim_id) FROM knowledge_rejections").fetchone()[0],
                          audit_records=db.execute("SELECT COUNT(*) FROM knowledge_rejections").fetchone()[0],
                          coverage_records=db.execute("SELECT COUNT(*) FROM knowledge_coverage").fetchone()[0],
                          revision=int(db.execute("SELECT value FROM knowledge_meta WHERE key='revision'").fetchone()[0]))
            result["sources"] = len({json.loads(row[0]).get("source_id") for row in db.execute("SELECT source_json FROM knowledge_claims")})
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_source_revocations'").fetchone():
                result["revoked_sources"] = db.execute("SELECT COUNT(*) FROM knowledge_source_revocations").fetchone()[0]
    except (sqlite3.Error, ValueError, TypeError) as exc:
        result["warning"] = "Knowledge library could not be read: " + str(exc)
    return result


class SharedKnowledgeBase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=60)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _revision(db: sqlite3.Connection) -> None:
        db.execute("UPDATE knowledge_meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")

    def summary(self) -> dict[str, Any]:
        return knowledge_summary(self.path)

    def import_run(self, run_db_path: str | Path) -> dict[str, Any]:
        origin = str(Path(run_db_path).resolve())
        if Path(origin) == self.path:
            raise ValueError("Knowledge library and source run must be different files")
        # Never instantiate RunDatabase for the origin: its constructor writes.
        with _readonly(origin) as source_db:
            sources = {row["source_id"]: _source(dict(row)) for row in source_db.execute("SELECT * FROM sources")}
            claims = [_clean({key: dict(row).get(key) for key in CLAIM_FIELDS}) for row in source_db.execute("SELECT * FROM claims")]
            tables = {row[0] for row in source_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            research = [dict(row) for row in source_db.execute("SELECT stage,item_id,result_json FROM tasks WHERE status='complete' AND stage IN ('node_research','edge_research')")] if "tasks" in tables else []
            context = {}
            if "metadata" in tables:
                row = source_db.execute("SELECT value FROM metadata WHERE key='adaptive_biological_context'").fetchone()
                context = _decode(row[0], {}) if row else {}
        counts = {"accepted_imported": 0, "accepted_existing": 0, "rejected_imported": 0,
                  "rejected_existing": 0, "coverage_imported": 0, "source_claims": len(claims), "quarantined": 0}
        with self._connect() as db:
            changed = False
            # A later run can discover a retraction affecting facts first imported
            # from another run. Apply unsafe source observations library-wide,
            # including claim IDs absent from the importing run.
            unsafe_sources = [source for source in sources.values() if _source_errors(source)]
            previous_sources = [json.loads(row[0]) for row in db.execute("SELECT source_json FROM knowledge_claims")]
            for unsafe in unsafe_sources:
                previously_accepted = any(unsafe.get("source_id") == source.get("source_id") or unsafe.get("url") == source.get("url")
                                          for source in previous_sources)
                # Failed first-time retrieval is not a permanent blacklist. An
                # actual retraction, or revoked safety of previously used facts,
                # persists so stale imports cannot add new IDs from that source.
                if unsafe.get("retracted") != 1 and not previously_accepted:
                    continue
                revocation_id = _digest(unsafe.get("source_id"), unsafe.get("url"))
                changed = bool(db.execute("INSERT OR IGNORE INTO knowledge_source_revocations VALUES (?,?,?,?,?,?,?)",
                    (revocation_id, str(unsafe.get("source_id") or ""), str(unsafe.get("url") or ""), _json(unsafe),
                     _json(_source_errors(unsafe)), origin, now())).rowcount) or changed
            revoked = [json.loads(row[0]) for row in db.execute("SELECT source_json FROM knowledge_source_revocations")]
            for prior in db.execute("SELECT * FROM knowledge_claims WHERE quarantined=0").fetchall():
                previous_source = json.loads(prior["source_json"])
                unsafe = next((source for source in revoked
                               if source.get("source_id") == previous_source.get("source_id")
                               or source.get("url") == previous_source.get("url")), None)
                if unsafe is None:
                    continue
                reasons = ["Source-wide safety revocation on import", *_source_errors(unsafe)]
                claim = json.loads(prior["claim_json"])
                audit_id = _digest(origin, claim, unsafe, reasons)
                db.execute("INSERT OR IGNORE INTO knowledge_rejections VALUES (?,?,?,?,?,?,?)",
                           (audit_id, prior["claim_id"], origin, prior["claim_json"], _json(unsafe), _json(reasons), now()))
                db.execute("UPDATE knowledge_claims SET quarantined=1 WHERE claim_id=?", (prior["claim_id"],))
                counts["quarantined"] += 1
                changed = True
            for claim in claims:
                source = sources.get(claim.get("source_id"), {})
                errors = _acceptance_errors(claim, source)
                if any(source.get("source_id") == record.get("source_id") or source.get("url") == record.get("url") for record in revoked):
                    errors.append("source has a durable safety revocation; explicit re-verification review is required")
                prior = db.execute("SELECT * FROM knowledge_claims WHERE claim_id=?", (claim["claim_id"],)).fetchone()
                content_hash = _digest(claim)
                if prior and prior["content_hash"] != content_hash:
                    errors.append("existing claim ID has conflicting content/status; quarantined pending explicit re-verification")
                if prior and json.loads(prior["source_json"]).get("url") != source.get("url"):
                    errors.append("existing claim source identity has conflicting URLs")
                if errors:
                    audit_id = _digest(origin, claim, source, errors)
                    inserted = db.execute("INSERT OR IGNORE INTO knowledge_rejections VALUES (?,?,?,?,?,?,?)",
                        (audit_id, claim["claim_id"], origin, _json(claim), _json(source), _json(errors), now())).rowcount
                    counts["rejected_imported" if inserted else "rejected_existing"] += 1
                    if prior and not prior["quarantined"]:
                        db.execute("UPDATE knowledge_claims SET quarantined=1 WHERE claim_id=?", (claim["claim_id"],))
                        counts["quarantined"] += 1
                        changed = True
                    changed = changed or bool(inserted)
                    continue
                if prior:
                    counts["accepted_existing"] += 1
                    old_source = json.loads(prior["source_json"])
                    old_meta = _decode(old_source.get("metadata_json"), {})
                    new_meta = _decode(source.get("metadata_json"), {})
                    excerpts = list(dict.fromkeys(old_meta.get("verified_excerpts", []) + new_meta.get("verified_excerpts", [])))
                    old_source["metadata_json"] = _json({"verified_excerpts": excerpts})
                    if _json(old_source) != prior["source_json"]:
                        db.execute("UPDATE knowledge_claims SET source_json=? WHERE claim_id=?", (_json(old_source), claim["claim_id"]))
                        changed = True
                else:
                    db.execute("INSERT INTO knowledge_claims VALUES (?,?,?,?,?,?,0,?)", (claim["claim_id"], claim["item_type"], claim["item_id"], _json(claim), _json(source), content_hash, now()))
                    counts["accepted_imported"] += 1
                    changed = True
                changed = bool(db.execute("INSERT OR IGNORE INTO knowledge_origins VALUES (?,?)", (claim["claim_id"], origin)).rowcount) or changed
            for item in research:
                result = _decode(item["result_json"], {})
                kind = "node" if item["stage"] == "node_research" else "edge"
                key = "LEGACY-" + _digest(origin, item["stage"], item["item_id"])
                # Explicitly not an answer to a new directed/mechanistic question.
                payload = _clean({"item_type": kind, "item_id": item["item_id"], "context": context,
                    "origin_run": origin, "legacy_item_wide": True, "reusable": False,
                    "verdict": result.get("verdict", "uncertain"), "accepted_claim_ids": result.get("accepted_claim_ids", []),
                    "unresolved_questions": result.get("unresolved_questions", []),
                    "note": "Legacy research completion is not scoped coverage of a new question; researcher prose is not a fact."})
                previous = db.execute("SELECT payload_json FROM knowledge_coverage WHERE coverage_key=?", (key,)).fetchone()
                if not previous or previous[0] != _json(payload):
                    db.execute("INSERT OR REPLACE INTO knowledge_coverage VALUES (?,?,?)", (key, _json(payload), now()))
                    counts["coverage_imported"] += 1
                    changed = True
            if changed:
                self._revision(db)
        return {**counts, "summary": self.summary()}

    def record_coverage(self, key: str, payload: dict[str, Any]) -> None:
        if not key or not isinstance(payload, dict):
            raise ValueError("Coverage requires an explicit key and object payload")
        encoded = _json(_clean(payload))
        with self._connect() as db:
            prior = db.execute("SELECT payload_json FROM knowledge_coverage WHERE coverage_key=?", (key,)).fetchone()
            if not prior or prior[0] != encoded:
                db.execute("INSERT OR REPLACE INTO knowledge_coverage VALUES (?,?,?)", (key, encoded, now()))
                self._revision(db)

    def get_coverage(self, key: str) -> dict[str, Any] | None:
        with _readonly(self.path) as db:
            row = db.execute("SELECT payload_json FROM knowledge_coverage WHERE coverage_key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def retrieve(self, symbols: list[str], edge_ids: list[str], context: dict[str, Any], query: str = "",
                 limit: int = 40, max_chars: int = 24000) -> dict[str, Any]:
        nodes = {str(x).strip().casefold() for x in symbols}
        pairs = {_pair(str(x)) for x in edge_ids} - {None}
        def eligible(kind: str, identifier: str) -> bool:
            return (kind == "node" and identifier.casefold() in nodes) or (kind == "edge" and _pair(identifier) in pairs)
        with _readonly(self.path) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM knowledge_claims WHERE quarantined=0 ORDER BY claim_id") if eligible(row["item_type"], row["item_id"])]
            coverage = []
            for row in db.execute("SELECT coverage_key,payload_json FROM knowledge_coverage ORDER BY coverage_key"):
                item = json.loads(row["payload_json"])
                if eligible(str(item.get("item_type", item.get("kind", ""))), str(item.get("item_id", ""))):
                    coverage.append({"key": row["coverage_key"], **item, "context_match": _context_annotation(item.get("context", {}), context),
                                     "note_for_retrieval": "Coverage metadata only, never accepted biological evidence."})
            revision = int(db.execute("SELECT value FROM knowledge_meta WHERE key='revision'").fetchone()[0])
        candidates, documents = [], []
        for row in rows:
            claim, source = json.loads(row["claim_json"]), json.loads(row["source_json"])
            if _acceptance_errors(claim, source):
                continue
            claim.update(url=source["url"], title=source.get("title") or "", pmid=source.get("pmid") or "", doi=source.get("doi") or "",
                         source_text_hash=source.get("text_hash"),
                         context=_decode(claim["context_json"], {}), context_match=_context_annotation(_decode(claim["context_json"], {}), context),
                         direction_match=_direction_annotation(claim, symbols))
            passages = [{"claim_id": claim["claim_id"], "source_id": claim["source_id"], "url": source["url"],
                         "kind": "verified_quotation", "text": claim["quotation"]}]
            for excerpt in _decode(source.get("metadata_json"), {}).get("verified_excerpts", []):
                if quote_is_present(claim["quotation"], excerpt):
                    passages.append({"claim_id": claim["claim_id"], "source_id": claim["source_id"], "url": source["url"],
                                     "kind": "verified_source_excerpt", "text": excerpt[:12000]})
            candidates.append((claim, passages))
            documents.append(" ".join([claim["subject"], claim["predicate"], claim["object"], claim["context_json"], claim["quotation"],
                                       *(p["text"] for p in passages[1:])]))
        scores, rank_stats = _rank(documents, query or " ".join(symbols) + " " + _json(context))
        result = {"accepted_claims": [], "passages": [], "coverage": [], "revision": revision,
                  "stats": {**rank_stats, "candidate_claims": len(candidates), "returned_claims": 0, "truncated": False,
                            "scope": "Exact node IDs and unordered edge-pair IDs; direction and context are annotations, not assumed."}}
        # max_chars bounds the whole serialized payload, not just quoted text.
        cap = max(0, int(max_chars))
        content_cap = max(0, cap - 256)  # Reserve space for final statistics.
        for index in sorted(range(len(candidates)), key=lambda i: (-scores[i], candidates[i][0]["claim_id"])):
            if len(result["accepted_claims"]) >= max(0, int(limit)):
                result["stats"]["truncated"] = True
                break
            claim, passages = candidates[index]
            claim["retrieval_score"] = round(scores[index], 6)
            result["accepted_claims"].append(claim)
            result["passages"].append(passages[0])
            if len(_json(result)) > content_cap:
                result["accepted_claims"].pop()
                result["passages"].pop()
                result["stats"]["truncated"] = True
                continue
            for passage in passages[1:]:
                result["passages"].append(passage)
                if len(_json(result)) > content_cap:
                    result["passages"].pop()
                    result["stats"]["truncated"] = True
        for item in coverage[:40]:
            result["coverage"].append(item)
            if len(_json(result)) > content_cap:
                result["coverage"].pop()
                result["stats"]["truncated"] = True
                break
        result["stats"]["returned_claims"] = len(result["accepted_claims"])
        result["stats"]["serialized_chars"] = 0
        for _ in range(3):
            result["stats"]["serialized_chars"] = len(_json(result))
        # Tiny budgets cannot hold even the mandatory empty envelope.
        if cap < len(_json(result)):
            result["stats"]["budget_too_small"] = True
        return result

    def hydrate_run(self, claim_ids: list[str], run_database: RunDatabase) -> int:
        """Copy accepted facts into a new run; never replace existing rejection/content."""
        wanted = set(claim_ids)
        with _readonly(self.path) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM knowledge_claims WHERE quarantined=0") if row["claim_id"] in wanted]
        copied = 0
        with run_database.connect() as db:
            for row in rows:
                claim, source = json.loads(row["claim_json"]), json.loads(row["source_json"])
                if _acceptance_errors(claim, source):
                    continue
                existing = db.execute("SELECT * FROM claims WHERE claim_id=?", (claim["claim_id"],)).fetchone()
                if existing:
                    # Existing local decisions (including rejected) remain authoritative.
                    continue
                conflict = db.execute("SELECT * FROM sources WHERE source_id=? OR url=?", (source["source_id"], source["url"])).fetchall()
                if conflict:
                    current = dict(conflict[0])
                    if len(conflict) != 1 or current["source_id"] != source["source_id"] or current["url"] != source["url"] or current["retracted"] or not current["retrieved"]:
                        continue
                    if current.get("text_hash") and source.get("text_hash") and current["text_hash"] != source["text_hash"]:
                        continue
                else:
                    db.execute("INSERT INTO sources (" + ",".join(SOURCE_FIELDS) + ") VALUES (" + ",".join("?" for _ in SOURCE_FIELDS) + ")", [source.get(key) for key in SOURCE_FIELDS])
                db.execute("INSERT INTO claims (" + ",".join(CLAIM_FIELDS) + ") VALUES (" + ",".join("?" for _ in CLAIM_FIELDS) + ")", [claim.get(key) for key in CLAIM_FIELDS])
                copied += 1
        return copied
