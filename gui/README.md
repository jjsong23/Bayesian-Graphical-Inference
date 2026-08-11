# Graphical Bayesian Inference workbench

This local browser application runs the project from node selection through
edge integration, external-target extension, and ranked path finding. It is
local by design: arbitrary target characterization reads the project's raw
mouse datasets and executes the audited Python analysis code on this computer.

## Start

From PowerShell:

```powershell
cd C:\Users\songjj\Documents\Codex\2026-07-21\un\graphical_bayesian_inference
.\gui\run_workbench.ps1
```

The workbench opens at `http://127.0.0.1:8765`. It binds only to the local
loopback interface by default.

## Workflow

1. **Select nodes.** Enable any subset of protein abundance, principal-cell
   RNA, kinase activity, phosphoprotein response, and the six lab-specific
   collecting-duct streams (proteome and RNA for CCD, OMCD, and IMCD). Each enabled
   factor can be raised to a nonnegative user weight. Every protein is an
   independent present-versus-absent hypothesis with prior probability 0.5 by
   default. Weighted Bayes factors update its odds without normalization across
   proteins. Proteins whose posterior is strictly above the selected node
   cutoff (default 0.5) are retained; curated second messengers can be included
   separately. Candidates outside the validated 891-node seed are inserted
   after incremental edge characterization. Every node dataset also has its
   own `Tq ×` control.
   The six collecting-duct streams are off in the generic default so the
   validated 891-node baseline remains exactly reproducible.
2. **Characterize edges.** Enable any subset of mpkCCD localization,
   KinasePredictor, STRING, HPA, OmniPath, STITCH, and the optional derived
   scaffold-closure stream. Edge prior odds are multiplied by
   each sparse Bayes factor raised to its selected weight. HPA primary and HPA
   high-confidence are alternatives and cannot be enabled together. Each edge
   dataset has an independent normalization control. Scaffold closure is off by
   default because it reuses the selected pre-closure graph rather than adding
   an independent experiment.
3. **Find paths.** Choose a selected start node and any mouse protein target.
   Targets outside the node universe are characterized from the same evidence
   streams and appended as an undirected row/column. Paths are ranked by the
   product of their edge probabilities. The path panel exposes every
   ontology-derived node-role class and the GO root term(s) used to assign it.
   Users select which roles may serve as internal signal relays. The
   sensitivity-oriented default enables ligand, binding, generic
   signaling-process, catalytic, regulatory, and second-messenger classes;
   `adaptor_scaffold` is available but disabled. Multi-role nodes qualify when
   any selected class matches. The optional scaffold override still removes
   every scaffold-tagged intermediate, even if another selected role matches.

The path count can be set from 1 to 500. All requested paths are shown in the
scrollable result table and written to `ranked_paths.tsv`; the separate
`ranked_path_edges.tsv` retains every constituent edge. The liberal intermediate
policy retains a substantially denser search graph than the earlier conservative
policy, so large path requests can take considerably longer. Runtime varies with
graph size, edge cutoff, hop limit, and endpoint connectivity.

Each run writes a new immutable folder under `results/gui_runs/` containing the
configuration, node posterior table, selected-node universe, adjacency matrix,
supported edge list, path tables, eligibility audit, and JSON summary.
`configuration.json` and `analysis_summary.json` retain the exact selected
ontology-class IDs, while `intermediate_node_eligibility.tsv` reports every
node's complete class list, matching selected roles, endpoint exemption, and
final eligibility decision.

## Per-dataset normalization

`Tq × = 1.00` reproduces the finalized analysis for any enabled stream. A value below 1 lowers a
dataset's threshold and increases sensitivity; a value above 1 raises the
threshold and is more stringent. Scalar thresholds (protein, RNA, kinase
activity, the six collecting-duct abundance streams, and OmniPath) and
observation-specific thresholds (phosphoprotein,
mpkCCD localization, KinasePredictor, and HPA) are all recomputed from their
stored raw statistics. The multiplier is applied to every threshold belonging
to that dataset before its likelihood and Bayes factor are recalculated.

STRING and STITCH are the exceptions: their integrations use score odds rather
than the Gaussian complement kernel. STRING uses the odds of the
STRING combined score relative to a reference score of 0.041, not the Gaussian
complement kernel. STITCH messenger–protein associations use score odds relative
to the database's 0.150 reporting floor. Their cards are labeled `Ref ×`; each
control scales that stream's reference score rather than calling it `Tq`.

Scaffold-mediated closure is calculated in a separate second pass. A protein is
an anchor to an `adaptor_scaffold` node only when its pre-closure edge
probability exceeds the card's `Anchor >` setting (default 0.90). If two
proteins have qualifying edges to at least one shared scaffold, the pair gets
the fixed `Support L` likelihood (default 0.90), equivalent to BF 1.8 relative
to neutral likelihood 0.50. All other pairs remain neutral. Scaffold degree,
anchor strength above the cutoff, and the number of shared scaffolds do not
alter the factor. No empirical Tq is calculated. Closure-derived edges are
never fed back as new anchors. Every qualifying pair, its complete supporting-
scaffold list, and its before/after probability are written to
`scaffold_triadic_closure_audit.tsv.gz` in that run's output folder.

Node factors are recalculated for all 9,170 candidates. The validated 891-node
graph is retained as an immutable seed, while newly selected proteins are
characterized against every selected node and inserted into the graph. Raw
statistics for each new unordered pair are stored in
`data/edge_characterization/incremental_edge_cache/edge_pair_cache.sqlite3`.
Later runs reuse those rows and only characterize pairs that have never been
seen under the current evidence-source signature. Changes to weights or
normalization controls rescore cached raw statistics without rereading the
large source datasets.

New node-specific localization thresholds use the fixed 891-node background,
so adding more nodes does not silently alter an earlier pair. Dynamic
KinasePredictor evidence uses global top-ten model rank for the same reason.
The run summary reports requested pairs, cache hits, newly characterized pairs,
source signature, and database totals.

The collecting-duct extension derives each segment's q75 from positive values
only; zeros are nondetections and stay neutral. Rat proteome rows are mapped to
mouse genes with Ensembl release 116 orthology (UniProt first, rat-symbol
fallback), and every mapping decision is audited. Enabling all six new streams
in addition to the existing four selects 3,296 proteins, or 3,316 nodes after
the 20 curated second messengers. The three segment streams within each assay
share a source experiment and are tagged as dependent even though they remain
separately selectable as requested.

## Extending evidence streams

The GUI is driven by `evidence_registry.json`. A new precomputed stream needs:

- a unique ID, label, and description;
- its factor-table path and factor column;
- its neutral value for node evidence, or target-factor column for edge
  evidence;
- default enabled state and weight; and
- normalization handler, label, reference, help text, and default multiplier;
- an optional exclusivity or dependence group.

The interface renders registry entries automatically. The engine's combination
logic is generic for positive factors. A stream requiring a new raw-data
transformation should first receive an audited preprocessing module that emits
the standard factor table.

## Scientific safeguards

- Missing sparse edge evidence is neutral (`BF = 1`), not evidence of absence.
- Node posteriors are independent Bernoulli probabilities and are not
  normalized across the 9,170 protein candidates.
- Protein/RNA source scores use 0.5 as their neutral likelihood floor and are
  divided by 0.5 before integration, making neutral evidence BF = 1.
- Edge probabilities are independent Bernoulli updates from the selected prior.
- Kinase activity and phosphoprotein response share a source and are visibly
  marked as dependent.
- HPA alternatives are mutually exclusive.
- Path products are ranking scores, not calibrated whole-path probabilities.
- Every run preserves its exact configuration and complete audit tables.
- Scaffold closure is labeled as dependent proximity/co-complex evidence, not
  proof of a direct binary PPI, and is disabled by default.
- Every active stream records its normalization or fixed-rule parameters in
  the run summary.
