# Conditional evidence-redundancy audit

## Question

Agreement is not automatically redundancy. Two genuinely independent assays
can agree because the same node or edge is real. The audit instead asks whether
two evidence streams remain associated after accounting for the support
supplied by every other enabled stream.

## Method

For every pair of enabled node streams and every pair of enabled edge streams:

1. Retain the complete node or unique-undirected-edge hypothesis universe,
   including rows neutral in both streams. Restricting to the pairwise
   non-neutral union is forbidden because it creates collider/Berkson bias.
2. Convert each stream to its applied contribution, `weight × ln(BF)`.
3. Sum the contributions from all other enabled streams.
4. Form up to 20 quantile strata of that other-evidence sum. Edge strata also
   preserve the endpoint node-type combination.
5. Within those strata, remove stratum means and calculate residual log-BF
   correlation and residual support-call phi.
6. Also calculate conditional mutual information for the three signed calls
   (refutes, neutral, supports), observed versus conditionally expected
   co-support, support Jaccard, and overlap counts.
7. Overlay the registry's declared `dependence_group` provenance annotations.

The empirical screen requires at least 100 jointly non-neutral hypotheses and
positive residual dependence. A pair is flagged by either (a) residual log-BF
correlation at least 0.25 plus co-support at least 1.25 times conditional
expectation, or (b) conditional mutual information at least 0.02 bits plus
conditional support phi at least 0.25. Negative correlation is not labeled
redundancy: it more often reflects complementary or mutually exclusive scope.

No row-wise p-values are reported. With up to millions of dependent edge
hypotheses, they would make negligible effects appear overwhelmingly
significant. The audit uses explicit effect sizes and treats every flag as a
scientific-review prompt, not an automatic instruction to remove a stream.

## September 23, 2026 six-run audit

The Prkaca-to-Dyrk1a/Gsk3b/Cdk12 edge-only and node-aware analyses shared one
node/edge evidence configuration. The audit found 10 unique registry-declared
dependency pairs and 13 unique empirical residual-dependency flags. Strong
expected examples included collecting-duct segment measurements from the same
proteomic or transcriptomic source, basal versus dDAVP IMCD co-detection, and
the two PKA catalytic-subunit phosphoproteomic streams. Potential undeclared
dependencies included double-KO versus single-subunit phosphoproteomic
responses and mpkCCD protein abundance versus collecting-duct proteomes.

BioGRID reported interactions and BioGRID shared-partner closure had zero
jointly non-neutral endpoint pairs after the closure exclusivity fix. Their
negative residual association is expected from the mutually exclusive rule and
is not an empirical redundancy flag.

The complete run-specific tables, heatmaps, thresholds, and findings summary
are stored under
`results/prkaca_multitarget/20260923_prkaca_three_targets_novel_biogrid_closure_2000paths_stats/evidence_redundancy_audit/`.

## Reproduction

```bash
python code/sensitivity_analysis/analyze_evidence_redundancy.py \
  results/gui_runs/<run-1> results/gui_runs/<run-2> \
  --output-dir results/<audit-directory>
```
