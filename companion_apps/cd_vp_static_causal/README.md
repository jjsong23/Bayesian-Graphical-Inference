# Static causal graph workbench

This is a standalone application. It does not modify or run inside the V1, V2,
or ground-truth-curation web applications.

It imports the complete V1 whole-network registry, reads replicate-level
phosphoproteomic intensities, constructs biologically explicit candidate directed graphs,
tests graph-implied conditional independences, audits cross-regime data
eligibility, searches for local ghost repairs, and ranks single/batch knockout
designs under exact `do(K=0)` graph surgery.

## Start the GUI

From this folder:

```powershell
python -m pip install -r requirements.txt
python -m pip install --target .vendor -r requirements-engine.txt
python launch.py --foreground
```

Then open <http://127.0.0.1:8771/>. The GUI automatically points to the newest
complete V1 whole-network export and the Kirby/Leo phosphoproteomic workbook.
Every run is saved under `runs/<timestamp>/` with an HTML report.

Completed runs appear directly in the main GUI and include an interactive
candidate-graph explorer. Select a candidate graph, search or click a protein,
and inspect its incoming and outgoing one-hop neighborhood. Edge styling keeps
two evidence layers separate: admission by the V1 integrated-evidence threshold
and the bounded local phosphoproteomic assessment (agreement, contradiction,
inconclusive, or untested). Local agreement is not presented as proof of arrow
direction. The complete explorer payload is saved as
`candidate_graph_view.json` alongside the standalone report.

## Biological representation

- Every protein in the V1 registry becomes a latent `ACTIVITY::<gene>` node.
- Every measured phosphosite becomes a distinct observed
  `PHOS::<gene>::<UniProt>::<site>` node.
- The 12 control and 12 dDAVP channels remain separate observations. Positive
  TMT intensities are log2-transformed and median-centered within each channel.
- `TREATMENT::DDAVP`, log2 sampling time, and their deterministic interaction
  are explicit observed design nodes. dDAVP has exactly one direct biological
  activity target: latent `ACTIVITY::PRKACA` (PKA).
- V1 protein relationships are oriented between activity nodes.
- Each activity node points to its phosphosite measurements. These links are
  explicit measurement-model assumptions, not assertions that the sites
  activate the protein.
- One representative phosphosite per protein is selected by the preregistered
  completeness/magnitude rule for bounded covariance tests. All available
  phosphosites remain in the graph and exported registries.
- Protein knockouts intervene on the latent activity node, not on a
  phosphosite measurement.

## Command line

```powershell
python -m static_causal.cli --config configs/default.json --output runs/manual
```

Add `code` to `PYTHONPATH` when invoking the module directly. The GUI handles
that automatically.

## Statistical contract

- Four time points × three biological replicates × two treatment conditions
  produce 24 normalized-intensity observations in 12 matched control/dDAVP
  blocks. Treatment, log2(time), and treatment×time are modeled explicitly.
- Partial correlations use least-squares residualization on all complete
  channels, but inferential degrees of freedom use the number of complete
  matched blocks (`effective_n`), not a claim of 24 independent replicates.
  They report confidence intervals, two-sided t tests, and BH FDR.
- A nonsignificant result is called inconclusive, not independent. A zero can
  only be labelled supported when its CI fits inside the configured equivalence
  margin.
- Candidate scores are overlapping-query composite compatibility scores—not
  likelihoods, graph posteriors, or global p-values.
- Hard KO surgery deletes every incoming edge (including latent inputs), fixes
  the target to zero, preserves outgoing edges in the mechanistic graph, and
  removes the fixed node from stochastic d-separation tests.
- Cyclic candidates remain in the output but are not evaluated with DAG
  d-separation.
- General symbolic identification is performed by the pinned `y0` 0.2.11 ID
  and IDC implementations. The derivation table records engine/version, graph
  signature, estimand or certified nonidentification, trace availability, and
  a separate numerical-estimability assessment. If the engine cannot load, the
  table fails honestly with `engine_unavailable`.

## Required outputs

The run folder includes the full registry, regime audit, query bank, local test
results, constraint ledger, candidate scores, ambiguity classes, cross-regime
tests, ghost proposals, pairwise KO discrimination, exact singleton counts
(`N_unique` and `W_unique`), batch designs, do-calculus status, manifest, and a
standalone report.

## Broad graph exploration

The additive workflow in [docs/DIVERSE_GRAPH_EXPLORATION.md](docs/DIVERSE_GRAPH_EXPLORATION.md)
builds at least 10,000 unique cyclic, signed, typed graph alternatives, audits
low-confidence coverage, and assigns a Kirby-only continuous-time surrogate fit
to a budgeted subset. Its graph-bank frequencies are coverage statistics, not
posterior probabilities.

For exploration without the bounded feasible universe, use
`configs/full_space_10000.json` with `static_causal.full_space_cli`. This mode
uses sparse exact structural scoring over the complete V1 registry and supports
parallel deterministic coverage shards for guaranteed edge exposure.

## Optional Node.js export tools on another computer

The `.mjs` workbook/figure exporters are optional postprocessing tools, not
dependencies of the Python GUI. They now resolve installed `@oai/artifact-tool`
and `skia-canvas` packages instead of a specific user's runtime directory.
If a managed runtime supplies those modules outside normal Node resolution,
set `ARTIFACT_TOOL_MODULE_PATH` or `SKIA_CANVAS_MODULE_PATH` to the corresponding
absolute module entry file. These optional libraries are not bundled or
installed by the portable Python setup; the application and existing reports
remain usable without them. Do not copy another machine's native binaries.

