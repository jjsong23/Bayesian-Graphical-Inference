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
phosphorylation, acute regulatory signaling, or messenger-binding models.
Localization and AlphaPulldown are calibrated against physical-interaction
references; KinasePredictor is calibrated against kinase/substrate references;
and both OmniPath and STRING are calibrated against acute regulatory signaling
rather than generic same-process association. Node streams remain routed to
cellular presence, kinase activity, or transient phosphoregulation.

The versioned edge reference catalog was built from the four workbooks in
`gui/calibration/positive_cases_20261028/source/Positive Cases`. Its H1/H0
policy is intentionally profile-specific:

| Profile | H1 | H0 | Context policy |
|---|---:|---:|---|
| Physical proximity | 78 | 390 | collecting-duct-specific positives; endpoint-matched unlabeled background |
| Physical binding | 78 | 390 | collecting-duct-specific positives; endpoint-matched unlabeled background |
| Directed phosphorylation | 88 | 22 | direct biochemical kinase/substrate evidence across model contexts; explicit tested nulls |
| Acute regulatory signaling | 48 | 240 | collecting-duct-specific, non-transcriptional positives; endpoint-matched unlabeled background |
| Messenger binding | 3 | 3 | context-general biochemical evidence; too sparse for replacement at the default minimum |

An endpoint-matched background H0 is a comparison distribution, **not** a set
of experimentally proven noninteractions. It excludes all curated positive and
explicit-negative pairs in the same profile and is labeled as background in
every generated row. Direct tested-null evidence is used whenever it exists.
Transcription-, promoter-, mRNA-, and gene-expression-mediated records are
excluded because this workflow targets transient signaling.

Derived scaffold and BioGRID shared-partner closure factors are not
independently recalibrated. They are deterministic consequences of already
integrated anchors, so fitting them against the same physical references would
double-count evidence. Node models continue to use leave-one-stream-out support
from biologically compatible peer streams.

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

Every active stream saves its hypothesis class, context policy, anchor
definition, explicit/background H0 counts, bin boundaries, H1 and H0 counts,
`P(Data|H1)`, `P(Data|H0)`, unclipped and clipped likelihood ratios, and
fallback state in `analysis_summary.json`. The evidence inspector uses the
saved model to reconstruct each posterior.

## Compatibility

The 90th-percentile Bayes-factor distribution matcher remains available as a
GUI-selectable sensitivity analysis. Empirical likelihood calibration, BF
distribution matching, and unmodified source-native factors are mutually
exclusive modes for the node and edge stages. Historical source factors remain
in the run audit as pre-calibration values.
