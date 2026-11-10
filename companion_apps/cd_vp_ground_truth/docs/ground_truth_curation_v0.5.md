# Collecting-duct vasopressin ground-truth curation

Protocol v0.5 replaces recursive node expansion with a bounded, repeated pair
search. It remains an independent literature-curation application and does not
modify the Bayesian scores in V1 or V2.

## Candidate discovery

Each GPT-6 Luna call is a generic search bot. It must use hosted `web_search`
and returns complete directed source→target relationships rather than a list of
nodes to expand. Calls use rotating mechanistic search lenses such as receptor
signaling, AQP2 phosphorylation, trafficking, phosphatases, cytoskeleton,
calcium, MAPK, AKT/mTOR, receptor desensitization, and negative feedback.

The default stopping policy is:

- 24 independent search calls per round;
- at least 3 rounds (72 discovery calls);
- at most 10 rounds (240 discovery calls);
- after the minimum, stop following 2 consecutive rounds that add fewer than
  10 previously unseen unordered pairs;
- rank pairs by independent rediscovery, stated confidence, collecting-duct
  context, and vasopressin context; retain at most 500 pairs by default.

This prevents the combinatorial snowball produced by asking for partners of
every newly discovered node. Every call has its own atomic checkpoint, so a
failed or cancelled run can resume without repeating successful paid calls.

## Independent validation

Pair proposals are discovery leads, not evidence. GPT-6 Sol independently
validates every retained endpoint and unordered pair using the pipeline's
PubMed/Europe PMC tools. Every admitted item must be primary experimental
evidence with a verbatim quote matched at `>=0.95` against retrieved text.
Reviews, database assertions, predictions, structures, colocalization,
fractionation, and omics associations may guide retrieval but do not establish
causality. Missing evidence remains `UNLABELED`, never negative.

The curation target is now explicitly transient signaling. Evidence mediated
only through transcription, mRNA, promoter activity, gene-expression change,
or long-term abundance regulation is excluded deterministically. Acute
phosphorylation, activity, binding, complex formation, trafficking,
localization, and other post-translational relationships remain eligible.

An edge retains every independently supported relationship class in
`supported_labels` even though `best_label` still provides a single ranking
label. This matters for multifunctional pairs: for example, a pair can be both
`CAUSAL` and `PHYSICAL` without the stronger causal label hiding the direct
binding evidence. `physical_interaction_supported` and `physical_evidence`
provide dedicated machine-readable fields.

## Run from the GUI

Run `python launch.py` and open `http://127.0.0.1:8770/`. Use **Preview without
API calls** to inspect the minimum and maximum discovery-call counts. Then paste
the Azure API key and launch the paid run. The key is held only in memory.

The defaults use 4 concurrent Luna calls and 4 concurrent Sol sessions.
Parallelism changes elapsed time, not total paid calls. Azure throttling and
temporary failures use a shared cooldown, server-provided `Retry-After` or
rate-limit-reset hints when available, bounded exponential backoff with jitter,
and staggered worker restarts. A stopped run resumes from its atomic call
checkpoints; successful paid calls are not repeated.

## Command line

```powershell
$env:AZURE_OPENAI_API_KEY = "paste-key-here"
python code/ground_truth/cli.py `
  --config configs/ground_truth_v0.5.example.json `
  --run-dir results/ground_truth/cd_vp_v0.5_20261020
```

The generator integration follows the official Responses API web-search tool
pattern: the request includes a `web_search` tool and requires its use. Search
call records and returned URL citations are saved alongside each checkpoint.
