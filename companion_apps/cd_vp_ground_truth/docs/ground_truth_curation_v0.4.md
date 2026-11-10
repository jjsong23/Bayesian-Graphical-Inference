# Collecting-duct vasopressin ground-truth curation

Protocol v0.4 is an independent, literature-grounded curation pipeline. It is
not a Bayesian evidence stream and does not modify the V1 or V2 applications.

## What changed in v0.4

- GPT-6 Luna candidate generation now has Azure/OpenAI hosted `web_search`.
- Search is required for every node, negative-control, and edge generation
  request; it is not left to the model's discretion.
- Search context is configurable as low, medium, or high and defaults to low.
- Each successful generation checkpoint stores the response ID, model, usage,
  web-search call record, and returned URL citations beside the structured
  candidate result.
- GPT-6 Sol remains an independent validator. Its PubMed/Europe PMC retrieval,
  exact-quote checking, and deterministic evidence gates are unchanged.
- Independent calls execute in bounded worker pools. Defaults are 12 concurrent
  Luna generation calls and 4 concurrent Sol validator sessions; the GUI allows
  1–32 and 1–16 respectively.
- Every worker writes its own atomic checkpoint. Completed calls survive a
  sibling failure, cancellation, server restart, or later retry.
- Azure/OpenAI `Retry-After` and `x-ms-retry-after-ms` cooldowns are honored
  across the worker pool. Other transient failures use exponential backoff with
  jitter.
- v0.4 writes to a new run directory and never reuses v0.3 candidates, because
  those candidates were generated without hosted web search.

## Evidence design

- Stage 1 searches the live web to propose candidate nodes and relationships;
  proposal frequency is metadata, never a label.
- Stage 2 receives blind node identities or randomized unordered pairs and
  searches primary literature through the pipeline's auditable retrieval tools.
- Papers are not removed merely because they contributed to another project
  dataset.
- Every admitted item requires a verbatim quote matched at `>=0.95` against
  text fetched and logged during that validator session.
- Database assertions, reviews, predictions, structures, colocalization,
  fractionation, and omics associations may lead the search toward a paper but
  do not themselves establish causality.
- Missing evidence is `UNLABELED`, never negative.
- Only causal/direct, vasopressin-responsive, ESTABLISHED or SUPPORTED edges
  enter `edges.jsonl`; everything else remains auditable.

## Run from the GUI

Run `python launch.py` and open `http://127.0.0.1:8770/`. Confirm that the
generator is `gpt-6-luna`, the validator is `gpt-6-sol`, and choose the web
search context size. The parallel-call defaults are intentionally aggressive;
reduce them if the Azure resource or the literature services repeatedly throttle.
Preview first, then enter the Azure key and launch.

The key is held only in memory. A failed v0.4 run can be resumed from its
saved checkpoints; successful web-assisted generation calls are not repeated.

## Command line

```powershell
$env:AZURE_OPENAI_API_KEY = "paste-key-here"
python code/ground_truth/cli.py `
  --config configs/ground_truth_v0.4.example.json `
  --run-dir results/ground_truth/cd_vp_v0.4_20261002
```

At the defaults, each node-generation round starts with 266 generator calls.
Every candidate node adds 6 edge-generation calls, and every one of those
generator calls makes at least one hosted web search. Validation then uses one
Sol tool-using agent per unique node and unordered edge. Use the free preview
and an Azure budget guard before launching.

Parallelism changes elapsed time, not the number of paid model or web-search
calls. Multiple deployment objects are not required: the worker pool sends
concurrent requests to the configured Luna and Sol deployments.
