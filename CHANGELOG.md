# Changelog

## 2026-10-02 — Cross-species COMPARTMENTS replaces default HPA

- Added a reproducible integrated COMPARTMENTS builder using the official
  mouse, human, and rat releases.
- Projected human and rat annotations into the mouse graph through the same
  orthology resources used by the BioGRID workflow.
- Consolidated species before edge scoring by retaining one maximum-confidence
  node/GO-term observation and recording all corroborating species.
- Added a 44,948-pair V1 factor catalog covering 869 of 871 protein nodes.
- Enabled integrated COMPARTMENTS by default and disabled standalone HPA
  primary/high-confidence to prevent double-counting HPA-derived localization.
- Added Tq rescaling, negative-evidence eligibility, external-target reuse for
  catalogued nodes, score-distribution support, provenance, and regression
  tests for the new stream.
- Added `docs/dataset_acquisition.md`, a source-by-source inventory with public
  URLs, exact destination paths, rebuild commands, version caveats, provenance
  requirements, and controlled-archive instructions for non-public inputs.

## 2026-09-29 — Resizable pathway-network workspace

- Retained the compact 430-pixel embedded network and isolated large-format
  rendering to the pop-out, preventing resize feedback from slowly enlarging
  the results page.
- Added bounded 60–240% zoom with explicit zoom-out, zoom-in, and reset
  controls.
- Added a resizable modal pop-out that keeps the same interactive SVG,
  evidence-selection behavior, path-count selector, posterior scales, and
  re-layout control. A labeled pop-out icon makes it easier to discover; the
  workspace can fill the browser viewport and dock back into the results page.

## 2026-09-29 — Simplified network statistics

- Reduced the default full-graph calculation to degree/weighted degree,
  clustering coefficient, and betweenness centrality.
- Limited the live GUI and saved-session HTML to four familiar node rankings:
  degree, weighted degree, clustering coefficient, and betweenness. The
  returned-path graph substitutes returned-path participation for clustering
  and additionally reports the number of paths that traverse each node as an
  internal intermediate.
- Reduced each network overview to four basic descriptors: size, edge count,
  density, and connected components (or returned-path count for the path
  union).
- Removed the duplicate top-10 metric-card grids; each section now uses one
  metric dropdown and one top-100 ranking table.
- Retained backend support for advanced metrics in historical configurations
  and retained full TSV audit tables, while removing those measures from new
  defaults and the ordinary interface.

## 2026-09-24 — Repaired the default Prkaca-to-Aqp2 run and weight inputs

- Restored the biologically intended default path orientation: selected
  signaling start `Prkaca` to externally characterized endpoint `Aqp2`.
  The previous reversed form tried to use external Aqp2 as the selected start
  and therefore stopped before path ranking.
- Removed browser-only step-grid restrictions from continuous evidence
  controls. Weights, Tq/reference multipliers, Bayes-factor/likelihood
  parameters, and probability cutoffs now accept any decimal within their
  backend bounds. This fixes both STRING weight 0.25 and BioGRID reported-pair
  BF 5, whose former minimum-plus-step grid incorrectly demanded 5.000001.

## 2026-09-24 — All edge evidence enabled except HPA high-confidence

- Enabled every registered Version 1 edge stream in the default profile except
  `hpa_high_confidence`.
- This newly enables basal and dDAVP IMCD compartment-presence evidence and
  BioGRID shared-partner closure by default. All previously enabled streams
  remain enabled.
- Kept HPA primary localization enabled and HPA high-confidence disabled so the
  documented mutually exclusive HPA alternatives cannot conflict.
- Preserved each stream's existing BF, weight, Tq/reference, dependence group,
  and positive/negative-evidence semantics; only default selection changed.

## 2026-09-23 — Database-native tracebacks and contextual literature interpreter

- Added on-demand STRING tracebacks to mapped STRING IDs, all seven component
  channels, the combined score, reference odds, raw BF, weight, and effective
  contribution without double-counting component channels.
- Added OmniPath tracebacks to directed source/target records, sign and
  consensus annotations, resources, references, curation effort, and the exact
  undirected integration rule.
- Added BioGRID tracebacks to source species, orthology mapping, BioGRID/Entrez
  IDs, assay systems, PMIDs, direct/co-complex/HuRI flags, and one-time equal-BF
  treatment. Closure tracebacks identify the shared partner and remain labeled
  as inferred rather than reported endpoint interactions.
- Added an optional Responses API reasoning-model interpreter with required web
  search and strict structured output. It classifies exact-context knowledge,
  related-context evidence, database-only support, cautious plausible novelty,
  conflicts, or insufficient evidence for a user-specified cell type and
  signaling purpose.
- Kept the interpreter downstream of the frozen Bayesian ledger: it cannot
  alter evidence, posteriors, edges, nodes, or path ranks. Results are cached
  per run and embedded with citations in complete standalone HTML exports.

## 2026-09-23 — Stabilized default STRING contribution

- Changed STRING's default edge-evidence weight from 1.0 to 0.25 in both
  application versions. The raw STRING score and BF are retained; integration
  now uses the fourth root of the raw factor by default.
- Selected 0.25 by constraining the empirical 99th-percentile STRING factor
  (644.90) to an effective factor of approximately 5. This changes the median
  effective factor from 6.83 to 1.62 and prevents ordinary STRING records from
  driving edges close to probability 1 by themselves.
- Documented the power-likelihood interpretation and added regression coverage
  for the new default.

## 2026-09-23 — Non-overlapping closure, redundancy audit, and expanded graph reporting

- Made BioGRID shared-partner closure apply only to endpoint pairs that are not
  already reported by BioGRID. Reported pairs therefore receive the reported-
  interaction BF once and never receive a second BioGRID closure BF.
- Added a conditional evidence-redundancy audit. It retains the complete
  hypothesis universe, conditions each stream pair on all other evidence (and
  edge endpoint types), and reports residual log-BF correlation, support phi,
  signed-call mutual information, conditional co-support, and documented
  provenance groups. Full-universe conditioning avoids pairwise-union
  collider/Berkson bias.
- Enabled full-graph statistics by default and expanded GUI/standalone-report
  rankings from 20 to 100 nodes per measure. Added at-a-glance panels for every
  computed node measure and richer graph summaries.
- Added explicitly labeled deterministic approximations for clustering,
  betweenness, closeness/harmonic centrality, path length, and diameter on
  large dense graphs, plus exact-key in-process reuse for identical graphs.
- Reran all six Prkaca-to-Dyrk1a/Gsk3b/Cdk12 edge-only/node-aware analyses at
  2,000 paths and generated GUI-equivalent standalone HTML archives.

## 2026-09-23 — Equal BioGRID tiers and expanded exact path searches

- Replaced the separate BioGRID direct/contact and co-complex strengths with
  one user-controlled reported-pair BF (default 5). A pair reported by either
  tier receives the same factor, and overlap between tiers is counted once.
- Regenerated the BioGRID backend catalog: all 14,277 mapped reported pairs now
  carry BF 5; assay tiers remain available only as provenance annotations.
- Raised the Version 1 exact path limit from 500 to 2,000 after runtime
  benchmarking. The GUI validation, help text, and batch analysis driver all
  use the new ceiling.
- Batched evidence reconstruction for standalone session exports so every node
  and edge in the visible top-50 path network remains inspectable offline
  without reloading every source table once per edge.
- Reran Prkaca-to-Dyrk1a/Gsk3b/Cdk12 in edge-only and node-aware modes at 2,000
  paths per run and generated full GUI-equivalent standalone HTML archives.

## 2026-09-22 — IMCD basal and dDAVP compartment co-detection

- Downloaded the official NHLBI rat IMCD cytoplasmic and nuclear proteome
  workbooks and recorded source hashes.
- Added a reproducible presence-only processor that ignores spectral-count
  magnitude, ratios, p-values, and fold changes; nuclear extract and pellet are
  collapsed with OR, and basal and 30-minute dDAVP are kept separate.
- Harmonized rat symbols to mouse with the existing Ensembl release 116
  orthology table and wrote full source/mapping/profile audits.
- Added two independently selectable undirected GUI edge streams with a
  configurable fixed `Support L` (default 0.75, BF 1.5), external-target and
  dynamically added-node support, and source-specific optional negative scope.
- Marked both streams as correlated, disabled them by default, and documented
  their low discrimination: 97.79% of eligible basal pairs and 97.91% of
  eligible dDAVP pairs receive positive co-detection evidence.

This file summarizes user-visible and scientifically meaningful code changes.
The canonical takeover instructions remain in `CODEX_PROJECT_CONTEXT.md`; dated
implementation detail is retained under `docs/lab_notebook/`.

## 2026-09-22

### Auditable BioGRID physical-interaction catalog

- Added a chunked, low-memory BioGRID Tab3 ingestion pipeline that downloads
  or accepts an archive, validates its real header, and separates human,
  mouse, rat, and cross-species physical interactions.
- Classified a documented set of assays as direct-binding or co-complex
  evidence, retained all excluded physical assays in an audit, and made
  Reconstituted Complex and self-interaction handling explicit CLI choices.
- Exported raw, nonredundant, multi-publication, multi-method, per-system,
  pair-level, HuRI, provenance, and validation tables for BioGRID 5.0.261.
- Kept BioGRID absence neutral and did not invent a Bayes-factor scale. The
  pair catalog is ready for a later calibrated or expert-specified edge stream.

### Node statistics for the returned-path union

- Compute a second set of node statistics on the exact union of every path
  returned by pathfinding, without admitting other edges from the full graph.
- Add path participation, internal-node usage, path score, position, directed
  in/out degree and strength, and directed betweenness fields alongside the
  existing structural measures.
- Display sortable path-union node rankings in the live GUI and standalone
  session report.
- Export `found_path_union_node_statistics.tsv.gz` and
  `found_path_union_statistics_summary.json` for every pathfinding run.

## 2026-09-21

### Full-graph statistics and optional node-aware path scores

- Added an optional full-graph analysis stage that constructs one unique,
  undirected graph from all selected nodes and edge posteriors strictly above a
  user-controlled cutoff. It reports graph size, density, components, isolates,
  shortest-path structure, clustering, assortativity, communities/modularity,
  articulation points, and random-versus-degree-targeted removal robustness.
- Added complete per-node exports for degree, posterior strength, local and
  weighted clustering, reliability-weighted betweenness/closeness/harmonic
  centrality, eigenvector centrality, PageRank, coreness, component membership,
  articulation/removal impact, and community membership. The GUI displays a
  compact whole-graph summary and selectable top-node rankings without drawing
  the full graph.
- Added an optional node-aware path score. The default remains the geometric
  mean of edge posteriors. When enabled, the primary score is the geometric mean
  of every edge posterior and every internal-node posterior. Fixed endpoints
  are excluded; curated/unscored molecules contribute 1.0 so missing node
  measurement is not treated as negative evidence. Raw edge-only scores remain
  in every path table for comparison.
- Added focused unit tests for threshold semantics, hand-checkable network
  statistics, and an example in which an internal-node posterior changes the
  winning path.

### KinasePredictor-aware propagation direction and clearer evidence scope

- Split the evidence-ledger wording into **record status** and **negative
  scope**. A retained positive record is now shown before the separate fact
  that absence may not have been scorable, eliminating the misleading
  “supports / outside scope” presentation in both the live GUI and saved HTML.
- Preserved the kinase and substrate roles of supporting KinasePredictor
  records and applied `kinase -> substrate` as a path-propagation constraint
  after ontology rules and before OmniPath fallback directions.
- Recomputed whether KinasePredictor supports an ordered relationship under the
  active run's Tq/reference and negative-evidence settings. Disabling the
  stream or assigning zero weight disables its direction source.
- Encoded the uniquely disallowed substrate-to-kinase traversal as zero in the
  propagation matrix without altering the symmetric Bayesian edge posterior.
  This excludes paths that would cross a resolved fork or collider by walking
  backwards along a predicted phosphorylation relationship.
- Added an auditable `kinase_predictor_direction_evidence.tsv.gz` artifact,
  KinasePredictor columns in the combined directionality audit, source-conflict
  counts in the run summary, and regression tests for fork/collider exclusion.

### Tier-jittered path-network layout

- Preserved the path-order tier assigned to each displayed node while adding
  deterministic, evenly spaced horizontal offsets within populated tiers.
  Same-tier relationships therefore remain visibly separated instead of
  collapsing onto the same vertical line after force-layout convergence.
- Kept start and target nodes fixed at their existing endpoints. The Re-layout
  control changes the deterministic within-tier ordering without changing any
  node, edge, probability, direction, or path rank.

## 2026-09-17

### Responsive edge-integration cancellation

- Added cooperative cancellation checkpoints between edge evidence streams,
  around eligibility construction, and throughout scaffold-closure pair audit
  generation. A cancellation requested during the former 62% edge-integration
  stage no longer has to wait for the entire combined matrix to finish.

### Observed-range network colors

- Changed the merged path-network colors to use independent monotone ranges for
  scored nodes and edges visible at the selected Top-N setting. The node range
  is blue and the edge range is green; within each, the visible minimum is
  lightest and the visible maximum is darkest. This prevents weaker node
  probabilities from compressing tightly clustered edge probabilities.
- Added separate vertical node and edge probability bars beside the graph with
  each range's exact visible minimum, midpoint, and maximum. Both ranges are
  recomputed whenever the displayed Top-N path count changes.
- Applied the same separate-scale mapping to standalone complete-session HTML
  reports. Posterior histograms retain their fixed 0–1 axes.

### Length-normalized path ranking and 50-path presentation defaults

- Replaced product-based primary path ranking with geometric mean edge
  probability. The engine now finds exact top-K simple paths within every
  allowed hop count and merges them by mean negative-log edge cost; this uses
  logarithms internally for numerical search but presents the geometric mean
  as the primary score.
- Preserved raw edge-probability products, total negative-log products,
  bottleneck edges, and hop counts as secondary audit fields.
- Changed the merged network to display the top 50 available paths by default.
  Complete-session HTML export now defaults to the same 50-path view and
  materializes offline node/edge evidence interpretations for that view, with
  a 1,000-hypothesis safety ceiling.
- Validated the exact ordering against exhaustive enumeration and completed a
  100-path `Prkaca` to `Aqp2` real-data run.

## 2026-09-11

### Complete portable session export

- Added a **Save entire session (.html)** action to every completed Version 1
  run. The generated report is a single offline file containing the submitted
  configuration, backend summaries, posterior-distribution figures, merged
  path network, ranked paths, calibration table, and the node/edge evidence
  inspections opened during that browser session.
- Embedded every file from the run directory in a recoverable file vault.
  Large text artifacts are gzip-compressed inside the report, while downloads
  restore the exact original bytes and names. The report records original and
  stored sizes plus a SHA-256 checksum for every artifact.
- Added `session_snapshot.json` inside the vault so the complete job payload,
  evidence registry, client view state, inspection history, and file manifest
  can be extracted without scraping the report markup.
- Added job creation/start/finish timestamps to the local API and a dedicated
  report endpoint. Exporting does not alter or recompute any scientific result.

### Version 1 default analysis profile

- Matched the GUI defaults to the approved Version 1 screenshots: all twelve
  node streams are enabled with their displayed dataset-specific Tq
  multipliers, node nondetections receive BF 0.5, and the prior/cutoff remain
  0.5.
- Enabled every primary edge stream except the mutually exclusive HPA
  high-confidence alternative, enabled fixed scaffold-mediated closure at
  Anchor 0.9 and Support L 0.9, and retained a 0.5 edge prior/cutoff without a
  global unsupported-edge penalty.
- Set the default path query to Aqp2–Prkaca, 100 paths, and six hops. The relay
  profile uses the explicitly selected receptor, regulator, ligand, catalytic,
  GTPase-regulatory, kinase/phosphatase-binding, and curated second-messenger
  roles while leaving broader signaling/binding and adaptor/scaffold roles
  available but unchecked.

## 2026-09-10

### Visual evidence and calibration transparency

- Replaced variable network node size and edge width with a fixed geometry and
  a continuous red–gray–green posterior color scale. This separates evidence
  strength from graph topology and makes similarly supported edges easier to
  compare.
- Connected network selections directly to the completed-run evidence
  inspector. Selecting a node or edge now loads its ledger; selecting a ranked
  path highlights the route and initially inspects its lowest-probability edge.
- Added compact log2(BF) distributions for every active evidence stream. Each
  inspector row reports the selected BF's percentile/tie interval, and its
  detail view marks that BF against all modeled node hypotheses or all unique
  undirected graph pairs from the same run.
- Added readable node and edge calibration tables showing each stream's
  starting and fitted weight plus starting, preferred, and fitted Tq/reference
  multiplier. The existing JSON, TSV, and optimizer-trace outputs remain the
  machine-readable audit source.

## 2026-09-09

### Direction-aware path inference

- Added mapped OmniPath mouse core source-target directions to the propagation
  layer while preserving undirected Bayesian edge-existence probabilities.
- Retained ontology precedence: OmniPath orients only pairs that ontology left
  unresolved and cannot reopen or reverse an ontology-disallowed traversal.
- Kept bidirectional or conflicting evidence unresolved and traversable both
  ways.
- Added a separate GUI control for OmniPath directions and expanded the
  direction audit with source-specific counts, resources, references, and
  ontology/OmniPath conflicts.

### Evidence transparency

- Added a completed-run inspector for any modeled node or unordered edge.
- The inspector reports all registered streams, including disabled streams,
  with support/refute/neutral status, scope, BF, weight, weighted log2-odds
  contribution, normalization, and missing-data handling.
- Added an arithmetic reconciliation between the displayed update ledger and
  the posterior stored by the completed run.

### Predicted-network visualization

- Added a merged network view that overlays up to 50 ranked paths and collapses
  repeated relationships.
- Added Top 5/10/25/all controls, deterministic re-layout, neighborhood
  highlighting, exact node/edge details, and accessible keyboard selection.
- Encoded node and edge evidence magnitude on a capped log-odds scale so highly
  supported relationships remain distinguishable. Green supports, red refutes,
  gray is near the prior, and orange marks nodes without a Bayesian node
  posterior.
- Added arrowheads only where the propagation matrix allows exactly one
  direction. The compact backend payload is saved as `top_path_network.json`.

### Sensitivity analysis

- Added Tq-aware leave-one-evidence-stream-out analysis across all 12 registered
  node streams and all 8 registered edge streams.
- Optional streams use matched add-one contexts; mutually exclusive source
  alternatives use matched replacement contexts.
- Node-stream ablation propagates through node selection, edge rebuilding,
  directionality, and path ranking. Edge-stream ablation rebuilds edges and
  paths with the selected nodes fixed.
- Added conditional Bernoulli KL divergence, cutoff flips, unique-edge and path
  Jaccard measures, path-rank changes, Tq robustness ranges, checkpoints, PNG
  summaries, and machine-readable methods metadata.

### Performance and auditability

- Added an exact-pair cache query path for small dynamic expansions, avoiding a
  multi-gigabyte SQLite cache scan when at most 50,000 pairs are requested.
- Added per-stream edge log-odds contribution collection without changing the
  posterior integration equation.
- Expanded regression coverage for OmniPath precedence/conflicts, merged path
  graphs, evidence reconstruction, all-stream ablation contexts, GUI controls,
  and small incremental-pair cache behavior.

## 2026-08-25

- Added all-stream score-distribution and Tq-response figures.
- Added reproducible Windows-safe PNG and runnable-application release archives.
- Renamed the portable application entry point to `launch.py`.
- Added the complete project summary and explicit Git-versus-OneDrive data
  handoff documentation.

## Earlier development

Earlier scientific decisions—including the 0.5 independent priors, optional
negative evidence, collecting-duct evidence streams, site-centric
phosphoproteomic scoring, regularized positive-control calibration, scaffold
closure, target extension, and temporal path validation—are recorded in the
dated lab notebook and detailed methods files under `docs/`.
# 2026-09-22 — BioGRID edge evidence and shared-partner closure

- Registered BioGRID build 5.0.261 as a positive-only V1 edge stream with
  one expert BF for direct/contact and co-complex reports alike (default 5);
  overlapping tiers are counted once rather than multiplied.
- Preserved HuRI coverage by projecting human BioGRID records to mouse using
  the existing HPA orthology map; rat records use Ensembl release 116 and
  native mouse records use official symbols. BioGRID nonreporting stays neutral.
- Added a one-pass shared-partner closure stream (default likelihood 0.90,
  BF 1.8) for two proteins related to the same third BioGRID partner. It has no
  partner-degree penalty, does not recurse, and retains exact anchor/pair audits.
- Excluded BioGRID self-interactions from serving as the required third protein.
- Added external-target, evidence-inspector, calibration, and ablation support
  plus regression tests and updated methods documentation.
