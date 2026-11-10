# Diverse signaling-graph exploration

This workflow constructs a deliberately broad bank of typed signaling graphs. It is separate from the app's small candidate set and from any posterior sampler.

## Scientific contract

- V1 `gui_posterior` values and integrated edge probabilities are preserved and mapped into `[epsilon, 1-epsilon]` as **soft structural preferences**. They are not relabeled as calibrated graph-participation probabilities.
- The finite feasible universe combines required genes with low-, middle-, and high-score measured proteins and edges. The manifest reports what was excluded.
- Every edge assertion records existence, direction (`A→B`, `B→A`, reciprocal, or unknown), sign, and mechanism. The source registry does not establish these attributes, so they are explored as alternatives.
- Cycles are retained. Reciprocal arrows are not latent-confounding edges.
- All graphs retain the same observed measurement registry. Excluding a protein from a functional graph does not delete its measurement from the dataset.
- Bank inclusion frequencies are exploration coverage, not posterior probabilities.

## Structural preference

The implementation uses the requested Bernoulli log-preference form over the declared feasible node and interaction universe, plus explicit direction/sign/mechanism preferences and a configurable size penalty. Endpoint consistency is enforced before scoring.

## Dynamic stage

Only the 24 Kirby–Leo columns enter dynamic fitting. Each time point has three metadata-matched vehicle/dDAVP blocks. Treatment-minus-control responses are formed within block; labels at different times are not treated as longitudinal trajectories.

The fitted model is a small signed linear continuous-time surrogate. Gradient matching supplies one optimizer start and a zero-response model supplies another; the final parameters optimize the observed response trajectory with signed ridge regularization. Each target receives a decay term and at most one graph-selected parent by default. Unequal times (1, 2, 5, and 15 minutes) are used directly. The observation likelihood is on the existing standardized scale. Missing values remain missing.

The model is intentionally smaller than a full biochemical kinetic model because four sampling times cannot identify unrestricted topology, signs, hidden states, kinetics, and observation parameters. The reported AIC-like optimized-fit quantity is not a marginal likelihood.

At least 25% of the fitting budget is reserved for low-confidence, feedback-rich, or otherwise deliberately unusual graphs.

## Run

```text
python -m static_causal.exploration_cli --config configs/exploration_10000.json --output runs/exploration_10000
```

Primary outputs are `exploration_report.html`, `graph_bank.jsonl.gz`, `graph_rankings.tsv`, coverage and structural-sensitivity tables, dynamic-fit parameters/predictions, resolved configuration, and provenance manifests.

The implementation uses the biochemical continuous-time and gradient-matching perspective of CheMA while adopting CellNOptR's discipline of retaining families of plausible models rather than claiming one uniquely recovered network.

## Full-registry mode

`configs/full_space_10000.json` removes the bounded 48-node/180-edge universe. All V1 registry nodes and edges become eligible.

Because the source confidence values are not calibrated graph-inclusion probabilities, full-registry mode maps their ranks monotonically to configurable sparse mean participation rates. Original source values remain in the provenance and coverage files. The structural absent-state contribution across the entire registry is computed once; each graph is then scored by adding log-odds deltas only for selected nodes and edges.

```text
python -m static_causal.full_space_cli --config configs/full_space_10000.json --output runs/full_space_10000
```

The ordinary full-space sampler combines global-uniform, confidence-stratified, bottom-quartile, uniform-node, prior-weighted, and feedback-oriented frontier proposals. Thus every registry item is eligible, but a finite run does not guarantee exposure of every edge.

For guaranteed exposure, divide the registry into deterministic shards. For example, shard 7 of 64 is:

```text
python -m static_causal.full_space_cli --config configs/full_space_10000.json --output runs/coverage_shard_07 --coverage-only --coverage-shard-index 7 --coverage-shard-count 64
```

Running indices 0–63 gives every source edge one forced appearance when `passes=1`. With the current 856,709-edge registry and 16 forced seed edges per graph, this requires about 53,600 graphs total, approximately 837 per shard. These forced appearances measure coverage; they are not posterior samples or evidence that the bundled edges belong together biologically.

Coverage-only runs write sparse ledgers containing only exposed edges. After all shards finish, merge them with:

```text
python -m static_causal.merge_full_space_shards --inputs "runs/coverage_shard_*" --output runs/coverage_merged
```

The merged manifest reports whether every source edge was exposed at least once. Graph banks remain separate shard artifacts; the merger combines rankings and exposure counts without pretending the forced graphs form a posterior chain.
