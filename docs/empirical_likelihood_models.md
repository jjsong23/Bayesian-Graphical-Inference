# Empirical class-conditional evidence likelihoods

Version 1 now treats an evidence stream's observed value as data to be
calibrated, rather than forcing all streams to have the same Bayes-factor
spread. For each stream the workflow estimates

\[
P(D_s\mid H_1, c),\qquad P(D_s\mid H_0, c)
\]

and applies their ratio to the prior odds. Here `s` is an evidence stream and
`c` is a biological hypothesis class.

## Edge-reference strategies

The GUI offers two edge-only strategies. **Class-matched** routes curated
physical proximity, physical binding, directed phosphorylation, acute
regulatory signaling, and messenger-binding references only to the streams
that measure those hypotheses. Localization and AlphaPulldown therefore use
physical references, KinasePredictor uses kinase/substrate references, and
OmniPath and STRING use acute regulatory signaling rather than generic
same-process association. **All curated edges** instead applies the pooled set
of unique curated edge references to every primary edge stream. If a pair is
H1 in one subclass and H0 in another, H1 takes precedence and the conflict is
saved in the audit. The versioned training table cross-scores every curated
pair with all 12 primary edge streams so switching strategies never requires
re-reading the source workbooks.

This choice does not affect node calibration. Node streams remain routed to
cellular presence, kinase activity, or transient phosphoregulation. Derived
scaffold and BioGRID shared-partner closure also remain source-native.

The versioned edge reference catalog was built from the revised positive-
evidence workbooks plus controlled signaling and PPI-negative workbooks in
`gui/calibration/positive_cases_20261028/source/Positive Cases`. Its H1/H0
policy is intentionally profile-specific:

| Profile | H1 | H0 | Context policy |
|---|---:|---:|---|
| Physical proximity | 81 | 405 | collecting-duct-specific positives; endpoint-matched unlabeled background |
| Physical binding | 81 | 405 | collecting-duct-specific positives; 13 controlled biological-pair nondetections + 392 endpoint-matched background pairs |
| Directed phosphorylation | 88 | 27 | direct biochemical kinase/substrate evidence across model contexts; explicit tested nulls |
| Acute regulatory signaling | 48 | 240 | collecting-duct-specific, non-transcriptional positives; 11 CD endpoint nonresponses + 16 outside-CD endpoint nonresponses + 213 endpoint-matched background pairs |
| Messenger binding | 3 | 3 | context-general biochemical evidence; too sparse for replacement at the default minimum |

An endpoint-matched background H0 is a comparison distribution, **not** a set
of experimentally proven noninteractions. It excludes all curated positive and
explicit-negative pairs in the same profile and is labeled as background in
every generated row. Direct tested-null evidence is used whenever it exists.
Transcription-, promoter-, mRNA-, and gene-expression-mediated records are
excluded because this workflow targets transient signaling.

The negative workbook is routed by what the assay can actually refute. A
controlled collecting-duct association failure informs physical binding, not
localization; a controlled collecting-duct endpoint nonresponse informs acute
regulation; mechanism-compatible outside-CD endpoint nonresponses also inform
the confirmed-negative component; and a direct biochemical kinase-reaction
failure may inform KinasePredictor outside a collecting-duct model.
Mechanism dispensability,
limited/disputed findings, and mutant-only controls are retained in the source
workbook but excluded from generic pair-level H0. Likewise, a site-specific
reaction failure does not make a pair H0 when another curated experiment shows
that the same regulator acts on another site of that target; these conflicts
remain visible in the unresolved-reference audit.

The active physical-positive workbook is the 2026-10-29 revision. It removes
eight TMEM27/collectrin claims from retracted study ROOT08. The PPI-negative
workbook's specificity controls, restricted constructs, condition-dependent
nondetections, and unrelated-protein controls do not enter pair-level H0.

Derived scaffold and BioGRID shared-partner closure factors are not
independently recalibrated. They are deterministic consequences of already
integrated anchors, so fitting them against the same physical references would
double-count evidence. Node models continue to use leave-one-stream-out support
from biologically compatible peer streams.

## Estimator

The datum is the source's pre-calibration `log(BF)`. A deterministic histogram
uses at most 12 discrete or pooled-quantile bins. Jeffreys smoothing (default
0.5 per bin) is applied independently to weighted H1 and H0 counts. Reference
weights default to one. A mixture parameter `w` in `[0, 1]` assigns `w` of
the H0 reference mass to confirmed negatives (including outside-CD experiments)
and `1-w` to endpoint-matched background. Within each component, every row
receives an equal share of that component's mass. For every stream and selected
reference strategy, V1 evaluates `w` on a 0.01 grid and chooses the value that
maximizes Jensen–Shannon divergence between `P(Data|H1)` and `P(Data|H0)`.
Jensen–Shannon is used as the objective because it is symmetric and finite;
`KL(H1||H0)`, `KL(H0||H1)`, and total variation are recorded at the optimum.
Ties prefer the value closest to 0.5 and then the lower value. If a stream has
only one H0 component, that component receives all H0 mass. Raw counts, bin
weights, effective total weights, the selected `w`, and all information indices
are retained for audit. The ratio is clipped
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

Every active stream saves its reference strategy, hypothesis class, context policy, anchor
definition, explicit/background H0 counts, bin boundaries, H1 and H0 counts,
`P(Data|H1)`, `P(Data|H0)`, unclipped and clipped likelihood ratios, and
fallback state in `analysis_summary.json`. It also saves the optimized mixture
weight, information objective, all information indices at the optimum, the
number of candidates evaluated, and cross-subclass conflicts. The evidence
inspector uses the saved model to reconstruct each posterior.

## Compatibility

The 90th-percentile Bayes-factor distribution matcher remains available as a
GUI-selectable sensitivity analysis. Empirical likelihood calibration, BF
distribution matching, and unmodified source-native factors are mutually
exclusive modes for the node and edge stages. Historical source factors remain
in the run audit as pre-calibration values.
