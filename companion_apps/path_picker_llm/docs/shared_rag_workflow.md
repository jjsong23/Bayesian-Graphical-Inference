# Shared evidence library and whole-path review

The recommended **RAG path-first** workflow reviews one complete signaling path
at a time. It retrieves previously accepted literature evidence from a shared
local library before asking the path model what is still missing. Overlapping
paths can therefore reuse the same verified facts without treating an earlier
path's verdict as a fact about a different path.

RAG means retrieval-augmented generation: selected evidence is supplied to the
model with its request. The library does not train the model, and the model does
not acquire unrestricted access to the SQLite file.

## Start a run

1. Launch the application and open `http://127.0.0.1:8772/`.
2. Select the BGI archive or directory for the intended query. The prepared
   `Prkaca → Crtc2` pilot contains 2,000 paths; it is not the whole collection of
   all endpoint queries.
3. Choose **RAG path-first (recommended)** and select **Create free inventory**.
   Review the imported path, node, edge, and evidence counts. Inventory creation
   initializes the local shared library and imports eligible, already verified
   evidence from the selected seed run. It makes no model or search API calls
   and does not modify the seed run.
4. Check the shared-library and optional seed-run paths, model deployment names,
   limits, and pricing assumptions. **Inspect default library** reads its
   current summary without importing a run or launching research.
5. Enter the API key and explicitly launch the paid run. The key is held in
   process memory and is not saved with the run or shared library.

The free inventory's `rag_audit.json` records library and import information.
During research, `rag_reviews.jsonl` records each assessed path and its remaining
uncertainties. The saved round checkpoints retain the retrieval bundle, permitted
claim IDs, and prompt hash used for that assessment. Rejected response diagnostics
are written under `rejected_responses/` when validation fails.

Use a **fresh inventory** to switch an existing legacy run to RAG. Resuming an
older run preserves its original workflow; it does not silently reinterpret
completed work using a different algorithm. The original database remains
available for its own resume and audit.

## What happens to each path

```text
Imported path + complete available BGI evidence for its nodes and edges
                              |
            Retrieve accepted facts and source excerpts
                              |
          Sol reviews the whole path and identifies gaps
                              |
      New web research, if needed, proposes atomic factual claims
                              |
         Existing independent grounding and verification gate
                  /                           \
       Accepted facts                    Rejected/unresolved
       saved and reusable                kept for audit only
                  \                           /
          Bounded follow-up review, when needed
                              |
      Saved path assessment and explicit remaining uncertainties
                              |
       Diverse deep review → audited hypothesis abstracts
```

By default there are at most **two path rounds**: the main round and one
follow-up. This is not an unlimited agent loop. A request can also be constrained
by the output-token, hosted-tool, new-claim, investigation, and total API-attempt
limits. Reaching a limit is not evidence that the biology is absent or that all
questions have been answered.

The whole-path model is `gpt-6-sol` with medium reasoning by default. Independent
claim verification, final deep review, and synthesis remain separate checks.
Deployment fields are Azure/OpenAI deployment names: they must exist on the
configured resource and support the requested API/tools.

## Recommended settings

| Setting | Default | Meaning |
|---|---:|---|
| Workflow | `rag_path_first` | Shared retrieval and whole-path reasoning |
| Path deployment | `gpt-6-sol` | Whole-path review and gap-directed research |
| Path reasoning effort | `medium` | Requested reasoning level |
| Maximum path rounds | 2 | Main round plus at most one follow-up |
| Retrieved claim limit | 40 | Bounds the supplied accepted-fact bundle |
| Retrieval context character limit | 24,000 | Bounds the supplied retrieval text |
| New claims per path round | 12 | Bounds proposed new atomic claims |
| Investigations per path round | 8 | Bounds requested gap investigations |

Limits are ceilings, not targets or guarantees of a particular dollar cost.
Increasing them can increase paid calls and token usage. No incomplete response
automatically raises a limit or starts a paid retry.

## What the library contains

The default shared database is:

```text
runtime/shared_knowledge/evidence.sqlite3
```

It is separate from each run's `path_picker.sqlite3`. The per-run database still
holds imported BGI records, path reviews, API checkpoints, and the run's claim
ledger. Shared retrieval does not alter the BGI Bayes factors or replace them
with literature counts.

The library retains atomic claims, source URLs and metadata, verified excerpts
when available, biological context, origin/run provenance, and research-coverage
records. It is **not** a complete downloaded-paper corpus. A source URL or a
model's source summary alone is insufficient to make a claim accepted.

Only claims satisfying the existing acceptance checks can become reusable
facts: accepted status, retrieved source, verified quotation, independent
entailment and context checks, and no recorded retraction. Rejected or unresolved
claims remain distinguishable audit material; they are not retrieved as facts.
Failed research and missing papers are never converted into biological negative
evidence.

An optional seed run is read **read-only**, with idempotent imports and preserved
claim/source identifiers. The default seed, when present, is:

```text
runs/20261109_174322_71b2484f/path_picker.sqlite3
```

At implementation time that pilot held 39 accepted claims and 53 nonaccepted
claims. These are claim counts, not 39 verified edges. Imported records still
have to pass the library's admission checks; they do not receive a blanket
approval merely because they came from an older run. Its accepted facts can
save research in the new run, but its path-level verdicts and provisional prose
are not promoted into shared facts.

## Retrieval and context safeguards

Retrieval combines exact node/pair identity with local lexical and semantic
matching. The semantic component uses NumPy-based TF-IDF and truncated SVD
(latent semantic analysis), **not neural embeddings** or a paid embedding
service. If the optional numeric dependency is unavailable, retrieval has an
explicit lexical fallback. This is a compact, local evidence index, not a claim
that text similarity proves a mechanism.

Species, cell model, treatment, direction, interaction mechanism, and assay
context travel with claims. Evidence from another context must remain labeled
as such. For example, a binding result does not automatically establish a
directed phosphorylation reaction, and a response in another tissue does not
establish collecting-duct specificity. Finding a claim by an undirected pair
key does not reverse its biological direction.

A contextually relevant fact is not necessarily evidence for a particular
traversal. Support labels require an applicable claim about the actual step;
background facts about one endpoint cannot establish the edge. Ambiguous alias
or direction matches stay uncertain rather than being silently upgraded.

Retrieved claims are copied into the current run's accepted ledger only through
the guarded import path. Model outputs must reference permitted accepted claim
IDs. Unknown or unaccepted IDs cause validation failure rather than silently
becoming citations. An incompatibility with a local rejection must not be
silently overwritten by a cache hit.

Research-coverage records describe what has already been checked. They are not
evidence that an edge exists or does not exist. A contextual exclusion requires
the existing independent exclusion checks; it is not a global blacklist for a
protein across all paths or experiments.

## Checkpoints, cost, and limitations

Completed tasks and accepted claims are retained for explicit resume. A stopped
run is not automatically restarted. Invalid or incomplete model output cannot
create partially accepted facts. API usage is recorded where the provider
reports it, including unsuccessful or incomplete attempts.

Early paths retain the evidence snapshot used when they were assessed; later
discoveries do not silently rerank all completed paths. The independent finalist
audit retrieves current evidence. A claim accepted only after a path's last
permitted round is available to later paths and finalist audit, but cannot
retroactively support that path's already-saved assessment.

Local retrieval itself makes no paid model calls. Supplying retrieved evidence
still consumes input tokens, and new searches, independent verifications, path
reviews, and final synthesis still cost money. Reuse reduces repeated research;
it does not make 2,000 path assessments free or guarantee a speedup for paths
whose evidence does not overlap. The GUI's usage estimates are not hard budget
limits or cloud invoices.

Source and model checks reduce unsupported claims but cannot guarantee zero
hallucinations or resolve every biological ambiguity. Final abstracts should
distinguish published facts from proposed mechanisms, preserve weak links, and
remain reviewable against the saved sources before experimental decisions.
