# Cross-species COMPARTMENTS localization evidence

## Why this replaces standalone HPA in the default model

COMPARTMENTS exposes separate knowledge, experiments, text-mining, prediction,
and integrated channels. Its human integrated channel incorporates Human
Protein Atlas localization evidence. The older project input was different: it
used only the **mouse knowledge channel**, so it did not itself contain the HPA
stream and was restricted to the separate AlphaFold-screening workflow.

Version 1 now uses the official integrated human, mouse, and rat releases as a
single broad-localization edge stream. Standalone HPA primary and HPA
high-confidence remain available only as mutually exclusive legacy sensitivity
alternatives. They must not be enabled with integrated COMPARTMENTS because
that would count HPA-derived localization twice.

## Cross-species consolidation

The mouse graph is the canonical node universe.

- Mouse COMPARTMENTS symbols map directly to canonical mouse graph symbols.
- Human symbols map through the existing mouse–human orthology table used by
  the HPA and BioGRID workflows.
- Rat symbols map through the existing Ensembl 116 rat–mouse orthology table.
- For each mouse node and GO cellular-component term, the maximum integrated
  score across mapped mouse, human, and rat records is retained.
- Contributing species, source symbols, and source identifiers remain in the
  audit table.
- Species agreement is **not** multiplied into multiple Bayes factors. It is
  corroborating provenance for one consolidated localization observation.

The primary profile retains integrated COMPARTMENTS scores of at least 4.0.
Scores are divided by five, arranged into a GO-term vector, L2-normalized, and
compared with cosine similarity. Each node receives its q75 background
threshold. If the all-pair q75 is zero, the positive-overlap q75 fallback is
used. Directed likelihoods use the existing complement-of-minimum kernel and
are averaged across the two endpoints before conversion to a Bayes factor.

## Current build

The build generated on 2026-10-02 from the official releases dated 2026-09-21
contains:

- 891 graph nodes, including 871 proteins;
- 869 proteins with a consolidated profile;
- 862 nodes supported by mouse annotations;
- 814 nodes supported by human annotations;
- 783 nodes supported by rat annotations;
- 29,866 consolidated node–GO annotations across 1,051 GO terms; and
- 44,948 non-neutral pair factors.

## Reproducibility and files

Run:

```powershell
python code/edge_characterization/integrate_compartments_localization.py
```

The builder streams the official datasets instead of retaining roughly 740 MB
of raw downloads. It records complete-source SHA-256 hashes, HTTP metadata,
row counts, mapping rules, and output paths in:

`data/edge_characterization/compartments/integrated/processed/provenance.json`

Other outputs are:

- `mapped_integrated_annotations.tsv.gz`: one auditable row per consolidated
  graph-node/GO-term observation;
- `node_compartments_profiles.tsv.gz`: per-node profiles, species coverage,
  and q75 thresholds; and
- `results/backend_bayes_factor_catalogs/edge_factors_891/compartments_localization_bf_gt1.tsv.gz`:
  sparse pair factors consumed by the GUI.

Official source page: <https://compartments.jensenlab.org/Downloads>

COMPARTMENTS paper: <https://pubmed.ncbi.nlm.nih.gov/24573882/>
