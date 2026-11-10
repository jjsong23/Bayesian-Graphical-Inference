"""Deterministic source and quotation checks for proposed literature claims."""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


RETRACTION_MARKERS = (
    "retracted publication", "retraction notice", "this article has been retracted",
    "retracted article", "withdrawn article", "article withdrawn",
)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            self.parts.append(data)


def normalize_text(value: str) -> str:
    value = html.unescape(value).replace("\u00a0", " ")
    value = re.sub(r"[\u2010-\u2015]", "-", value)
    value = re.sub(r"[\u2018\u2019]", "'", value)
    value = re.sub(r"[\u201c\u201d]", '"', value)
    return re.sub(r"\s+", " ", value).strip().casefold()


def quote_is_present(quote: str, page_text: str) -> bool:
    quote_norm = normalize_text(quote)
    page_norm = normalize_text(page_text)
    if len(quote_norm) < 20 or len(quote_norm.split()) < 5:
        return False
    if quote_norm in page_norm:
        return True
    # Permit formatting-only omissions while retaining every alphanumeric token.
    quote_tokens = re.findall(r"[a-z0-9]+", quote_norm)
    page_tokens = re.findall(r"[a-z0-9]+", page_norm)
    if len(quote_tokens) < 8:
        return False
    window = len(quote_tokens)
    return any(page_tokens[index:index + window] == quote_tokens for index in range(max(0, len(page_tokens) - window + 1)))


def source_type(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    if host.endswith("pubmed.ncbi.nlm.nih.gov"):
        return "pubmed"
    if host.endswith("pmc.ncbi.nlm.nih.gov") or host.endswith("ncbi.nlm.nih.gov"):
        return "ncbi"
    if host.endswith("uniprot.org"):
        return "uniprot"
    if host.endswith("doi.org"):
        return "doi"
    return "publisher_or_other"


def quotation_excerpt(page_text: str, quotation: str, limit: int = 12000) -> str:
    """Center verification on the quoted passage, not the first page of a long paper."""
    text = normalize_text(page_text)
    quote = normalize_text(quotation)
    position = text.find(quote)
    if position < 0:
        # Same formatting-only fallback as quote_is_present, with original offsets.
        tokens = list(re.finditer(r"[a-z0-9]+", text))
        wanted = re.findall(r"[a-z0-9]+", quote)
        values = [match.group() for match in tokens]
        for index in range(max(0, len(values) - len(wanted) + 1)):
            if wanted and values[index:index + len(wanted)] == wanted:
                position = tokens[index].start()
                break
    start = max(0, position - limit // 2)
    return text[start:start + limit]


@dataclass
class RetrievedSource:
    url: str
    status: int
    text: str
    title: str
    retracted: bool
    text_hash: str


def retrieve(url: str, timeout: int = 30) -> RetrievedSource:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Only HTTP(S) sources are allowed")
    request = Request(url, headers={"User-Agent": "PathPicker/0.1 evidence verification"})
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - source URL is recorded and audited
            body = response.read(6_000_000)
            status = int(response.status)
            content_type = str(response.headers.get("Content-Type", ""))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not retrieve source {url}: {exc}") from exc
    if "pdf" in content_type.casefold() or body.startswith(b"%PDF"):
        raise RuntimeError("PDF source requires manual review; HTML/text quotation verification only")
    decoded = body.decode("utf-8", errors="replace")
    parser = TextExtractor()
    parser.feed(decoded)
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    title_match = re.search(r"<title[^>]*>(.*?)</title>", decoded, re.I | re.S)
    title = re.sub(r"\s+", " ", html.unescape(title_match.group(1))).strip() if title_match else ""
    normalized = normalize_text(f"{title} {text[:20000]}")
    retracted = any(marker in normalized for marker in RETRACTION_MARKERS)
    return RetrievedSource(url, status, text, title, retracted, hashlib.sha256(text.encode("utf-8")).hexdigest())


def stable_id(prefix: str, *values: str, size: int = 16) -> str:
    digest = hashlib.sha256("\0".join(values).encode("utf-8")).hexdigest()[:size].upper()
    return f"{prefix}-{digest}"


def validate_narrative(text: str, accepted_claim_ids: set[str]) -> list[str]:
    """Return validation errors for an abstract-like hypothesis narrative."""

    errors: list[str] = []
    claimed = set(re.findall(r"\[(CLM-[0-9A-F]+)\]", text))
    unknown = claimed - accepted_claim_ids
    if unknown:
        errors.append("Unknown or unaccepted claim IDs: " + ", ".join(sorted(unknown)))
    sentences = [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]
    for index, sentence in enumerate(sentences, start=1):
        if "[INFERENCE]" not in sentence and not re.search(r"\[CLM-[0-9A-F]+\]", sentence):
            errors.append(f"Sentence {index} lacks an accepted claim ID or [INFERENCE]")
    if len(text.split()) < 80:
        errors.append("Narrative is shorter than 80 words")
    if len(text.split()) > 300:
        errors.append("Narrative is longer than 300 words")
    return errors


def compact_evidence(payload: dict[str, Any]) -> dict[str, Any]:
    """Retain all stream results while dropping bulky calibration histograms from prompts."""

    ledger = payload.get("evidence_ledger") or {}
    evidence = []
    for item in ledger.get("evidence", ledger.get("streams", [])):
        evidence.append({
            "stream_id": item.get("stream_id"), "label": item.get("label"),
            "status": item.get("status"), "observed_record": item.get("observed_record", item.get("observed")),
            "enabled": item.get("enabled"), "applied_bf": item.get("applied_bf", item.get("applied_bayes_factor")), "weight": item.get("weight"),
            "raw_value": item.get("raw_value"), "raw_value_label": item.get("raw_value_label"),
            "note": item.get("note"), "provenance": item.get("provenance"),
            "conditional_site_evidence": item.get("conditional_site_evidence"),
        })
    return {
        "id": payload.get("id"), "label": payload.get("label"), "name": payload.get("name"),
        "classes": payload.get("classes"), "node_a": payload.get("node_a"), "node_b": payload.get("node_b"),
        "posterior_probability": payload.get("posterior_probability", ledger.get("posterior_probability")),
        "edge_probability": payload.get("edge_probability"), "directionality": payload.get("directionality"),
        "curated_reference": ledger.get("curated_reference", ledger.get("curated_edge_reference")), "evidence": evidence,
        "evidence_ledger_missing": bool(payload.get("evidence_ledger_missing")),
    }

