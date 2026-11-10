"""Audited PubMed and Europe PMC retrieval tools used by the validator."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


USER_AGENT = "CD-VP-ground-truth-curator/0.6 (literature curation; contact local operator)"


def _get(url: str, *, accept: str = "application/json", timeout: int = 60) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed scientific APIs
        return response.read()


class RetrievalSession:
    """One validator item's retrieval state and immutable tool log."""

    def __init__(self, directory: Path, *, max_searches: int = 10, max_papers: int = 12) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.documents: dict[str, str] = {}
        self.metadata: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.max_searches = max_searches
        self.max_papers = max_papers

    def _enforce_budget(self, name: str, arguments: dict[str, Any]) -> None:
        if name.startswith("search_"):
            searches = sum(str(call.get("tool", "")).startswith("search_") for call in self.calls)
            if searches >= self.max_searches:
                raise RuntimeError(f"search budget exhausted ({self.max_searches})")
        if name.startswith("fetch_"):
            requested = str(arguments.get("pmid") or arguments.get("pmcid") or "").casefold()
            fetched = {
                str(call.get("arguments", {}).get("pmid") or call.get("arguments", {}).get("pmcid") or "").casefold()
                for call in self.calls if str(call.get("tool", "")).startswith("fetch_")
            }
            if requested not in fetched and len(fetched) >= self.max_papers:
                raise RuntimeError(f"paper-reading budget exhausted ({self.max_papers})")

    def _record(self, name: str, arguments: dict[str, Any], result: Any) -> Any:
        self.calls.append({
            "tool": name, "arguments": arguments, "result": result,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        })
        (self.directory / "retrieval_log.json").write_text(
            json.dumps(self.calls, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        return result

    def search_pubmed(self, query: str, max_results: int = 20) -> dict[str, Any]:
        params = urlencode({"db": "pubmed", "term": query, "retmode": "json", "retmax": min(max_results, 50)})
        payload = json.loads(_get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{params}"))
        result = {"query": query, "pmids": payload.get("esearchresult", {}).get("idlist", [])}
        return self._record("search_pubmed", {"query": query, "max_results": max_results}, result)

    def search_europepmc(self, query: str, max_results: int = 20) -> dict[str, Any]:
        params = urlencode({"query": query, "format": "json", "pageSize": min(max_results, 50), "resultType": "core"})
        payload = json.loads(_get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{params}"))
        rows = []
        for item in payload.get("resultList", {}).get("result", []):
            rows.append({key: item.get(key) for key in ("pmid", "pmcid", "title", "authorString", "journalTitle", "pubYear", "isOpenAccess")})
        return self._record("search_europepmc", {"query": query, "max_results": max_results}, {"query": query, "hit_count": int(payload.get("hitCount", 0)), "results": rows})

    def fetch_abstract(self, pmid: str) -> dict[str, Any]:
        pmid = re.sub(r"\D", "", pmid)
        params = urlencode({"db": "pubmed", "id": pmid, "retmode": "xml"})
        root = ET.fromstring(_get(f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?{params}", accept="application/xml"))
        article = root.find(".//PubmedArticle")
        if article is None:
            result = {"pmid": pmid, "found": False}
            return self._record("fetch_abstract", {"pmid": pmid}, result)
        title = "".join(article.findtext(".//ArticleTitle", default=""))
        abstracts = ["".join(node.itertext()) for node in article.findall(".//Abstract/AbstractText")]
        authors = []
        for author in article.findall(".//AuthorList/Author"):
            surname = author.findtext("LastName", default="")
            initials = author.findtext("Initials", default="")
            if surname:
                authors.append(f"{surname} {initials}".strip())
        publication_types = ["".join(node.itertext()) for node in article.findall(".//PublicationType")]
        doi = ""
        for node in article.findall(".//ArticleId"):
            if node.attrib.get("IdType") == "doi":
                doi = node.text or ""
        metadata = {
            "title": title,
            "doi": doi,
            "journal": article.findtext(".//Journal/Title", default=""),
            "year": article.findtext(".//PubDate/Year", default=""),
            "first_author": authors[0] if authors else "",
            "last_author": authors[-1] if authors else "",
            "publication_types": publication_types,
        }
        text = "\n".join([title, *abstracts])
        existing = self.documents.get(pmid, "")
        self.documents[pmid] = f"{existing}\n{text}".strip()
        self.metadata[pmid] = metadata
        result = {"pmid": pmid, "found": True, "metadata": metadata, "text": text}
        return self._record("fetch_abstract", {"pmid": pmid}, result)

    def fetch_fulltext(self, pmid: str = "", pmcid: str = "") -> dict[str, Any]:
        if not pmcid:
            clean_pmid = re.sub(r"\D", "", pmid)
            params = urlencode({"query": f"EXT_ID:{clean_pmid}", "format": "json", "pageSize": 1})
            payload = json.loads(_get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{params}"))
            rows = payload.get("resultList", {}).get("result", [])
            pmcid = str(rows[0].get("pmcid", "")) if rows else ""
        if not pmcid:
            return self._record("fetch_fulltext", {"pmid": pmid, "pmcid": pmcid}, {"found": False, "reason": "no_pmc_fulltext"})
        try:
            xml = _get(f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML", accept="application/xml")
        except Exception as europe_pmc_exc:
            try:
                bioc = json.loads(_get(
                    f"https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{pmcid}/unicode"
                ))
                passages = [
                    passage for collection in bioc
                    for document in collection.get("documents", [])
                    for passage in document.get("passages", [])
                ]
                text = "\n".join(
                    f"[{passage.get('infons', {}).get('section_type', passage.get('infons', {}).get('type', 'text'))}] {passage.get('text', '')}"
                    for passage in passages if passage.get("text")
                )
                bioc_pmid = next((
                    str(document.get("infons", {}).get("article-id_pmid", ""))
                    for collection in bioc for document in collection.get("documents", [])
                    if document.get("infons", {}).get("article-id_pmid")
                ), "")
                source = "NCBI_BioC"
            except Exception as bioc_exc:
                return self._record(
                    "fetch_fulltext", {"pmid": pmid, "pmcid": pmcid},
                    {"pmid": re.sub(r"\D", "", pmid), "pmcid": pmcid, "found": False, "reason": f"fulltext_unavailable:europe_pmc={europe_pmc_exc};bioc={bioc_exc}"},
                )
        else:
            root = ET.fromstring(xml)
            text = "\n".join(" ".join(node.itertext()) for node in root.findall(".//body//p"))
            source = "Europe_PMC_XML"
        resolved_pmid = re.sub(r"\D", "", pmid)
        if not resolved_pmid:
            if source == "NCBI_BioC":
                resolved_pmid = re.sub(r"\D", "", bioc_pmid)
            else:
                resolved_pmid = re.sub(r"\D", "", root.findtext(".//article-id[@pub-id-type='pmid']", default=""))
        if resolved_pmid:
            existing = self.documents.get(resolved_pmid, "")
            self.documents[resolved_pmid] = f"{existing}\n{text}".strip()
        result = {"pmid": resolved_pmid, "pmcid": pmcid, "found": True, "source": source, "text": text}
        return self._record("fetch_fulltext", {"pmid": pmid, "pmcid": pmcid}, result)

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        # A resumed validator may ask the same question again. Replay the
        # durable result instead of spending another external request or
        # consuming the item's search budget twice.
        for call in self.calls:
            if call.get("tool") == name and call.get("arguments") == arguments:
                return call.get("result", {})
        self._enforce_budget(name, arguments)
        if name == "search_pubmed":
            return self.search_pubmed(**arguments)
        if name == "search_europepmc":
            return self.search_europepmc(**arguments)
        if name == "fetch_abstract":
            return self.fetch_abstract(**arguments)
        if name == "fetch_fulltext":
            return self.fetch_fulltext(**arguments)
        raise ValueError(f"Unknown retrieval tool: {name}")


TOOL_DEFINITIONS = [
    {"type": "function", "name": "search_pubmed", "description": "Search PubMed and return PMIDs.", "strict": False, "parameters": {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 50}}}},
    {"type": "function", "name": "search_europepmc", "description": "Search Europe PMC for primary literature and open full text.", "strict": False, "parameters": {"type": "object", "additionalProperties": False, "required": ["query"], "properties": {"query": {"type": "string"}, "max_results": {"type": "integer", "minimum": 1, "maximum": 50}}}},
    {"type": "function", "name": "fetch_abstract", "description": "Fetch an abstract and authoritative PubMed metadata.", "strict": False, "parameters": {"type": "object", "additionalProperties": False, "required": ["pmid"], "properties": {"pmid": {"type": "string"}}}},
    {"type": "function", "name": "fetch_fulltext", "description": "Fetch open full text by PMID or PMCID from Europe PMC, with NCBI BioC fallback.", "strict": False, "parameters": {"type": "object", "additionalProperties": False, "required": [], "properties": {"pmid": {"type": "string"}, "pmcid": {"type": "string"}}}},
]
