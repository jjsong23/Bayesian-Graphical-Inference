# PKA-responsive evidence-class curation protocol v0.6

## Purpose

Protocol v0.6 builds independent literature reference sets for calibrating the
evidence streams used by Graphical Bayesian Inference. It does not use a single
generic definition of “supported.” Each search and validation pass is tied to a
specific biological claim and a restricted set of admissible experimental
methods.

The search universe is the full acute PKA-responsive protein network. AQP2 may
appear when relevant, but it is not required to be an endpoint. The protocol
includes direct PRKACA/PRKACB targets and downstream relationships between two
non-PKA proteins when an experiment shows that the relationship responds to
acute PKA activation, inhibition, depletion, or rescue. Relationships mediated
only through transcription, promoter activity, mRNA, gene expression, or
long-term abundance changes are excluded.

## Evidence profiles

| Validation profile | GBI streams calibrated | What the literature must test | Examples of admissible primary methods |
|---|---|---|---|
| Physical interaction | AlphaPulldown structural, BioGRID physical, mpkCCD/COMPARTMENTS/IMCD colocalization, scaffold and shared-partner closure | Physical binding or membership in an experimentally isolated molecular complex | co-IP, affinity pull-down, purified binding, Y2H, FRET/BRET, cross-linking, proximity labeling |
| Directed phosphorylation | KinasePredictor | A named kinase directly phosphorylates a target, or a named phosphatase removes the phosphate | purified kinase/phosphatase assay, phosphosite mutant, acute enzyme perturbation with a phospho-specific readout |
| Acute regulatory signaling | OmniPath and STRING | Acute perturbation of the source changes target activity, localization, trafficking, or post-translational state | genetic or inhibitor perturbation, activity assay, trafficking assay, functional assay, phosphosite mutant |
| Messenger binding | STITCH | A secondary messenger or small molecule directly binds the protein or shows target engagement | ligand/affinity binding, competition, target engagement, purified binding |

STRING is intentionally calibrated against acute directed regulation rather
than generic functional relatedness. Every retained directed-phosphorylation
candidate is also inserted into the acute-regulatory candidate set, because
direct phosphorylation is a mechanistically specific subset of acute
regulation. It is still validated separately under the stricter phosphorylation
rules before it can calibrate KinasePredictor.

Database co-membership, prediction, pathway diagrams, text mining, omics
correlation, and colocalization by themselves cannot validate a physical or
causal claim. They may identify a candidate, but the independent Sol validation
must retrieve an admissible primary experiment.

## Positive and negative references

Discovery alternates positive and negative searches within every enabled
profile. Validation separately records the positive queries and negative
queries used for each pair.

- **H1 positive:** at least one admissible primary result supports the profile's
  claim and the result is either directly PKA-related or experimentally shown
  to be PKA-dependent.
- **H0 explicit negative:** at least one admissible primary experiment explicitly
  reports a null result for that pair, documents appropriate controls, and is
  directly PKA-related or PKA-dependent.
- **Conflict:** qualifying positive and explicit-negative results both exist.
- **Context only:** the result is connected only to cAMP or vasopressin without
  a PKA test.
- **Unlabeled:** no qualifying experiment was recovered.

Failure to find a paper, absence from a database, and an unreported experiment
remain **unlabeled**. They are never converted into H0 examples.

## Two-model workflow

1. GPT-6 Luna uses hosted web search to propose pairs for one validation profile
   and one target polarity at a time. Search calls and URL citations are saved
   immediately.
2. GPT-6 Sol independently searches and retrieves primary literature for each
   proposed pair. It must document separate positive and negative searches.
3. The pipeline rejects experimental methods that are not allowed for that
   profile, verifies cited quotations against retrieved article text, and applies
   the PKA and polarity rules above.
4. Every candidate is retained in the audit trail, including conflicts,
   context-only findings, and unlabeled pairs.

## Main outputs

- `evidence_stream_references.jsonl`: every validated pair/profile record.
- `positive_references.jsonl`: H1-positive references only.
- `negative_references.jsonl`: explicit experimental H0 references only.
- `evidence_stream_references.tsv`: compact cross-profile audit table.
- `audit.tsv`: complete inclusion/exclusion reasoning.
- `edges.jsonl` and `edges.tsv`: compatibility exports containing H1-positive
  pairs.

The validation profile is part of a candidate's identity. The same protein pair
can therefore be supported for physical interaction but remain unlabeled for
directed phosphorylation without one claim leaking into the other.

## Running

Start the standalone workbench:

```powershell
cd C:\Users\songjj\Documents\Codex\2026-07-21\un\cd_vp_ground_truth
python launch.py
```

First select **Preview without API calls**. For a paid run, paste the Azure API
key into the GUI and select **Launch paid curation**. The key is never saved.
Old v0.5 checkpoints are intentionally not reused because their candidates were
generated and validated under a different, general-purpose claim definition.

Command-line preview:

```powershell
$env:PYTHONPATH = "$PWD\code"
python code\ground_truth\cli.py `
  --config configs\ground_truth_v0.6.example.json `
  --run-dir runs\v0.6_preview `
  --dry-run
```
