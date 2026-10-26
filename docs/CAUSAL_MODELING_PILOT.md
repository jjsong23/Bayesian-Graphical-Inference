# V1 causal-modeling pilot

This pilot treats the existing V1 Bayesian network as a **candidate generator and prior-evidence layer**. It does not reinterpret independent V1 node/edge posteriors as a joint causal-graph posterior.

## Run

From the repository root:

```powershell
python run_causal_modeling.py
```

For a quick smoke run:

```powershell
python run_causal_modeling.py --fast --output results/causal_modeling/smoke_test
```

The default configuration is `configs/causal_modeling/prkaca_aqp2_pilot.json`.

## What it does

1. Audits the replicate-level dDAVP time series and the locally available PKA-KO workbooks.
2. Builds a six-state measured module from the V1 Prkaca-to-Aqp2 ranked paths.
3. Contracts excluded or unmeasured intermediates into explicitly labeled effective mechanisms.
4. Fits sparse continuous-time linear input-response models to the irregular 1, 2, 5, and 15 minute grid.
5. Chooses the representative phosphosite for each gene inside every training fold.
6. Compares candidates by leave-one-timepoint-out log predictive density and RMSE.
7. Refits edge and node ablations, runs a local sensitivity-rank audit, tests restricted one-edge repairs, and ranks follow-up perturbations/time points by cross-model predictive dispersion.

## Interpretation boundary

- The observed state for a gene is a selected phosphosite log2(dDAVP/vehicle) response, not protein abundance or kinase activity.
- PRKACA is an exogenous input because it is not directly measured in the supplied time-course workbook.
- A contracted edge is an effective mechanism and may hide multiple proteins.
- The time points are destructively sampled population responses, not longitudinal cells.
- PKA-KO workbooks available locally contain summary contrasts rather than raw replicate matrices and are not used as clean zero-shot validation.
- PKA-KO summaries contributed to V1 node evidence, and the time series is already available to V1 diagnostics. Results are therefore labeled exploratory/conditional.
- Predictive model dispersion is a design-screening surrogate, not expected information gain.
- The numerical Fisher/sensitivity audit is local and model-conditional, not a structural-identifiability certificate.

## Outputs

Each run writes the requested audit and analysis artifacts:

- `data_audit/`
- `candidates.tsv` and `candidate_edges.tsv`
- `candidate_scores.tsv` and per-fold scores
- `graph_ambiguity_classes.tsv`
- `edge_importance.tsv` and `node_importance.tsv`
- `identifiability_audit.tsv`
- `missing_mechanism_proposals.tsv`
- `experiment_designs.tsv`
- `predicted_readouts.tsv` and `predicted_trajectories.svg`
- `scientific_report.md`
- `run_manifest.json`

The implementation is intentionally a small transparent baseline. It should be expanded only after raw KO replicate matrices and their exact genotype-by-treatment design are available.
