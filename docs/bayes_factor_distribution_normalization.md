# Optional Bayes-factor distribution matching

## Purpose

Evidence streams do not naturally produce Bayes factors on equally dispersed
scales. In particular, STRING's broad combined score can yield a much wider BF
distribution than localization or curated-interaction streams. Even when that
is a faithful transformation of the source score, it can make one stream
numerically dominate the posterior.

V1 therefore provides optional, separate distribution-matching controls for
node and edge evidence. They are disabled by default.

## Transformation

For each active stream, after source-specific scoring and any configured
absence rule, the engine:

1. removes neutral factors (`BF = 1`) from the scale estimate;
2. computes `q = quantile_0.90(abs(ln(BF)))`;
3. chooses `a = ln(target BF) / q`;
4. bounds `a` to `[0.1, 10]`; and
5. applies `BF_matched = BF_raw ^ a` to every modeled hypothesis.

The default target is BF 2. A raw refuting magnitude is interpreted
symmetrically: BF 0.5 has the same absolute log magnitude as BF 2. If a stream
contains no non-neutral factors, its exponent remains 1.

The ordinary model weight is applied afterward:

`posterior odds = prior odds × product((BF_raw ^ a) ^ weight)`

Node and edge families are matched independently because they represent
different hypothesis spaces. Derived edge streams are included in edge-stage
matching. The reference population is all modeled candidates for node streams
and all unique undirected pairs in the selected graph for edge streams; neutral
pairs do not affect the fitted quantile.

## Invariants and safeguards

- `BF = 1` remains exactly neutral.
- A supporting BF remains above 1 and a refuting BF remains below 1.
- The transformation is monotone within each stream, so its within-stream rank
  is unchanged.
- Exponent bounds prevent an almost-neutral stream from being amplified
  without limit and prevent a broad stream from being flattened completely.
- The raw BF, transformed BF, fitted exponent, source q90, target, and clipping
  status are saved for audit.
- Distribution matching and positive-control calibration cannot be enabled in
  the same run. They optimize different scale conventions and combining them
  would make attribution unclear.

## Interpretation

This is a sensitivity analysis and numerical scale-harmonization option. It
does not prove that the streams are equally reliable, independent, or
calibrated to empirical truth. It also does not remove biological redundancy
between databases. Explicit weights remain available after matching to encode
deliberate reliability or dependence judgments.

When matching is enabled, a STRING weight of 0.25 still applies after the
STRING distribution is compressed to the common target. Set that weight to 1
only if equal post-matching influence is the intended sensitivity analysis;
the default 0.25 stabilization remains unchanged.
