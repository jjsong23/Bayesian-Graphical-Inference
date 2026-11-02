# Positive Cases edge-likelihood reference catalog

This directory is the versioned input and generated output for the V1 edge
models initially dated 2026-10-28 and revised through 2026-10-30. The source
workbooks, including the controlled-negative catalogs, are retained unchanged
under `source/Positive Cases`.

## Policy

- Physical proximity and physical binding use collecting-duct-specific curated
  positives. Direct contact and co-complex evidence are treated equally.
- Acute regulatory signaling uses collecting-duct-specific direct regulation
  for H1. Confirmed H0 includes both collecting-duct negative controls and
  compatible endpoint nonresponses from outside-CD assays at equal within-group weight. The
  profile excludes transcription-, promoter-, mRNA-, and gene-expression
  mechanisms and calibrates both OmniPath and STRING.
- Directed phosphorylation accepts direct biochemical kinase/substrate evidence
  across model contexts because KinasePredictor does not require collecting-duct
  specificity. Explicit tested-null pairs provide H0.
- Controlled collecting-duct association failures provide explicit H0 for
  physical binding, and controlled collecting-duct endpoint nonresponses provide
  explicit H0 for acute regulation. These replace, rather than add to, the
  profile's target number of endpoint-matched background controls.
- The revised physical-association workbook removes all eight ROOT08
  TMEM27/collectrin claims from a retracted paper. The expanded PPI-negative
  workbook contributes only biological wild-type pair tests; specificity
  controls and restricted constructs do not label full-length pairs H0.
- Mechanism dispensability, limited/disputed results, and mutant-only controls
  are not generic pair-level H0: each can coexist with a real wild-type
  interaction. Physical-binding failures are not reused as localization H0.
- Site-specific reaction failures that coexist with a positive result for the
  same pair are retained in the unresolved/conflict audit rather than labeling
  the entire pair H0.
- Messenger binding may use context-general biochemical evidence. Only three
  canonical V1 pairs currently resolve in each class, so the default minimum of
  20 causes a documented source-native fallback.
- When a profile lacks experimental negatives, deterministic endpoint-matched
  pairs provide an **unlabeled background** H0. They are not claims of absent
  interaction.
- Derived closure streams are not independently calibrated because their values
  are consequences of already integrated physical anchors.

## Counts

| Profile | H1 | H0 | H0 type |
|---|---:|---:|---|
| physical proximity | 81 | 405 | endpoint-matched background |
| physical binding | 81 | 405 | 13 explicit tested null + 392 endpoint-matched background |
| directed phosphorylation | 88 | 27 | explicit tested null |
| acute regulatory signaling | 48 | 240 | 11 confirmed CD tested null + 16 confirmed outside-CD tested null + 213 endpoint-matched background |
| messenger binding | 3 | 3 | endpoint-matched background; sparse fallback |

When both H0 groups exist, the fitted mixture parameter `w` gives
confirmed, context-appropriate negatives total mass `w` and matched-background
pairs total mass `1-w`. Weights are normalized within each group, so group size
does not determine the intended reliance. Each stream evaluates `w` from 0 to 1
in steps of 0.01 and selects the value with maximum Jensen–Shannon separation;
both directional KL indices and total variation are retained. Context-general
biochemical negatives remain eligible for KinasePredictor because that stream
does not require collecting-duct specificity.

## Files

- `curated_edge_likelihood_references.tsv`: one canonical H1/H0 reference row.
- `curated_edge_likelihood_training.tsv`: every reference cross-scored by every
  primary V1 edge stream, including its source-scale Bayes factor. Runtime can
  use class-matched subclasses or the pooled unique-pair catalog.
- `curated_edge_likelihood_summary.json`: counts, policies, and default fitted
  distributions.
- `unresolved_reference_rows.tsv`: audit trail for rows that could not be mapped
  unambiguously into the modeled V1 symbol universe.

Regenerate from the repository root with:

```text
python gui/build_curated_likelihood_references.py
```
