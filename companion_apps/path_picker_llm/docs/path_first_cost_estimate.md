# Path-first cost estimate

Offline estimate — no paid API calls were made.

Measured **2,000 saved paths**, **164 unique nodes**, **512 unique edges** from `20261104_pka_functions_tuned_known_edges_retry2`.
The full scored graph has 2,902 nodes and 3,751,593 edges, but is not the review scope. This is not the separate 418-node/3,296-edge archive.

Initial all-path screening payload: **36,380,359 proxy input tokens**, median **18,966/path**. Every stream's compact evidence is retained; histogram arrays are omitted.

| Final deep reviews | Workload assumption | Luna screen/search + Sol judgment | All Sol | All Luna (illustrative) |
|---:|---|---:|---:|---:|
| 50 | lean | $12.32 | $118.22 | $6.95 |
| 50 | planning | $31.23 | $184.21 | $14.64 |
| 50 | heavy | $129.23 | $488.38 | $60.38 |
| 200 | lean | $22.57 | $128.68 | $7.55 |
| 200 | planning | $43.63 | $196.61 | $15.26 |
| 200 | heavy | $145.98 | $505.13 | $61.22 |

All figures include the assumed Azure web-search transaction charges. Global Standard reference rates; regional/contract rates may differ.

## What is assumed, not measured

- Offline planning estimate, NOT a measured paid pilot, quote, or strict upper bound.
- All saved paths get an initial screen: no assumed savings from excluding unevaluated paths. Up to one additional adaptive screen/path is possible; scenarios use 10/25/100%. These are assumptions, not measured decisions. Deep finalists see fresh research without an extra screening call.
- Research sets are cost proxies chosen by path frequency, augmented to cover every node/edge of cost-proxy finalists; actual adaptive requests may differ. Finalists are balanced by query then rank solely to size prompts.
- Every searched constituent is cached globally; search is not repeated per path. Typical batch occupancy is explicitly smaller than maximum node6/edge3 to allow on-demand requests.
- Claim-verification assumptions cover only claims with retrievable sources and literal quotes. Exclusion auditing is a separate independent call, counted here.
- Thirteen independent final narrative factual-grounding audits are included (up to12 abstracts plus the network synthesis). Research question length uses a fixed generic placeholder; actual generated questions vary.
- Output token allowances INCLUDE hidden reasoning and visible JSON/prose. Actual high-reasoning use can exceed these assumptions.
- Web retrieved/context tokens and paid Bing transactions are separate charges. A model call is not necessarily one transaction. Actual Azure usage should use tool_usage.web_search.num_requests.
- No input cache savings, retries, taxes, negotiated discounts, network hosting, or human review time assumed. No claim of zero hallucination risk.
- Pricing uses short-context rates. All scenario single-request estimates are below272k input; cumulative run tokens do not trigger per-request long-context pricing. Unusually long live claims may change that.
- Missing saved node ledgers: Clip1, Nsfl1c. They remain explicit unscored placeholders. No invented Bayes factors.

## Scenario details

| Assumption | Lean | Planning | Heavy |
|---|---:|---:|---:|
| Unique constituents researched (minimum; finalists may add more) | 0.25 | 0.6 | 1.0 |
| Additional screening fraction | 0.1 | 0.25 | 1.0 |
| Output/reasoning tokens per screen | 1200 | 2000 | 3500 |
| Claims independently verified per researched item | 1 | 1.5 | 3 |
| Search transactions per research call | 1 | 2 | 4 |
| Research context tokens per call | 3000 | 6000 | 12000 |

Detailed per-stage input/output counts, calls, assumptions and hashes are in [the JSON report](path_first_cost_estimate.json).

## Pricing sources

- [developers.openai.com](https://developers.openai.com/api/docs/models/gpt-6-sol)
- [developers.openai.com](https://developers.openai.com/api/docs/models/gpt-6-luna)
- [azure.microsoft.com](https://azure.microsoft.com/en-us/blog/gpt-6-astra-sol-and-luna-for-production-agents-in-microsoft-foundry/)
- [www.microsoft.com](https://www.microsoft.com/en-us/bing/apis)
- [learn.microsoft.com](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/web-search)

## Reproduce locally

Requires `tiktoken` and the adjacent V1 evidence exports, not just a standalone HTML. From the Path Picker directory:

```text
python estimate_path_first_cost.py PATH_TO_SAVED_SESSION --output docs/path_first_cost_estimate.json --report docs/path_first_cost_estimate.md --deep-reviews 50 200
```

If tiktoken is in a separate directory, pass `--tokenizer-path PATH_TO_DEPENDENCIES`. The estimate makes no model API requests. Change `--web-price-per-thousand` to your actual provider price.
