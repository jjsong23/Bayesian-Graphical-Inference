# Bayesian Graphical Inference of Renal Signaling

This research codebase builds and explores a probabilistic signaling graph for renal principal cells, with a current biological focus on vasopressin/PKA regulation of Aqp2 throughout the collecting duct. Bayesian node and edge inference first produces an undirected association graph; an auditable ontology-plus-KinasePredictor-plus-OmniPath layer can then partially orient supported edges for signal-propagation path searches. It combines heterogeneous evidence in four stages:

1. **Node selection** estimates which candidate signaling participants are relevant to the biological system.
2. **Edge characterization** estimates the probability that two selected nodes are associated.
3. **Full-graph description** computes threshold-aware graph and node statistics without drawing the complete graph (enabled by default and independently configurable).
4. **Path inference** ranks plausible paths from a chosen signaling receptor or regulator to a target protein.
5. **Temporal validation** optionally tests whether measured dDAVP phosphoproteomic response times are consistent with the proposed path order.

The local workbench exposes evidence selection, per-dataset normalization controls, evidence weights, and default class-conditional empirical estimates of `P(Data|H1)` and `P(Data|H0)`. Physical-proximity and structural streams are calibrated against physical-interaction references; kinase, regulatory, and broad association streams use their own biologically appropriate reference classes. Completed runs save both distributions, their reference counts, smoothing, bins, bounds, and the resulting likelihood ratios for reconstruction in the evidence inspector. Missing or out-of-scope data remain neutral. The legacy cross-stream BF-magnitude matcher remains available only for historical reproducibility and is bypassed by the new default. Scope-aware negative evidence, partial directionality, path constraints, external-target insertion, full-graph statistics, and temporal annotations remain independently configurable. See [the empirical likelihood method](docs/empirical_likelihood_models.md).

Two default-enabled IMCD compartment co-detection streams separately encode basal and 30-minute dDAVP conditions from the NHLBI cytoplasmic and nuclear proteome resources. They use binary detection only—never spectral-count magnitude, fold change, or p-values—and support an undirected pair when both proteins occur in cytoplasm or both occur in nucleus. See [the IMCD evidence method](docs/imcd_compartment_presence_evidence.md).

For the lab-specific Aqp2 analysis, node selection exposes rat proteome and
mouse RNA abundance for CCD, OMCD, and IMCD as six separate streams. All six
are enabled in the Version 1 default profile; the mapping audits are under
`results/collecting_duct_node_selection/` and
`data/node_selection/collecting_duct/` in the companion data archive.

## Repository and data archive

GitHub contains the source code, tests, interface, documentation, and configuration. Large datasets and generated results are intentionally excluded from Git history and distributed separately in the complete project archive. Extract that archive so that `data/`, `results/`, and `outputs/` sit beside `code/` and `gui/`.

Read [CODEX_PROJECT_CONTEXT.md](CODEX_PROJECT_CONTEXT.md) first when taking over the project in a new Codex session. It records the current scientific conventions, important entry points, canonical artifacts, and decisions that must not be silently reversed. See [DATA_AND_RESULTS.md](DATA_AND_RESULTS.md) for the data handoff layout and [docs/dataset_acquisition.md](docs/dataset_acquisition.md) for the complete download/rebuild inventory.

For the exact changes in the current code state, read [CHANGELOG.md](CHANGELOG.md)
and the latest entry under [`docs/lab_notebook/`](docs/lab_notebook/).

## Architecture at a glance

```text
candidate proteins --node evidence--> selected signaling nodes + messengers
       |                                      |
       |                                      v
       |                         unordered node-pair hypotheses
       |                                      |
       |                              edge evidence integration
       |                                      v
       +--------------------------> symmetric edge probabilities
                                              |
                   ontology + KinasePredictor + OmniPath traversal constraints
                                              v
                           ranked paths to an internal or external target
                                              |
                         optional temporal audit + interactive merged network
```

Node and edge hypotheses begin at an independent probability of 0.5 by
default. Evidence is integrated in odds space as
`posterior odds = prior odds × product(BF_i ^ weight_i)`. The graph is not a
competition in which probabilities sum to one. Edge existence remains an
undirected hypothesis even when the separate propagation layer disallows one
traversal direction.

### Legacy cross-stream BF distribution matching

Historical configuration files may still request the former Bayes-factor
distribution matcher. For every active stream, that method measures the
90th percentile of non-neutral `abs(ln(BF))` and raises all of that stream's
factors to one audited exponent so the percentile approaches a configurable
target magnitude (default BF 2). This compresses a broad stream such as STRING
and can expand a weak stream without forcing their source-native scores or
thresholds to share units. BF 1 stays exactly neutral, BF above/below 1 keeps
its support/refute direction, and the exponent is bounded to 0.1–10. Explicit
stream weights are applied afterward and remain independently user-controlled.

This legacy feature is a sensitivity/scale-harmonization tool, not empirical
calibration and not a guarantee of independence between databases. It is off
by default, is no longer exposed as the ordinary GUI workflow, and is bypassed
whenever the default empirical class-conditional model is enabled. Historical
runs still retain raw and transformed BFs for exact reproduction. See
[`docs/bayes_factor_distribution_normalization.md`](docs/bayes_factor_distribution_normalization.md).

### BioGRID physical-interaction catalog

`code/experimental_ppi/biogrid_ppi_counts.py` builds an auditable physical-PPI
catalog from the latest BioGRID Tab3 archive. It distinguishes direct-binding
and co-complex assays, reports human, mouse, rat, and cross-species records,
retains excluded assay counts, and exports nonredundant pair annotations in
Parquet and compressed TSV formats. Reproduction and the exact assay mapping
are documented in `docs/biogrid_ppi_evidence.md`.

BioGRID is now an explicit positive-only edge stream. Direct/contact and
co-complex records share one default BF 5; when both are present, the pair is
counted once rather than multiplying correlated records.
Record absence always remains neutral because BioGRID does not define a
comprehensive tested-negative pair set. Human projections preserve HuRI coverage.
The default-enabled one-pass shared-partner closure assigns a fixed BF when two proteins
each have a direct/contact or co-complex BioGRID relationship to the same third
protein. The full anchors and closure support counts are audited; the rule is
dependent proximity evidence, not proof of direct binding. A pair already
reported by BioGRID is excluded from closure, so reported and closure BFs are
never multiplied for the same endpoints. Exact methods and
sensitivity caveats are documented in `docs/biogrid_ppi_evidence.md`.

The separate V1 AlphaPulldown evidence campaign tests colocalization-supported
pairs that still lack direct BioGRID evidence. It runs the 732 BioGRID
co-complex-only pairs first, followed by the other 6,164 eligible pairs. This
is not a V2 beam-search feature: V1 owns its manifests, compact predictions,
raw ipTM audit, and candidate evidence table. The completed feature/MSA cache
from the sibling V2 checkout may be mounted read-only to avoid recomputation.
Completed portable bundles are validated by
`code/alphafold/import_v1_alphapulldown_evidence.py`. The current import adds
6,805 measured pairs as a default-enabled structural edge stream; measured low
ipTM scores are retained below BF 1, while 91 missing computations remain
neutral. Edge inspection traces each measurement back to ipTM, phase, pair ID,
and selection basis. The frozen odds mapping around ipTM 0.6 is explicitly a
heuristic pending empirical calibration.
See [the V1 Biowulf screen guide](docs/v1_colocalized_biogrid_alphapulldown.md).

Every nonredundant primary edge stream is enabled by default. Cross-species
integrated COMPARTMENTS replaces standalone HPA localization because its human
channel already incorporates HPA. HPA primary and HPA high-confidence remain
available only as mutually exclusive legacy sensitivity alternatives, so no
default analysis counts HPA-derived localization twice. See
[`docs/compartments_cross_species_evidence.md`](docs/compartments_cross_species_evidence.md).

### Full-graph statistics

The default-enabled **Describe the full graph** stage makes one undirected unique-pair
graph from all selected nodes. An edge exists only when its posterior is
strictly greater than the user-selected graph-statistics cutoff. It exports
`full_graph_node_statistics.tsv.gz` and
`full_graph_statistics_summary.json`. New runs deliberately focus on four
familiar node measures: degree, weighted degree (the sum of incident edge
posteriors), clustering coefficient, and betweenness centrality. The overview
shows only node count, edge count, density, and connected components. These
statistics describe the inferred thresholded graph and do not update Bayesian
probabilities. The GUI and saved HTML show up to 100 ranked nodes for each of
the four measures; the TSV retains every node. Historical configurations that
request the former advanced measures remain readable for compatibility.

After pathfinding, the workbench also computes node statistics for the exact
union of every path returned by the search. Only edges that actually occur in
those paths are included; other posterior-supported edges between the same
nodes are not added. This table reports how many returned paths contain each
node alongside compact structural measures. The ordinary
interface ranks nodes by path participation, intermediate traversal count,
degree, weighted degree, and betweenness. Traversal count includes only paths
where the node is internal, not a fixed endpoint; the TSV also retains the
detailed path-use audit columns. Results
are saved as
`found_path_union_node_statistics.tsv.gz` and
`found_path_union_statistics_summary.json`. “All found paths” means all paths
returned under the configured Top paths, maximum hops, cutoff, and direction
constraints—not every mathematically possible simple path.

### Optional node-aware path score

Edge-only ranking remains the default. If **Include internal-node probabilities
in path score** is checked, a path with `m` edges and `m-1` internal nodes uses:

`score = (product(edge posteriors) × product(internal-node posteriors)) ^ (1 / (2m - 1))`

The start and target are fixed by the query and are therefore excluded from the
node product. Curated/unscored molecules contribute 1.0 so missing node data is
not silently converted into negative evidence. The bounded posterior is used
rather than an unbounded raw Bayes factor, so the score remains between 0 and 1.
Every output retains the edge-only product and geometric mean as audit fields.

## Quick start

Python 3.10 or newer is recommended.

```powershell
python -m pip install -r requirements.txt
python launch.py
```

The workbench binds to `127.0.0.1` and opens at `http://127.0.0.1:8765/`.
Use `python launch.py --foreground` when you prefer a server that stops with
Ctrl+C, or `python launch.py --check` to validate a freshly extracted release.
The PowerShell launcher remains available at `gui/run_workbench.ps1`.

After a run completes, use **Save entire session (.html)** in the results
panel to create one self-contained offline record. It includes the exact run
configuration, summaries, posterior plots, merged path network, ranked paths,
calibration results, evidence ledgers inspected during the browser session,
and a compressed vault from which every generated run file can be restored
with its original name and SHA-256 checksum.

The edge evidence inspector also provides database-native tracebacks for
STRING, OmniPath, BioGRID, and derived closure records. An optional
reasoning-model interpreter accepts a session-only API key in the GUI (or an
`OPENAI_API_KEY` launch-environment fallback), then uses web search to audit
every unique node and edge in the displayed top-path network. It distinguishes
exact-context knowledge, related-context evidence, and plausible novelty and
adds a pathway-level synthesis. Its report and citations are downstream
commentary only and never alter Bayesian scores. See
[`docs/database_traceback_and_literature_interpreter.md`](docs/database_traceback_and_literature_interpreter.md).

For a complete scientific and software overview, read
[`docs/PROJECT_COMPLETE_SUMMARY_2026-08-25.md`](docs/PROJECT_COMPLETE_SUMMARY_2026-08-25.md).
The reproducible lab-sharing release procedure is documented in
[`docs/SHAREABLE_RELEASE_2026-08-25.md`](docs/SHAREABLE_RELEASE_2026-08-25.md).

## Main directories

- `code/`: Bayesian utilities and reproducible analysis modules.
- `gui/`: minimal local web workbench, evidence registry, and workflow engine.
- `docs/`: detailed methods, target-extension documentation, and lab notebook.
- `notebooks/`: reserved for exploratory notebooks.
- `data/`: source and precomputed evidence tables; supplied in the data archive.
- `results/`: immutable analysis outputs; supplied in the data archive.
- `outputs/`: additional generated artifacts; supplied in the data archive.
- `deliverables/`: ignored release archives produced for lab sharing.

The Tq-aware leave-one-stream-out analysis is implemented in
`code/sensitivity_analysis/analyze_end_to_end_ablation.py`. By default it tests
all registered streams, including optional streams in matched add-one contexts,
while holding non-focal settings fixed and rebuilding downstream nodes, edges,
directionality, and paths as appropriate.

The conditional dependency screen in
`code/sensitivity_analysis/analyze_evidence_redundancy.py` distinguishes plain
agreement from potential evidence redundancy by conditioning each stream pair
on all remaining evidence. Its method and interpretation limits are documented
in `docs/evidence_redundancy_audit.md`.

## Tests

From the repository root:

```powershell
python -m unittest discover -s code -p "test_bayes_factors.py"
python -m unittest discover -s code\path_finding -p "test_*.py"
python -m unittest discover -s code\sensitivity_analysis -p "test_*.py"
python -m unittest discover -s gui -p "test_*.py"
```

## Scientific status

This is an evolving hypothesis-generation workflow, not a clinically validated model. Path scores rank graph-supported hypotheses; they are not calibrated probabilities that an entire biological pathway is correct. Source-specific licenses and redistribution restrictions remain applicable to the data in the companion archive.
