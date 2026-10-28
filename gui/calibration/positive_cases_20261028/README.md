# Positive Cases edge-likelihood reference catalog

This directory is the versioned input and generated output for the V1 edge
models dated 2026-10-28. The source workbooks are retained unchanged under
`source/Positive Cases`.

## Policy

- Physical proximity and physical binding use collecting-duct-specific curated
  positives. Direct contact and co-complex evidence are treated equally.
- Acute regulatory signaling uses collecting-duct-specific direct regulation,
  excluding transcription-, promoter-, mRNA-, and gene-expression mechanisms.
  It calibrates both OmniPath and STRING.
- Directed phosphorylation accepts direct biochemical kinase/substrate evidence
  across model contexts because KinasePredictor does not require collecting-duct
  specificity. Explicit tested-null pairs provide H0.
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
| physical proximity | 78 | 390 | endpoint-matched background |
| physical binding | 78 | 390 | endpoint-matched background |
| directed phosphorylation | 88 | 22 | explicit tested null |
| acute regulatory signaling | 48 | 240 | endpoint-matched background |
| messenger binding | 3 | 3 | endpoint-matched background; sparse fallback |

## Files

- `curated_edge_likelihood_references.tsv`: one canonical H1/H0 reference row.
- `curated_edge_likelihood_training.tsv`: each reference expanded to the V1
  streams in its hypothesis profile, including the source-scale Bayes factor.
- `curated_edge_likelihood_summary.json`: counts, policies, and default fitted
  distributions.
- `unresolved_reference_rows.tsv`: audit trail for rows that could not be mapped
  unambiguously into the modeled V1 symbol universe.

Regenerate from the repository root with:

```text
python gui/build_curated_likelihood_references.py
```
