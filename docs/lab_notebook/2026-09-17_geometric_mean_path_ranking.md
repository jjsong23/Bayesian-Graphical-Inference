# 2026-09-17 — Geometric-mean path ranking and 50-path exports

## Rationale

Version 1 previously ranked a path by the product of its edge posterior
probabilities. Because every additional factor below one reduces that product,
the primary rank implicitly favored shorter routes. At the investigator's
request, the primary score is now the geometric mean edge probability:

\[
S_{GM}(r)=\left(\prod_{e\in r}p_e\right)^{1/h}
=\exp\left[-\frac{1}{h}\sum_{e\in r}-\log(p_e)\right],
\]

where `h` is the path's number of edges. This score measures typical edge
strength and removes automatic path-length scaling. It is a ranking statistic,
not a calibrated probability that a complete signaling route is true.

## Implementation

Within a fixed hop count, ranking by product and geometric mean is identical.
The engine obtains up to K exact-hop simple paths for each length from one
through the configured maximum, then merges all length-specific candidates by
mean negative-log edge cost. Exact-hop search uses an admissible relaxed
dynamic-programming bound and a simple-path A*/Yen procedure. A path below rank
K within its own length cannot enter the global top K, so the merged result is
exact rather than a beam-search approximation.

`ranked_paths.tsv` now records `geometric_mean_edge_probability` and the alias
`primary_path_score`, plus `mean_negative_log_edge_probability`. Existing
`path_probability_product` and `negative_log_path_probability` columns remain
for audit and backward interpretation.

The live merged graph now selects the top 50 available paths on first render.
The standalone complete-session HTML inherits that 50-path view and
materializes evidence ledgers for all inspectable nodes and edges in it. The
automatic interpretation safety ceiling was raised from 250 to 1,000 so a
50-path union is not prematurely truncated.

## Validation

- Seven path-finding tests passed, including comparison with exhaustive
  enumeration and a case where geometric mean correctly ranks a stronger
  two-edge path above a weaker direct edge even though its raw product is
  smaller.
- A real-data `Prkaca` to `Aqp2` run requested and returned 100 paths across a
  892-node extended graph. Every reconstruction and ordering validation passed.
- The highest-ranked paths in that validation all used six hops and had
  geometric means near 0.9995. This is expected under the requested criterion:
  a dense graph containing many near-one edges allows longer routes to achieve
  stronger average edge support. It is an important interpretive consequence
  of removing the product's length penalty.

Validation output:
`results/path_finding/ranked_paths/geometric_mean_validation_20260917/`.

## Existing profile caveat observed during validation

The current uncommitted Version 1 default profile specifies `Aqp2` as the
start and `Prkaca` as the target, while the standalone path engine can
auto-extend only its target argument and Aqp2 is outside the seed matrix. The
ranking validation therefore used the historically supported orientation
`Prkaca` to `Aqp2`. This pre-existing profile choice was not silently reversed
as part of the ranking change.
