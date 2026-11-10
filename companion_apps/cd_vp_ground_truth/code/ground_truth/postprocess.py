"""Deterministic gates and labels for ground-truth protocol v0.6.

LLMs extract candidate evidence.  This module decides whether an extraction is
admissible and how it is labelled.  Keeping this code free of model calls makes
the calibration set reproducible and independently testable.
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from .evidence_profiles import profile
from .schemas import EDGE_METHODS


PRIMARY_METHODS = {
    "IHC_IF", "IMMUNO_EM", "IMMUNOBLOT_ISOLATED", "ISH", "FUNCTIONAL",
    "PHOSPHO_IMMUNOBLOT", "PHOSPHO_IMMUNOSTAIN", "IMMUNOBLOT",
    "IMMUNOSTAIN", "ACTIVITY_ASSAY", "IN_VITRO_PURIFIED", "IN_VITRO_IP",
    "SITE_MUTANT", "CELL_GENETIC", "CELL_INHIBITOR", "PHYSICAL", "NEGATIVE",
} | set(EDGE_METHODS)
DIRECT_METHODS = {"IN_VITRO_PURIFIED", "SITE_MUTANT", "CELL_GENETIC"}
CAUSAL_METHODS = {"SITE_MUTANT", "CELL_GENETIC", "CELL_INHIBITOR"}
POSITIVE_EDGE_METHODS = {
    "IN_VITRO_PURIFIED", "IN_VITRO_IP", "SITE_MUTANT", "CELL_GENETIC",
    "CELL_INHIBITOR", "PHYSICAL",
}
NATIVE_CONTEXTS = {"native", "native_cd"}
CELL_LINE_CONTEXTS = {"cell_line", "cd_cell_line"}
VP_STIMULI = {"AVP", "dDAVP"}
CAMP_STIMULI = {"forskolin", "cAMP_analog"}
TRANSCRIPTIONAL_PATTERNS = (
    r"\btranscription(?:al|ally)?\b",
    r"\bmRNA\b",
    r"\bpromoter\b",
    r"\bgene[- ]expression\b",
    r"\btranscript abundance\b",
    r"\btranscription factor\b",
)


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u00ad", "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def verify_quote(quote: object, retrieved_text: object, threshold: float = 0.95) -> float:
    """Return a deterministic quote-match score in [0, 1].

    Exact normalized containment receives 1.0.  The fuzzy fallback exists for
    publisher Unicode/whitespace transformations, not paraphrases.
    """

    needle = normalize_text(quote)
    haystack = normalize_text(retrieved_text)
    if not needle or not haystack:
        return 0.0
    if needle in haystack:
        return 1.0
    if len(haystack) <= len(needle) * 2:
        return SequenceMatcher(None, needle, haystack, autojunk=False).ratio()
    words = needle.split()
    hay_words = haystack.split()
    width = max(len(words), 1)
    best = 0.0
    for start in range(0, max(1, len(hay_words) - width + 1)):
        window = " ".join(hay_words[start:start + width])
        best = max(best, SequenceMatcher(None, needle, window, autojunk=False).ratio())
        if best >= 1.0:
            break
    return best if best >= threshold else best


def _is_primary(metadata: dict[str, Any]) -> bool:
    publication_types = {
        normalize_text(value).casefold() for value in metadata.get("publication_types", [])
    }
    return not any("review" in value for value in publication_types)


def is_transcription_mediated(item: dict[str, Any]) -> bool:
    """Identify response/edge evidence whose reported mechanism is transcriptional.

    Presence-only localization records are intentionally exempt.  This gate is
    conservative: it requires explicit transcription/gene-expression language
    rather than treating every abundance immunoblot as slow regulation.
    """
    if "polarity" in item and "stimulus" not in item:
        return False
    searchable = " ".join(
        normalize_text(item.get(key))
        for key in ("quote", "readout", "relation", "cell_or_tissue")
    )
    return any(re.search(pattern, searchable, flags=re.IGNORECASE) for pattern in TRANSCRIPTIONAL_PATTERNS)


def gate_evidence_item(
    item: dict[str, Any],
    *,
    retrieved_text_by_pmid: dict[str, str],
    metadata_by_pmid: dict[str, dict[str, Any]],
    allowed_methods: set[str] | None = None,
    sequence_by_species_symbol: dict[str, str] | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Apply quote, primary-study, experimental-method, site, and family gates."""

    candidate = dict(item)
    pmid = re.sub(r"\D", "", str(candidate.get("pmid", "")))
    candidate["pmid"] = pmid
    if not pmid or pmid not in retrieved_text_by_pmid:
        return None, "quote_source_not_retrieved"
    score = verify_quote(candidate.get("quote"), retrieved_text_by_pmid[pmid])
    candidate["quote_match"] = score
    if score < 0.95:
        return None, "quote_match_below_0.95"
    metadata = metadata_by_pmid.get(pmid)
    if not metadata:
        return None, "missing_pubmed_metadata"
    if not _is_primary(metadata):
        return None, "not_primary_research"
    method = str(candidate.get("method", ""))
    if method not in (allowed_methods or PRIMARY_METHODS):
        return None, "method_not_experimental"
    if is_transcription_mediated(candidate):
        return None, "transcription_mediated_not_transient"
    site = candidate.get("site")
    if isinstance(site, dict) and site.get("position"):
        species = normalize_text(site.get("species") or candidate.get("species")).casefold()
        symbol = normalize_text(candidate.get("target"))
        sequence_key = f"{species}:{symbol.casefold()}"
        sequence = (sequence_by_species_symbol or {}).get(sequence_key)
        if not sequence:
            candidate["site"] = None
            candidate["site_verified"] = False
            candidate["site_unverified_reason"] = "site_sequence_unavailable"
        else:
            position = int(site["position"])
            residue = str(site.get("residue", "")).upper()
            if position < 1 or position > len(sequence) or sequence[position - 1].upper() != residue:
                candidate["site"] = None
                candidate["site_verified"] = False
                candidate["site_unverified_reason"] = "site_residue_mismatch"
            else:
                candidate["site_verified"] = True
    candidate["citation"] = {
        key: metadata.get(key)
        for key in ("title", "doi", "journal", "year", "first_author", "last_author")
    }
    candidate["pubmed_url"] = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    return candidate, "admitted"


def build_node_record(symbol: str, admitted: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(admitted)
    present = [item for item in items if item.get("polarity") == "present"]
    absent = [item for item in items if item.get("polarity") == "absent"]
    native = [item for item in present if item.get("context") in NATIVE_CONTEXTS]
    cell_line = [item for item in present if item.get("context") in CELL_LINE_CONTEXTS]
    if present and absent:
        label = "CONFLICT"
    elif native:
        label = "POS_NATIVE"
    elif cell_line:
        label = "POS_CELL_LINE"
    elif absent:
        label = "NEG_EXPLICIT"
    else:
        label = "UNLABELED"
    pka_items = [
        {**item, "target": symbol, "node_vp_evidence": True}
        for item in items if item.get("pka_relationship") in {"direct_pka", "pka_dependent"}
    ]
    return {
        "symbol": symbol,
        "label": label,
        "pmids": sorted({item["pmid"] for item in items if item.get("pmid")}),
        "evidence": items,
        "pka_evidence": pka_items,
        # Compatibility alias for existing audit consumers. In v0.6 this field
        # contains only evidence with a direct or experimentally required PKA
        # relationship, not every AVP/cAMP response.
        "vp_evidence": pka_items,
    }


def build_stream_reference_record(
    pair: tuple[str, str],
    validation_profile: str,
    admitted: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Create an auditable H1/H0 candidate for one pair and evidence class.

    H0 requires an explicit null experiment.  Missing literature and database
    noncoverage remain UNLABELED. Evidence that is only cAMP/vasopressin-related
    without testing PKA is retained for review but cannot enter either reference
    class.
    """

    definition = profile(validation_profile)
    items = list(admitted)
    pka_items = [
        item for item in items
        if item.get("pka_relationship") in {"direct_pka", "pka_dependent"}
    ]
    positives = [item for item in pka_items if item.get("polarity") == "positive"]
    negatives = [
        item for item in pka_items
        if item.get("polarity") == "negative"
        and normalize_text(item.get("negative_controls"))
    ]
    contextual = [
        item for item in items
        if item.get("pka_relationship") == "camp_or_vasopressin_only"
    ]
    if positives and negatives:
        reference_label = "CONFLICT"
    elif positives:
        reference_label = "H1_POSITIVE"
    elif negatives:
        reference_label = "H0_EXPLICIT_NEGATIVE"
    elif contextual:
        reference_label = "CONTEXT_ONLY"
    else:
        reference_label = "UNLABELED"

    directional = positives or negatives or contextual or items
    direction_counts: dict[tuple[str, str], int] = defaultdict(int)
    for item in directional:
        direction_counts[(str(item.get("actor", pair[0])), str(item.get("target", pair[1])))] += 1
    source, target = max(
        direction_counts,
        key=lambda direction: (direction_counts[direction], direction),
        default=pair,
    )
    if validation_profile in {"physical_interaction", "messenger_binding"}:
        source, target = pair

    return {
        "reference_id": f"{validation_profile}|{source}|{target}",
        "validation_profile": validation_profile,
        "profile_label": definition["label"],
        "calibrates_streams": list(definition["calibrates_streams"]),
        "source": source,
        "target": target,
        "unordered_pair": sorted(pair, key=str.casefold),
        "reference_label": reference_label,
        "h1_reference": reference_label == "H1_POSITIVE",
        "h0_reference": reference_label == "H0_EXPLICIT_NEGATIVE",
        "positive_evidence_count": len(positives),
        "negative_evidence_count": len(negatives),
        "context_only_evidence_count": len(contextual),
        "pmids": sorted({item.get("pmid", "") for item in items if item.get("pmid")}),
        "positive_evidence": positives,
        "negative_evidence": negatives,
        "context_only_evidence": contextual,
        "evidence": items,
    }


def _best_edge_label(items: list[dict[str, Any]]) -> str:
    positive = [item for item in items if item.get("method") in POSITIVE_EDGE_METHODS]
    negative = [item for item in items if item.get("method") == "NEGATIVE"]
    if positive and negative:
        return "CONFLICT"
    if negative:
        return "NEG_EXPLICIT"
    if any(
        item.get("method") == "IN_VITRO_PURIFIED" and item.get("site")
        for item in positive
    ):
        return "DIRECT_SITE"
    if any(
        item.get("method") == "IN_VITRO_PURIFIED"
        or (item.get("method") == "IN_VITRO_IP" and item.get("site"))
        for item in positive
    ):
        return "DIRECT_PROTEIN"
    if any(item.get("method") in CAUSAL_METHODS for item in positive):
        return "CAUSAL"
    if any(item.get("method") == "PHYSICAL" for item in positive):
        return "PHYSICAL"
    return "UNLABELED"


def _supported_edge_labels(items: list[dict[str, Any]]) -> list[str]:
    """Preserve every independently supported relationship class for a pair."""
    labels: list[str] = []
    if any(item.get("method") == "IN_VITRO_PURIFIED" and item.get("site") for item in items):
        labels.append("DIRECT_SITE")
    if any(
        item.get("method") == "IN_VITRO_PURIFIED"
        or (item.get("method") == "IN_VITRO_IP" and item.get("site"))
        for item in items
    ):
        labels.append("DIRECT_PROTEIN")
    if any(item.get("method") in CAUSAL_METHODS for item in items):
        labels.append("CAUSAL")
    if any(item.get("method") == "PHYSICAL" for item in items):
        labels.append("PHYSICAL")
    if any(item.get("method") == "NEGATIVE" for item in items):
        labels.append("NEG_EXPLICIT")
    return labels or ["UNLABELED"]


def _vp_tier(items: list[dict[str, Any]], relation_target: str) -> str:
    edge_vp = [
        item for item in items
        if item.get("stimulus") in VP_STIMULI
        and item.get("context") in NATIVE_CONTEXTS | CELL_LINE_CONTEXTS
        and item.get("method") in DIRECT_METHODS | {"IN_VITRO_IP", "CELL_INHIBITOR"}
    ]
    if edge_vp:
        return "VP_EDGE"
    target_vp = [
        item for item in items
        if item.get("target") == relation_target
        and item.get("stimulus") in VP_STIMULI
        and item.get("context") in NATIVE_CONTEXTS | CELL_LINE_CONTEXTS
    ]
    if target_vp:
        return "VP_TARGET"
    if any(item.get("stimulus") in CAMP_STIMULI for item in items):
        return "VP_CAMP_ONLY"
    return "NONE"


def build_edge_record(pair: tuple[str, str], admitted: Iterable[dict[str, Any]]) -> dict[str, Any]:
    items = list(admitted)
    by_context: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_context[str(item.get("context", "other_cell"))].append(item)
    labels = {context: _best_edge_label(values) for context, values in by_context.items()}
    positive = [item for item in items if item.get("method") in POSITIVE_EDGE_METHODS]
    directions = defaultdict(list)
    for item in positive:
        directions[(str(item.get("actor", "")), str(item.get("target", "")))].append(item)
    direction, directional_items = max(directions.items(), key=lambda value: len(value[1]), default=((pair[0], pair[1]), []))
    source, target = direction
    last_authors = {
        normalize_text(item.get("citation", {}).get("last_author")).casefold()
        for item in directional_items
        if normalize_text(item.get("citation", {}).get("last_author"))
    }
    strong = any(item.get("method") in DIRECT_METHODS for item in directional_items)
    conflict = any(label == "CONFLICT" for label in labels.values())
    if len(last_authors) >= 2 and strong and not conflict:
        establishment = "ESTABLISHED"
    elif len(last_authors) >= 1 and strong and not conflict:
        establishment = "SUPPORTED"
    else:
        establishment = "TENTATIVE"
    strongest = _best_edge_label(items)
    supported_labels = _supported_edge_labels(items)
    # Physical binding/complex evidence is intrinsically undirected. Preserve
    # it even when the majority causal direction points the other way.
    physical_evidence = [item for item in items if item.get("method") == "PHYSICAL"]
    vp_tier = _vp_tier(items, target)
    final = (
        strongest in {"DIRECT_SITE", "DIRECT_PROTEIN", "CAUSAL"}
        and vp_tier in {"VP_EDGE", "VP_TARGET"}
        and establishment in {"ESTABLISHED", "SUPPORTED"}
        and not conflict
    )
    return {
        "edge_id": f"{source}|{target}|{strongest}",
        "source": source,
        "target": target,
        "unordered_pair": sorted(pair, key=str.casefold),
        "edge_label_by_context": labels,
        "best_label": strongest,
        "supported_labels": supported_labels,
        "physical_interaction_supported": bool(physical_evidence),
        "physical_evidence": physical_evidence,
        "vp_tier": vp_tier,
        "establishment_tier": establishment,
        "n_independent_labs": len(last_authors),
        "n_papers": len({item.get("pmid") for item in directional_items if item.get("pmid")}),
        "evidence": directional_items,
        "conflicts": [item for item in items if item.get("method") == "NEGATIVE"] if conflict else [],
        "in_final_list": final,
        "exclusion_reason": "" if final else _edge_exclusion_reason(strongest, vp_tier, establishment, conflict),
    }


def _edge_exclusion_reason(label: str, vp_tier: str, establishment: str, conflict: bool) -> str:
    if conflict:
        return "unresolved_conflict"
    if label not in {"DIRECT_SITE", "DIRECT_PROTEIN", "CAUSAL"}:
        return f"nonqualifying_edge_label:{label}"
    if vp_tier not in {"VP_EDGE", "VP_TARGET"}:
        return f"nonqualifying_vasopressin_tier:{vp_tier}"
    return f"nonqualifying_establishment_tier:{establishment}"


def write_outputs(run_dir: Path, edge_records: list[dict[str, Any]], audit_rows: list[dict[str, Any]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    final = [record for record in edge_records if record["in_final_list"]]
    tier_order = {"ESTABLISHED": 0, "SUPPORTED": 1}
    vp_order = {"VP_EDGE": 0, "VP_TARGET": 1}
    final.sort(key=lambda item: (
        tier_order.get(item["establishment_tier"], 9),
        vp_order.get(item["vp_tier"], 9),
        -item["n_independent_labs"], -item["n_papers"], item["edge_id"],
    ))
    with (run_dir / "edges.jsonl").open("w", encoding="utf-8") as handle:
        for rank, record in enumerate(final, start=1):
            handle.write(json.dumps({**record, "rank": rank}, ensure_ascii=False, default=str) + "\n")
    with (run_dir / "edges.tsv").open("w", encoding="utf-8", newline="") as handle:
        fields = ["rank", "source", "target", "best_label", "supported_labels", "physical_interaction_supported", "vp_tier", "establishment_tier", "n_independent_labs", "n_papers", "pmids", "pubmed_links"]
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for rank, record in enumerate(final, start=1):
            pmids = sorted({item["pmid"] for item in record["evidence"] if item.get("pmid")})
            writer.writerow({
                **{key: record.get(key, "") for key in fields}, "rank": rank,
                "supported_labels": ";".join(record.get("supported_labels", [])),
                "pmids": ";".join(pmids),
                "pubmed_links": ";".join(f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" for pmid in pmids),
            })
    with (run_dir / "audit.tsv").open("w", encoding="utf-8", newline="") as handle:
        fields = sorted({key for row in audit_rows for key in row}) or ["reason"]
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(audit_rows)


def write_stream_reference_outputs(
    run_dir: Path,
    records: list[dict[str, Any]],
    audit_rows: list[dict[str, Any]],
) -> None:
    """Write v0.6 class-specific positive, negative, and review references."""

    run_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(
        records,
        key=lambda item: (
            item["validation_profile"],
            item["reference_label"],
            item["source"],
            item["target"],
        ),
    )
    h1 = [record for record in ordered if record["h1_reference"]]
    h0 = [record for record in ordered if record["h0_reference"]]

    def write_jsonl(name: str, rows: list[dict[str, Any]]) -> None:
        with (run_dir / name).open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    write_jsonl("evidence_stream_references.jsonl", ordered)
    write_jsonl("positive_references.jsonl", h1)
    write_jsonl("negative_references.jsonl", h0)
    # Compatibility: edges.jsonl now contains the accepted positive reference
    # edges. The complete positive/negative/review population remains in the
    # evidence_stream_references file.
    write_jsonl("edges.jsonl", h1)

    fields = [
        "validation_profile", "profile_label", "calibrates_streams", "source",
        "target", "reference_label", "h1_reference", "h0_reference",
        "positive_evidence_count", "negative_evidence_count",
        "context_only_evidence_count", "pmids", "pubmed_links",
    ]
    with (run_dir / "evidence_stream_references.tsv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for record in ordered:
            pmids = record.get("pmids", [])
            writer.writerow({
                **{key: record.get(key, "") for key in fields},
                "calibrates_streams": ";".join(record.get("calibrates_streams", [])),
                "pmids": ";".join(pmids),
                "pubmed_links": ";".join(
                    f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" for pmid in pmids
                ),
            })
    with (run_dir / "edges.tsv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for record in h1:
            pmids = record.get("pmids", [])
            writer.writerow({
                **{key: record.get(key, "") for key in fields},
                "calibrates_streams": ";".join(record.get("calibrates_streams", [])),
                "pmids": ";".join(pmids),
                "pubmed_links": ";".join(
                    f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" for pmid in pmids
                ),
            })
    with (run_dir / "audit.tsv").open("w", encoding="utf-8", newline="") as handle:
        audit_fields = sorted({key for row in audit_rows for key in row}) or ["reason"]
        writer = csv.DictWriter(
            handle, fieldnames=audit_fields, delimiter="\t", extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(audit_rows)
