# V1 colocalized/BioGRID AlphaPulldown evidence campaign

## Scientific scope

This is a **Version 1 evidence-generation experiment**. It is not a V2
backward-search round and it does not populate V2's pair cache.

A pair is eligible only when all four current V1 localization streams have a
positive retained record:

1. mpkCCD fractionation localization;
2. cross-species integrated COMPARTMENTS localization;
3. IMCD basal nucleus/cytoplasm co-detection; and
4. IMCD dDAVP nucleus/cytoplasm co-detection.

Every pair with BioGRID direct/contact evidence is excluded. The remaining
pairs are divided without overlap:

- **Phase 1:** 732 pairs with BioGRID co-complex evidence but no direct/contact
  evidence.
- **Phase 2:** 6,164 other pairs with neither direct/contact nor co-complex
  BioGRID evidence.

The four-way intersection contains 7,118 pairs. Of these, 222 already have
direct BioGRID evidence, leaving 6,896 structural candidates.

## Why a V2 directory appears in the command

The weekend MSA/feature campaign already produced reusable AlphaPulldown
monomer objects at:

```text
/data/$USER/graphical_bayesian_inference_v2/runs/20260918_125108_4c6bdbb8/backward_search/features
```

The V1 jobs read those objects to avoid thousands of duplicate MSA searches.
This is only reuse of a computational cache. The V2 FASTA does not contain
every V1 candidate: missing sequences are recovered from V1's audited protein
index and mouse UniProt reference-proteome table. Existing feature objects are
symlinked into a V1-owned combined feature directory, and a targeted CPU array
generates retained MSAs/features only for genuinely missing proteins there.
The V2 source cache is not modified. Candidate selection, pair predictions,
logs, raw ipTM scores, structures, and evidence tables are written under the
V1 checkout. The scripts never write to V2's run state or SQLite pair cache and
never apply V2's HuRI/BioGRID substitution rules.

## Biowulf: phase 1 first

From the V1 checkout:

```bash
cd /data/$USER/graphical_bayesian_inference
source .venv/bin/activate
bash biowulf/submit_v1_colocalized_structural_screen.sh phase1
```

The builder freezes the current expected counts (7,118 / 732 / 6,164), fills
sequence gaps from the V1 UniProt mapping, and stages a V1-owned combined
feature directory. If feature objects are missing, it first submits a targeted
CPU array with saved/precomputed MSA retention and makes the GPU array depend
on its successful completion. It then submits one A100 task per phase-1 pair
with at most ten concurrent GPU tasks. Each pair uses exactly one
`model_1_multimer_v3` prediction, three recycles, and node-local scratch. Only
the score table, `ranked_0.pdb`, small metadata, and a prediction manifest
return to `/data`.

Monitor it with:

```bash
squeue -u "$USER"
SCREEN=$(cat runtime/latest_v1_colocalized_structural_screen.txt)
python -m json.tool "$SCREEN/collection_summary.json"
```

The collection job runs even when individual array tasks fail so failures are
audited. Check `status_counts` before starting phase 2.

## Submit phase 2 after reviewing phase 1

```bash
cd /data/$USER/graphical_bayesian_inference
source .venv/bin/activate
bash biowulf/submit_v1_colocalized_structural_screen.sh phase2
```

Phase 2 is submitted in sequential waves of at most 1,000 array tasks, staying
within Biowulf's 1,001-task array limit and avoiding more than 4,000 queued jobs
for one user. The final wave triggers the same aggregate collector.

To deliberately chain both phases without a manual inspection point, use
`all` instead of `phase1`, but the recommended workflow is to review phase 1
before incurring the larger phase-2 cost.

## Custom cache or FASTA paths

Override either input without changing scientific ownership:

```bash
export GBI_SEQUENCE_FASTA=/data/$USER/path/to/node_sequences.fasta
export GBI_FEATURE_DIR=/data/$USER/path/to/completed/features
bash biowulf/submit_v1_colocalized_structural_screen.sh phase1
```

FASTA identifiers and feature-object basenames must be the V1 protein symbols.
Existing source MSAs are never deleted or modified. Missing V1 MSAs are
generated and retained under the V1 screen directory.

## Evidence products and interpretation

The screen directory contains:

- `screen_summary.json`: frozen selection logic, counts, and source paths;
- `phase*/candidate_manifest.tsv`: every selected unordered pair and its four
  localization BFs;
- `phase*/models/<pair_id>/`: compact prediction products;
- `phase*/structural_prediction_audit.tsv.gz`: observed and failed tasks;
- `phase*/alphapulldown_structural_bf.tsv.gz`: phase-specific observed scores;
- `alphapulldown_structural_bf.tsv.gz`: combined, deduplicated V1-ready factor
  table; and
- `collection_summary.json`: aggregate completion and mapping provenance.

The provisional mapping converts ipTM to an odds ratio around reference score
0.6 and clips it to `[0.1, 10]`. AlphaPulldown ipTM is not a calibrated
probability, so the output is explicitly labeled a **heuristic BF pending V1
calibration**. The imported stream is enabled by default but has its own
`structural_physical_evidence` dependence label so it remains distinguishable
from BioGRID and from the localization streams that selected the screen.

Failed or missing predictions are omitted from the factor table. They remain
unobserved and neutral; computation failure is never treated as biological
negative evidence.

The AlphaPulldown interface-PAE utility is run with an export-only cutoff of
50 so that it emits an ipTM record for every valid multimer prediction. The
usual PAE < 5 hit-triage filter is not used as an inclusion criterion because
that would discard weak predictions and upward-bias the structural evidence
distribution. Low ipTM is retained as weak/negative structural evidence;
only a genuinely failed or missing computation remains neutral.

## Import a completed portable bundle into V1

Copy and extract the portable bundle anywhere under the V1 checkout, then run
the validated importer from the repository root. For the current local bundle:

```powershell
cd C:\Users\songjj\Documents\Codex\2026-07-21\un\graphical_bayesian_inference
python code\alphafold\import_v1_alphapulldown_evidence.py runtime\imports\v1_alphapulldown_import_20261026_121436
```

The importer finds the actual bundle root, validates the ownership, required
columns, unique unordered pairs, ipTM range, positive BFs, and agreement with
`collection_summary.json`. It then installs:

- the analysis-ready factor catalog at
  `results/backend_bayes_factor_catalogs/edge_factors_891/alphapulldown_structural_bf.tsv.gz`;
- the screen summaries, candidate manifests, phase audits, and phase factor
  tables under
  `data/edge_characterization/alphapulldown/v1_colocalized_model1_cycles3_predictions1_compact/`;
- an `import_summary.json` recording hashes, counts, ranges, and the neutral
  missing-result policy.

The V1 GUI registers this catalog as **AlphaPulldown structural prediction**.
For a measured pair it applies the imported BF exactly once. A pair selected
for the screen but missing a valid prediction, or a pair never screened, has
no catalog row and therefore receives BF 1 (neutral). The edge interpretation
panel traces a measured result back to its ipTM, screen phase, selection basis,
pair ID, mapping formula, and audit record.

The 2026-10-26 import contains 6,805 measured pairs: 730 in phase 1 and 6,075
in phase 2. It retains 210 BFs above 1 and 6,595 BFs below 1. The 91 missing
predictions remain neutral and are never reclassified as biological negatives.
