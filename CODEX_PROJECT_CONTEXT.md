# Codex project context

Read this file before changing the project. It is the short handoff for a new Codex session; the detailed scientific record is in `docs/full_project_methods.md`, `docs/edge_characterization_methods_891.md`, `docs/path_finding_target_extension.md`, and `docs/lab_notebook/`.

## One-sentence objective

Construct an auditable Bayesian graph of renal principal-cell signaling participants, estimate undirected association probabilities between them from selectable evidence streams, and rank plausible signal-propagating paths from a user-selected start node to a target such as Aqp2.

## Current architecture

```text
9,170 candidate mouse proteins
        |
        | node-level Bayes factors
        v
selected node universe + curated second messengers
        |
        | edge-level Bayes factors on unordered pairs
        v
symmetric undirected probability matrix
        |
        | optional external-target row/column
        v
ontology-constrained, product-ranked loopless paths
```

The validated seed graph contains **891 nodes: 871 proteins and 20 curated secondary messengers**. The GUI is no longer capped at 891: newly selected protein nodes are characterized against the active universe and added as needed. Raw observations for new unordered pairs are cached so later runs calculate only previously unseen pairs under the current source signature.

A lab-specific Aqp2/collecting-duct profile additionally enables proteome and
RNA abundance for CCD, OMCD, and IMCD. All six new streams plus the existing
four select **3,296 proteins**, or **3,316 total nodes** with second messengers.
This large profile is stored separately; the generic GUI defaults remain at the
validated 891-node baseline.

## Bayesian conventions

- Node and edge evidence are represented as positive Bayes factors (BFs).
- Missing or inapplicable evidence is neutral: `BF = 1`. It is not evidence against a node or edge.
- For a probability prior `p`, integration is performed in odds space: `posterior_odds = prior_odds * product(BF_i ** weight_i)`, then converted back to probability.
- The common continuous-evidence transformation uses a source-specific threshold `T_q`. The GUI permits independent normalization multipliers for each applicable dataset.
- STRING and STITCH instead use score odds relative to configurable reference scores, not the Gaussian-complement threshold kernel.
- The historical phrase "complement of the minimum Bayes factor" refers to converting complementary tail evidence into positive likelihood/Bayes support while preserving a neutral floor. Confirm the exact implementation in `code/bayes_factors.py` before describing equations.
- Node and edge posteriors are independent Bernoulli updates. Every protein and every edge begins at probability 0.5 by default, and posterior values are not normalized across candidates or pairs. Do not describe ranked whole-path products as calibrated biological probabilities.

## Node selection streams

The selectable streams currently include:

- mpkCCD protein abundance;
- principal-cell RNA expression, using raw abundance with zero-valued observations excluded from the evidence-background calculation;
- inferred kinase activity from PKA-knockout phosphosite changes; and
- protein-level differential phosphorylation after PKA deletion;
- rat proteome abundance for CCD, OMCD, and IMCD, harmonized to mouse with
  audited Ensembl release 116 orthology; and
- mouse renal-tubule RNA abundance for CCD, OMCD, and IMCD.

For every protein, the active model compares `present in the signaling system`
with `not present`. The default prior assigns probability 0.5 to each
hypothesis. Protein/RNA source scores have a neutral floor of 0.5 and are
divided by 0.5 to obtain a conventional neutral Bayes factor of 1; kinase and
phosphoprotein multipliers are already neutral at 1. Weighted factors multiply
each protein's prior odds independently. A protein is selected when its
posterior is strictly greater than the configured node cutoff, which defaults
to 0.5. There is no across-protein sum-to-one normalization.

Non-kinases remain neutral under kinase-activity evidence. Proteins with no detected phosphosite remain neutral under phosphoprotein evidence. Curated secondary messengers are inserted separately because they are not gene products measured by the protein/RNA screens.

For the six collecting-duct streams, zero is nondetection and receives BF=1;
each q75 uses all positive finite source values in that assay segment before
candidate filtering. Rat mapping uses UniProt first and rat-symbol fallback,
prefers high-confidence orthologs, flags low-confidence fallbacks, retains valid
multi-ortholog relationships, and aggregates duplicate contributions by the
maximum abundance. The three segment streams within each assay share a
dependence group. Entry points are
`code/node_selection/build_collecting_duct_evidence.py` and
`code/node_selection/run_collecting_duct_node_selection.py`.

The canonical symbol field is `symbol`; duplicate symbols were removed when the original liberal signaling universe was consolidated.

## Edge characterization streams

The graph is intentionally **undirected and symmetric**. An unordered biological pair is represented once in pair tables and mirrored across the adjacency matrix; self-edges are excluded or fixed by the relevant matrix convention. Direction and activation/inhibition annotations from sources such as OmniPath are retained for audit but are not currently used to direct the graph.

Selectable streams include:

- mpkCCD subcellular localization;
- observed-phosphosite-restricted KinasePredictor kinase–protein support;
- STRING protein association;
- Human Protein Atlas localization (primary or high-confidence alternative);
- OmniPath interactions, collapsed to undirected support;
- STITCH and curated secondary-messenger associations; and
- optional scaffold-mediated binary closure.

Localization compatibility/adjacency matrices for mpkCCD, HPA, and COMPARTMENTS are stored with the archived data so a scientist can validate every assumed compartment relationship. COMPARTMENTS is used in the dedicated colocalization/AlphaFold candidate-filtering workflow. Experimental PPI tiers assembled from STRING, BioGRID, and IntAct support the prior-knowledge filter used before structural prediction.

The incremental raw-pair cache is `data/edge_characterization/incremental_edge_cache/edge_pair_cache.sqlite3`. It is a performance cache, not the only scientific record: each run also writes explicit configuration and audit outputs.

## Scaffold closure: do not restore the rejected heuristic

The accepted optional rule is deliberately simple:

1. Use only the **pre-closure** graph.
2. A protein is an anchor to an exact `adaptor_scaffold` node only when their edge probability is strictly greater than the selected cutoff (default `0.90`).
3. Two proteins qualify if they share at least one such scaffold.
4. Every qualifying pair receives fixed support likelihood `0.90`, equivalent to `BF = 0.90 / 0.50 = 1.8`; every other pair receives neutral `BF = 1`.
5. Apply the rule once. Closure-derived edges never become new anchors.

There is **no scaffold-degree or “promiscuity” penalty, no anchor-excess score, no noisy-OR aggregation, and no empirical scaffold `T_q`**. Those earlier heuristics were rejected as insufficiently biologically justified. Closure is off by default and must be described as hypothesis-generating proximity/co-complex support, not proof of a direct PPI.

Validation output: `results/gui_runs/scaffold_binary_closure_validation_20260804_v2/`. At defaults, 195 scaffold-tagged proteins yielded 14,308 qualifying protein–scaffold anchors, 205,920 qualifying protein pairs, and 81,323 pairs newly raised above probability 0.5.

## Target extension and path inference

`code/path_finding/build_target_adjacency_vector.py` characterizes an external mouse protein against all active nodes and appends a symmetric row and column while preserving the existing matrix. `code/path_finding/find_ranked_paths.py` ranks loopless undirected paths by the product of edge probabilities, implemented with additive `-log(p)` costs and a hop-limited Yen/Dijkstra search.

Path intermediates are controlled by ontology-derived role classes in the GUI. The sensitivity-oriented interface exposes all current role labels and lets the user select which may propagate signals. Multi-role kinase/scaffold proteins remain eligible whenever they match any selected relay role. The separate strict scaffold override excludes every scaffold-tagged intermediate and should remain optional. Start and target nodes are endpoint exemptions.

## Workbench entry points

- Start: `gui/run_workbench.ps1`
- Server: `gui/server.py`
- Main workflow: `gui/workflow_engine.py`
- Evidence metadata: `gui/evidence_registry.json`
- Browser client: `gui/web/`
- Bayesian primitives: `code/bayes_factors.py`
- Incremental edge cache: `code/edge_characterization/incremental_edge_cache.py`

The registry drives the cards and generic factor combination. A genuinely new raw-data transformation still needs a dedicated, audited preprocessing module that emits the standard factor-table shape.

## Run outputs and current validated records

- GUI runs: `results/gui_runs/<immutable_run_id>/`
- Scaffold-rule validation: `results/gui_runs/scaffold_binary_closure_validation_20260804_v2/`
- Target extensions: `results/path_finding/target_extensions/`
- Ranked paths: `results/path_finding/ranked_paths/`
- Current methods: `docs/full_project_methods.md`
- Current edge methods: `docs/edge_characterization_methods_891.md`
- Collecting-duct node results: `results/collecting_duct_node_selection/`
- Recent decisions: `docs/lab_notebook/2026-08-10.md`

The full data/results tree is not stored in GitHub. Restore it from the dated complete-project archive described in `DATA_AND_RESULTS.md`, preserving paths.

## First checks in a new Codex session

1. Read this file and the most recent lab-notebook entry.
2. Inspect `git status` and preserve unrelated user changes.
3. Confirm the companion data archive has been restored before running the GUI.
4. Run the unit tests listed in `README.md` before and after logic changes.
5. For a scientific claim, trace the exact factor column and source metadata through `gui/evidence_registry.json`, the preprocessing script, and the immutable run configuration.
6. Record substantive method changes in both the relevant detailed methods document and a dated lab-notebook entry.

## Scientific cautions

- Treat evidence-stream dependence explicitly; kinase activity and phosphoprotein response share the PKA-knockout experiment, and scaffold closure is derived from the integrated graph.
- HPA primary and high-confidence variants are mutually exclusive alternatives.
- A database association can represent functional linkage or co-complex support rather than direct physical binding.
- Preserve raw score, mapping, direction/sign, threshold, and source columns in audits even when the active graph simplifies them.
- Do not silently change node identifiers, species mappings, zero handling, threshold backgrounds, neutral defaults, edge directionality, or path eligibility semantics.
