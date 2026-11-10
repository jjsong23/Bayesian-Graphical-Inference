# Path-first hypothesis selection

## Goal and boundaries

Turn a user-selected BGI candidate-path collection into approximately 5–12
mechanistically interesting, experimentally testable hypotheses. The endpoint
is an abstract-like paragraph for each pathway: what is proposed, which steps
are supported by published facts, what remains an inference, and why the
pathway is worth testing. Do not invent extra hypotheses to fill a quota.

The imported path collection is the search space for this task. A 2,000-path
session and a multi-session 152,000-path archive are different workloads; neither
is a universal or typical size. The inventory displays what was actually loaded.
The union of evidence on those paths is not the entire pre-search BGI matrix.

Supported inputs are an `agent_graph.json.gz`, an archive/directory containing
agent graphs, or a legacy session directory/HTML with adjacent
`ranked_paths.tsv` and `all_returned_path_evidence_ledgers.json.gz`. A standalone
HTML is not assumed to contain all hidden paths. Missing evidence remains
missing; it must not be silently replaced with neutral or negative facts.

## Workflow

1. **Inventory, without an API.** Import paths and available node/edge evidence,
   preserving session context and per-stream BFs. Check sources, targets, path
   counts, and evidence availability before choosing a paid workload.
2. **Target-balanced path screening.** Consider one path at a time within
   rounds, alternating across endpoint/query groups. Evaluate the full route,
   not a bag of high-scoring edges. Inspect BGI evidence and identify uncertain
   mechanisms. All paths are tracked; the final deep-review cap does not limit
   the initial screening population.
3. **Targeted, cached research.** Search for a small number of unresolved node
   or edge questions as they become relevant. Reuse research for repeated
   components across paths. Keep facts, BGI support, and model inference apart.
4. **Independent verification.** Proposed factual claims require a search source,
   retrieved text, a matching quotation, and a separate entailment/context check.
   Retraction markers and unsuccessful grounding checks prevent acceptance.
5. **Conservative exclusion.** An adverse screening opinion is insufficient to
   erase other paths. A rule needs independently verified evidence and scoped
   applicability. Context, orientation, and mechanism matter. Broad-impact rules
   are held for review, not automatically activated. There is no automatic
   human-approval action for held rules in this version.
6. **Deep review and diversity.** Rank surviving candidates with explicit
   heuristic scores and deep-review a diverse final subset (default 50).
   Those scores are not posterior probabilities. Retain plausible alternatives
   and weaker-link uncertainty instead of selecting solely by BGI rank.
7. **Cited pathway abstracts.** Aim for 5–12 nonredundant hypotheses. Include
   reasoning, accepted claim IDs, explicit `[INFERENCE]` passages, weakest links,
   and falsifiable experiments. Export coverage and exclusion audits alongside
   the report. A separate narrative audit checks sentence-level support and
   grounding of the other card fields before final output is published. This
   is still a model-based check, not a guarantee that every conclusion is true.

## What must not cause an automatic global ban

- No paper found, no collecting-duct-specific paper, or a missing BF record.
- Low BGI support alone.
- A structural/trafficking/localization protein label without assessing its role
  in the particular route. A terminal effector need not relay signal onward.
- A positive interaction in the opposite direction; that alone does not prove
  the requested direction impossible.
- A context-specific result transferred to all cell types, treatments, or roles.

Scaffolding, localization, trafficking, translocation, cytoskeletal changes, and
assembly can be mechanistic signaling steps without being phosphorylation
reactions. Novel but plausible links should remain uncertain hypotheses, not be
misrepresented as established facts or dismissed merely for missing literature.

## Coverage and reversibility

The GUI exposes imported-path count, directly reviewed count, uncertain count,
rule-excluded count, pending count, and researched-item count. These are not
mutually exclusive: uncertain is a reviewed subset, and a previously reviewed
path can later be excluded. `path_coverage.json` gives per-path details and rule
IDs, avoiding claims of direct review for paths merely covered by a rule.

Stop safely before using **Undo rule**. Revocation preserves the original
reviews and other active rules, restores eligibility as appropriate, and marks
the old shortlist stale. Resume to reassess paths and regenerate the report.
The prior BGI scores are never rewritten by an exclusion rule.

The current biological context defaults to mouse kidney collecting-duct cells
and acute vasopressin/dDAVP–PKA signaling. Context-specific rules cannot simply
be reused for a different experiment; a changed context requires a separate
run so that cached decisions are not silently transferred.

## Operating instructions

1. Launch `python launch.py` from this standalone application's directory.
   Do not restart an existing process that is running another paid job.
2. Select **New inventory**, paste the local input path, and select
   **Create free inventory**. Confirm the scope in the live audit.
3. Enter an Azure/OpenAI endpoint, a key, and deployment names. Screening and
   targeted research default to `gpt-6-luna`; verification, deeper reasoning,
   and synthesis default to `gpt-6-sol`. Deployments must actually exist on that
   endpoint and support the requested structured output/search capabilities.
4. Inspect limits. Defaults: 50 final deep reviews, 10 paths per screening round,
   2,000 distinct researched items, 10,000 API attempts, 8,000 output tokens per
   request, and 6 hosted tool calls per request. Automatic rule impact must be
   no greater than both 50 paths and 10% of the imported set.
5. Select **Launch/resume paid curation** and confirm the scope. The key remains
   in memory only. Re-enter it after restarting the server.
6. Use **Cancel safely** to stop between requests/checkpoints. Select a saved
   run to resume that same input. Use a new inventory for a different input.

A research/request cap stops the run with an explicit error and saved partial
coverage. Raising the cap is a user decision. It is not legitimate to mark a
capped review complete merely because a shortlist exists.

## Outputs and cost accounting

- `report.html`: readable pathway abstracts and scope/coverage section.
- `shortlist.json`: structured hypothesis cards.
- `path_coverage.json`: every imported path, screening state, and applied rules.
- `exclusion_ledger.json`: active, held, and revoked scoped rules and supporting claims.
- `path_picker.sqlite3`: resumable original evidence, tasks, claims, and audit data.
- `llm_handoff.json`: shortlisted candidates/reviews and accepted evidence;
  use the database as well when a downstream agent needs the entire imported set.
- `api_usage.jsonl`: request attempts and reported usage by model and stage.

Cost depends on the actual number of screened paths, targeted research items,
accepted/rejected claims needing verification, output/reasoning tokens, and
billable web-search requests. Caching shared evidence reduces repeated research;
it does not make initial screening free. Reasoning tokens are already included
in billed output tokens and must not be added twice. Count retries and search
charges separately when relevant. Cached input rates should only be used when
the service reports eligible cached usage.

Use the [generated path-first cost report](path_first_cost_estimate.md) for scenarios and price assumptions;
the local usage log is the measurement for a real pilot, not those projections.
The API-attempt/token/tool limits are safety controls, not a guaranteed total
dollar ceiling. Review Azure billing and institutional terms before large runs.

Grounding checks reduce hallucination risk but cannot guarantee zero errors.
Human source checking is required before experimental decisions.
