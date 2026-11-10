# Collecting-duct vasopressin ground-truth curation

Protocol v0.3 is an independent, literature-grounded curation pipeline. It is
not a Bayesian evidence stream and does not modify the V1 or V2 applications.

## Evidence design

- Stage 1 proposes candidate nodes and relationships; proposal frequency is
  metadata, never a label.
- Stage 2 receives blind node identities or randomized unordered pairs and
  searches for primary experiments.
- There is no dataset/PMID evaluation firewall in v0.3. Papers are not removed
  merely because they contributed to another project dataset.
- Every admitted item still requires a verbatim quote matched at `>=0.95`
  against text fetched and logged during that validator session.
- PubMed supplies citation metadata; model-supplied citation metadata is
  ignored.
- Database assertions, reviews, predictions, structures, colocalization,
  fractionation, and omics associations may lead the search toward a paper but
  do not themselves establish causality. The validator must find a primary
  perturbation, biochemical, site-mutant, or physical experiment and classify
  it into a supported experimental method.
- Missing evidence is `UNLABELED`, never negative.
- Site evidence is reduced to protein level unless the reported residue can be
  checked against a retrieved species sequence.
- Only causal/direct, vasopressin-responsive, ESTABLISHED or SUPPORTED edges
  enter `edges.jsonl`; everything else remains auditable.

## Azure setup and cost preview

Run `python launch.py` and open the printed URL. The browser provides a free
dry run, a session-only API-key field, live progress/cancellation, and direct
downloads.

Confirm the endpoint and deployments in
`configs/ground_truth_v0.3.example.json`. The API key is never saved:

```powershell
$env:AZURE_OPENAI_API_KEY = "paste-key-here"
python code/ground_truth/cli.py `
  --config configs/ground_truth_v0.3.example.json `
  --run-dir results/ground_truth/dry_run_v0.3_20261002 `
  --dry-run
```

At the defaults, each node-generation round starts with 266 calls. Every
candidate node adds 6 edge-generation calls, and validation uses one
tool-using agent per unique node and unordered edge. Snowballing can make a
full run expensive, so use the free preview and an Azure budget guard.

## Full run

```powershell
python code/ground_truth/cli.py `
  --config configs/ground_truth_v0.3.example.json `
  --run-dir results/ground_truth/cd_vp_v0.3_20261002
```

Candidate and raw-validation files are checkpoints. Outputs are `edges.jsonl`,
`edges.tsv`, `audit.tsv`, `nodes.jsonl`, `qa_metrics.json`, and
`run_manifest.json`. Retrieval logs and fetched text are retained under
`validation/`.

## Interruption recovery

The pipeline writes every successful node- and edge-generation response under
`generation/` before starting the next paid request. Small
`node_generation_checkpoint.json` and `edge_generation_checkpoint.json` files
summarize progress, while completed validation items retain their raw result
and retrieval log. Exact repeated literature-tool calls replay the saved
result instead of querying the external service again.

The Responses client retries connection resets, timeouts, HTTP 408/409/429,
and temporary 5xx responses with bounded exponential backoff and honors Azure
retry headers. Authentication and deployment errors are not retried.

When the GUI recovers a stopped production run, enter the API key and choose
**Retry from checkpoint**. The same run directory and reviewed configuration
are reused; the API key is again kept only in memory for the resumed process.
