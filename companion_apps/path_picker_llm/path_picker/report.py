"""Human-readable report with traceable pathway abstracts."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def coverage_panel(run_directory: Path) -> str:
    """Do not turn a shortlist into an implicit claim of exhaustive review."""
    inventory_path = run_directory / "inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8")) if inventory_path.is_file() else {}
    coverage_path = run_directory / "path_coverage.json"
    coverage = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.is_file() else {}
    counts = coverage.get("counts", {})
    metrics = {
        "Imported paths": counts.get("total_paths", inventory.get("paths", "not recorded")),
        "Directly screened": counts.get("directly_reviewed", "not recorded"),
        "Uncertain": counts.get("uncertain", "not recorded"),
        "Rule-excluded": counts.get("excluded_by_rule", "not recorded"),
        "Pending": counts.get("pending", "not recorded"),
    }
    rows = "".join(f"<tr><th>{esc(label)}</th><td>{esc(value)}</td></tr>" for label, value in metrics.items())
    warning = "This is a partial review; pending paths were not individually assessed." if counts.get("pending", 0) else "A completed screening pass is not exhaustive literature verification or proof that every path is biologically valid."
    links = " · ".join(f'<a href="{esc(name)}">{esc(name)}</a>' for name in ("inventory.json", "path_coverage.json", "exclusion_ledger.json", "api_usage.jsonl", "path_picker.sqlite3") if (run_directory / name).exists())
    return f'''<section class="audit"><h2>Scope and coverage</h2>
    <p>{esc(inventory.get("coverage_scope", "Imported BGI candidate paths and available evidence; not every possible network or the complete scored candidate matrix."))}</p>
    <table>{rows}</table><p>{esc(warning)}</p>
    <p>Counts overlap: uncertain paths were screened, and a screened path may subsequently be rule-excluded. Exclusion by a shared rule is not the same as individually reviewing that path. Absence of literature is not negative evidence.</p>
    <p>BGI per-stream evidence is retained in the run database where provided. Path-ranking scores are triage heuristics, not newly calibrated probabilities. The cited facts and marked inferences still require human review.</p><p>{links}</p></section>'''


def write_report(run_directory: Path, result: dict[str, Any], candidates: list[dict[str, Any]], claims: list[dict[str, Any]]) -> Path:
    path_map = {item["path_id"]: item for item in candidates}
    claim_map = {item["claim_id"]: item for item in claims}
    cards = []
    for index, card in enumerate(result.get("cards", []), start=1):
        candidate = path_map.get(card["path_id"], {})
        cited = [claim_map[identifier] for identifier in card.get("supporting_claim_ids", []) if identifier in claim_map]
        citations = "".join(
            f'<li id="{esc(item["claim_id"])}"><code>{esc(item["claim_id"])}</code> '
            f'{esc(item["subject"])} {esc(item["predicate"])} {esc(item["object"])}. '
            f'<a href="{esc(item["url"] if urlparse(str(item["url"])).scheme in {"http", "https"} else "#")}" target="_blank" rel="noreferrer">source</a></li>'
            for item in cited
        ) or "<li>No accepted literature claim was cited; inspect the inference markers.</li>"
        cards.append(f"""
        <article class="card">
          <div class="rank">{index:02d}</div>
          <div>
            <p class="eyebrow">{esc(candidate.get('query_id', ''))}</p>
            <h2>{esc(card['title'])}</h2>
            <p class="path">{esc(candidate.get('path_symbols', ''))}</p>
            <p class="abstract">{esc(card['abstract'])}</p>
            <div class="grid">
              <section><h3>Why selected</h3><p>{esc(card['selection_reason'])}</p></section>
              <section><h3>Weakest link</h3><p>{esc(card['weakest_step'])}</p></section>
              <section><h3>Experiment</h3><p>{esc(card['recommended_experiment'])}</p></section>
              <section><h3>Decision rule</h3><p><strong>Expected:</strong> {esc(card['predicted_result'])}</p><p><strong>Falsifies:</strong> {esc(card['falsifying_result'])}</p></section>
            </div>
            <details><summary>Accepted claims cited in this hypothesis</summary><ol>{citations}</ol></details>
          </div>
        </article>""")
    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Path Picker shortlist</title><style>
:root{{--ink:#132a25;--muted:#65736f;--paper:#f5f3ec;--card:#fff;--accent:#0d765a;--line:#d9dfda}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 system-ui,sans-serif}}
main{{max-width:1200px;margin:auto;padding:48px 24px 100px}}h1,h2{{font-family:Georgia,serif;line-height:1.08}}h1{{font-size:clamp(2.8rem,7vw,6rem);margin:.1em 0}}
.lede{{max-width:850px;font-size:1.18rem;color:var(--muted)}}.synthesis{{margin:34px 0 50px;padding:24px;border-left:5px solid var(--accent);background:#e6f1ed}}
.card{{display:grid;grid-template-columns:70px 1fr;gap:20px;background:var(--card);border:1px solid var(--line);border-radius:22px;padding:30px;margin:24px 0;box-shadow:0 12px 40px #173c3120}}
.rank{{font:700 1.35rem Georgia;color:var(--accent)}}.eyebrow{{text-transform:uppercase;letter-spacing:.12em;color:var(--accent);font-size:.75rem;font-weight:700}}
h2{{font-size:2rem;margin:.15em 0}}.path{{font-family:ui-monospace,monospace;background:#eef3f0;padding:10px;border-radius:8px;overflow:auto}}
.abstract{{font-family:Georgia,serif;font-size:1.22rem;line-height:1.65}}.grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}
.grid section{{border:1px solid var(--line);border-radius:12px;padding:15px}}h3{{font-size:.78rem;text-transform:uppercase;letter-spacing:.09em;color:var(--muted)}}
.audit{{padding:24px;background:#fff;border:1px solid var(--line);border-radius:16px;margin:25px 0}}.audit table{{border-collapse:collapse;width:100%;max-width:500px}}.audit th,.audit td{{text-align:left;border-bottom:1px solid var(--line);padding:8px}}
details{{margin-top:18px}}a{{color:var(--accent)}}code{{font-weight:700}}@media(max-width:720px){{.card{{grid-template-columns:1fr}}.grid{{grid-template-columns:1fr}}}}
</style></head><body><main>
<p class="eyebrow">Evidence-grounded hypothesis selection</p><h1>Path Picker shortlist</h1>
<p class="lede">Each factual sentence in a pathway abstract carries an accepted claim ID. <code>[INFERENCE]</code> marks the system's synthesis rather than an established fact. BGI probabilities remain computational evidence and are not presented as literature facts.</p>
{coverage_panel(run_directory)}
<section class="synthesis"><h2>Network-level synthesis</h2><p>{esc(result.get('network_synthesis',''))}</p></section>
{''.join(cards)}
</main></body></html>"""
    output = run_directory / "report.html"
    output.write_text(document, encoding="utf-8")
    return output

