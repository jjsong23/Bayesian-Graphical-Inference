# Path Picker LLM

Path Picker is a standalone, resumable workbench for reducing a large set of
Bayesian Graphical Inference (BGI) paths to a small set of experimentally useful
hypotheses. It keeps two kinds of support separate:

1. **Computational support** — imported path, node, edge, Bayes-factor, and
   interpretation records from BGI. This covers the supplied returned paths,
   not every possible graph or the entire pre-search scored matrix.
2. **Literature support** — atomic claims whose source, quotation, context, and
   independent entailment check have been stored in a claim ledger.

An LLM cannot insert an accepted fact directly. A factual claim is accepted only
after its URL was returned by web search, the page was retrieved, the short
quotation was found on that page, retraction markers were absent, and an
independent verifier judged the quotation to entail the claim. Failed checks are
kept for audit but cannot support a final hypothesis.

## Run

```powershell
cd C:\Users\songjj\Documents\Codex\2026-07-21\un\path_picker_llm
python launch.py
```

The bundled Codex Python can also be used directly if `python` is not on PATH:

```powershell
& "C:\Users\songjj\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" launch.py
```

On another computer, install Python 3.10 or newer and run
`python -m pip install -r requirements.txt` in this directory. NumPy enables
local semantic retrieval; API access itself uses Python's standard library.
Use the portable bundle launcher to relocate saved run paths before resuming
copied checkpoints. No API key is bundled; enter your own key in the GUI.

Open <http://127.0.0.1:8772/>. Choose the actual run you want to inspect; no
particular archive is assumed. Run **Create free inventory** first; it does not call an API. A production run
requires an Azure/OpenAI API key entered in the browser. The key is held only in
the running process and is never written to SQLite, JSON, logs, or the report.

The recommended mode is now **RAG path-first**: whole-path Sol reasoning backed
by a shared, local library of independently accepted claims. It retrieves known
facts before researching gaps, then reuses newly verified evidence across
overlapping paths. The library uses exact matching plus local TF-IDF/LSA
retrieval; no paid embedding service is required. See the
[shared-RAG operating guide](docs/shared_rag_workflow.md) for setup, limits, and
the distinction between reusable facts and contextual path verdicts.

Create a fresh inventory to use the new mode. Resuming a saved legacy run keeps
its original workflow. An optional read-only seed import can reuse accepted
claims from the earlier pilot without modifying that run or accepting its
rejected claims. Free inventory initializes the library and performs this local
seed import; it makes no model or search calls. Neither opening the GUI nor
inspecting the library starts a paid run.

## Output

Each run writes to `runs/<run-id>/`:

- `path_picker.sqlite3` — resumable task state, compact BGI records, sources,
  claims, reviews, and event log.
- `inventory.json` — exact imported scope, provenance, and input limitations.
- `path_coverage.json` — every imported path and its direct-review/rule/pending status.
- `exclusion_ledger.json` — scoped, reversible rules with accepted evidence and impact.
- `rag_audit.json`, `rag_reviews.jsonl` — in RAG mode, shared-library status,
  bounded-round checkpoints, path assessments, and unresolved gaps.
- `api_usage.jsonl` — request attempts and actual response token/tool usage where reported.
- `shortlist.json` — structured, machine-readable hypothesis cards.
- `report.html` — human-readable report.
- `llm_handoff.json` — selected candidates, reviews, and accepted evidence for a
  downstream agent. Original per-stream BGI records are retained in SQLite;
  this compact handoff is not a substitute for the whole database.

Every final pathway has an abstract-like paragraph. Published factual clauses
carry claim IDs such as `[CLM-000123]`; explicit synthesis is marked
`[INFERENCE]`. The report validator rejects unknown, unaccepted, or missing claim
IDs before publication.

## Pipeline

1. Import all supplied returned paths and their available per-stream evidence.
2. Visit paths in target-balanced order and retrieve accepted facts with source,
   direction, and biological-context provenance from the shared evidence library.
3. Review each complete path with Sol, research missing facts on demand, and
   allow at most two path rounds by default. A path judgment is not a verified
   fact; only newly accepted atomic claims become reusable shared evidence.
4. Independently verify factual claims and any proposed exclusion's applicability.
   Exclusions are contextual and reversible; broad-impact rules are held, not applied.
5. Account for every path as directly screened, uncertain, excluded by a rule, or
   pending. A cap that prevents completion produces a partial/stopped run, not
   an assertion that all paths were reviewed. These counters can overlap.
6. Deep-review up to 50 diverse candidates by default; aim for 5–12 justified
   hypotheses, with fewer allowed when there are not enough eligible candidates.
7. Write evidence-grounded abstract-style paragraphs with claim references,
   explicit inference labels, weakest links, and falsifiable experiments.

Cancel requests are honored between API calls and item commits. Relaunching and
resuming a stopped run reuses completed tasks and accepted source records. Undo
an active exclusion only while the run is stopped; then resume to regenerate
affected output. Do not present an old shortlist as current after undoing a rule.

The recommended RAG mode uses `gpt-6-sol` for whole-path reasoning/research with
medium reasoning, and retains independent verification/deep review/synthesis.
Legacy adaptive path-first and evidence-first modes remain available; their
screening/research defaults use `gpt-6-luna`. These are editable Azure deployment
names, not a guarantee that any endpoint exposes those deployments or tools.
Request caps and token/tool metering limit runaway work, but are not hard dollar
budgets. No paid call starts without the GUI's launch action and supplied key.

## Live usage and estimated cost

The workbench's **Live usage and cost** panel reads each run's `api_usage.jsonl`
as requests finish. It separates input, cached input, cache-write, and output
tokens, and shows reported web-search usage and estimated USD charges. Reasoning
tokens are already included in output tokens, so they are not charged twice.
Retries are recorded individually; duplicate response IDs are counted only once.

These are estimates for the recorded usage, not a cloud invoice, a forecast of
the complete run, or a dollar spending limit. In-flight calls and responses that
omit usage can make the displayed subtotal incomplete. In particular, Azure
search action counts are not substituted for billable Bing transactions.
Missing usage is flagged rather than assumed free.

Expand the pricing settings to enter your actual deployment rates before
launching. Defaults are public OpenAI token-rate references, not a guarantee of
Azure contract prices. The Azure web-search rate is an explicitly unverified
reference assumption. Unknown deployment prices require an explicit rate or
alias. Rate settings are saved with the run; API keys are not.

Token accounting follows the official [Responses usage and spending example](https://developers.openai.com/cookbook/articles/per_run_spending_controller_responses_api)
and [reasoning-token accounting](https://developers.openai.com/api/docs/guides/reasoning).
Reference token rates were checked on 2026-11-09 against the
[Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) and
[Sol](https://developers.openai.com/api/docs/models/gpt-6-sol) model pages.

## One-query pilot selection

`inputs/prkaca_to_crtc2_source_native/agent_graph.json.gz` is the prepared pilot:
2,000 distinct **Prkaca → Crtc2** source-native paths, 144 nodes, and 726 edges.
Its 870 complete evidence ledgers contain 12,082 per-stream evidence rows.
This is the full returned-path union for that query, not the small display graph.
`selection.json` records the original archive/member, hashes, and exact scope.
Nothing in the original archive or older inventories was removed or rescored.

For other selections, `python -m path_picker.subset --help` describes the
offline exporter. It requires an exact session and query, preserves the
scientific records, checks complete evidence coverage, and refuses to overwrite
an existing selection. Selecting a query and creating its inventory do not
launch paid curation.

See [the shared-RAG operating guide](docs/shared_rag_workflow.md) or the
[legacy path-first design and operating guide](docs/path_first_workflow.md).
No method can guarantee zero hallucinations: the system preserves uncertainty,
requires grounding checks, and provides the original evidence for human review.

## Recovering an incomplete response

An HTTP success can still carry an incomplete model response. The output-token
ceiling includes reasoning and formatting tokens as well as the visible JSON;
there may be too little space left to finish the evidence record. The client
records available usage before rejecting such a response. Partial claims are
never accepted, even if a partial message happens to parse as JSON.

New defaults use a 32,000-token output ceiling, medium research reasoning,
one edge per research request, and two nodes per research request. Other roles'
reasoning settings are unchanged. This is a ceiling, not a target or a fixed
charge. Smaller research batches improve checkpoint granularity but may require
more calls. Monitor the live usage panel.

For an older stopped run, select **Apply output-limit recovery settings**. It
changes only those four settings in the form; nothing is sent to the API until
you explicitly launch/resume. Completed initial screens and completed research
remain reusable. The incomplete task is retried with the new settings. Neither
an incomplete response nor an unknown stop reason triggers a hidden paid retry
or automatic increase of the configured limits.

Apply the recovery preset **after** selecting the saved run, and then resume
without reloading the page. Reloading or reselecting restores the saved settings;
form edits are persisted on launch/resume. A confirmed research output-limit
failure with unchanged settings now shows a warning both beside the recovery
button and in the paid-launch confirmation. That confirmation displays the exact
token ceiling, research reasoning effort, and batch sizes to be submitted.

New usage entries record the provider's incomplete reason when available and
the request's output/tool ceilings and reasoning effort. Historical entries
without `incomplete_details` cannot establish the exact cause retrospectively.
See the official [reasoning and output-limit guidance](https://developers.openai.com/api/docs/guides/reasoning).

