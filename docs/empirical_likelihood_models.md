# Empirical class-conditional evidence likelihoods

Version 1 now treats an evidence stream's observed value as data to be
calibrated, rather than forcing all streams to have the same Bayes-factor
spread. For each stream the workflow estimates

\[
P(D_s\mid H_1, c),\qquad P(D_s\mid H_0, c)
\]

and applies their ratio to the prior odds. Here `s` is an evidence stream and
`c` is a biological hypothesis class.

## Biological routing

Edge streams are routed to physical proximity, physical binding, directed
phosphorylation, regulatory signaling, functional association, or
messenger-binding models. Consequently, localization and AlphaPulldown are
calibrated against physical-interaction references, whereas KinasePredictor is
calibrated against kinase/substrate references. Node streams are routed to
cellular presence, kinase activity, or transient phosphoregulation.

The reference sets are independent of the value being calibrated wherever the
data permit. Physical H1 references use reported BioGRID pairs, BioGRID
co-complex AlphaPulldown phase-1 pairs, and positive structural predictions;
physical H0 references use completed sub-neutral structural predictions and
the non-complex phase-2 background. Regulatory references use independently
retained KinasePredictor and OmniPath relationships plus their scorable
background. STRING uses the union of independent physical, structural, and
regulatory anchors. Node models use leave-one-stream-out support from
biologically compatible peer streams.

## Estimator

The datum is the source's pre-calibration `log(BF)`. A deterministic histogram
uses at most 12 discrete or pooled-quantile bins. Jeffreys smoothing (default
0.5 per bin) is applied independently to H1 and H0 counts. The ratio is clipped
to `[0.05, 20]` to prevent sparsely occupied bins from creating unbounded
posterior odds. A class needs the configured minimum number of both H1 and H0
references; otherwise that stream retains its original source likelihood ratio
and records why empirical replacement was not applied. This prevents a sparse
but genuinely observed evidence class from being silently erased.

Missing or out-of-scope observations remain exactly neutral. An absent record
is used as negative evidence for an individual hypothesis only when the user
enables the existing scope-aware negative-evidence option. This separates
background construction for calibration from the scientific claim that a
particular nondetection refutes an edge.

Every active stream saves its hypothesis class, anchor definition, reference
counts, bin boundaries, H1 and H0 counts, `P(Data|H1)`, `P(Data|H0)`, unclipped
and clipped likelihood ratios, and fallback state in `analysis_summary.json`.
The evidence inspector uses the saved model to reconstruct each posterior.

## Compatibility

The former 90th-percentile Bayes-factor distribution matcher is retained only
to read and reproduce historical configurations. Enabling empirical likelihood
calibration disables that matcher. Historical source factors remain in the run
audit as pre-calibration values.
