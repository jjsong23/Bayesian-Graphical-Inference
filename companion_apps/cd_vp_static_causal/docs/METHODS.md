# Methods and design decisions

## Whole-network scope

The source registry is `supported_edges.tsv.gz` plus
`full_graph_node_statistics.tsv.gz` from the newest complete V1 GUI run. No
source, target, AQP2, or displayed-path filter is applied. The registry keeps
the V1 edge posterior as evidence metadata. Because that export is undirected,
direction is introduced only inside named candidate hypotheses. The default
hypotheses apply biological-class ordering at two posterior thresholds and one
reverse-tier sensitivity orientation. This produces DAGs without claiming that
V1 established direction.

## Measurements

Each phosphosite is separately registered. For graph-node tests, a protein is
mapped to its most complete site and then the site with the largest mean
absolute response. Multiple-site mappings are labelled
`measurement_sensitive_representative_site`; they are never described as exact
protein measurements. The measurement is log2(dDAVP / time-matched vehicle).

## Query bank

One deterministic bounded bank is shared by every candidate. It prioritizes
observed endpoint pairs whose edge assertions differ across candidates, then
adds conditioning sets from observed local neighborhoods. The default limit is
80 and `|S| <= 2`. Latent or fixed variables are forbidden as ordinary
conditioning variables.

## Cross-regime restrictions

Raw replicate-level intact data are eligible. Local KO workbooks are audited,
but summary contrasts are not treated as replicate covariance matrices. When
raw comparable KO data are unavailable, invariance rows say `unavailable`.
Descendant nonresponse is never treated as proof, and a nondescendant versus
unrestricted comparison is recorded only as a one-sided opportunity.

## Knockout design

Every registered node is feasible by default. Each graph undergoes exact hard
intervention surgery. Pairwise discrimination requires at least one shared
testable query with opposite graph predictions and is marked as requiring
faithfulness. `N_unique` counts candidate graphs that become singleton
prediction classes. `W_unique` sums their normalized compatibility mass.
One-sided invariance opportunities receive no singleton credit. Greedy batches
optimize `N_unique` first and `W_unique` second.

Knockout ranking uses the first `ko_query_budget` entries of the same shared
query bank (16 by default) so whole-registry design remains tractable. This is
reported as bounded coverage, never as a claim of full interventional
equivalence.

## Limitations

Twelve samples make many conditional tests low-powered. The default graph
orientations are hypotheses, not causal facts. Composite scores reuse dependent
queries. KO summaries do not support covariance invariance. A general causal
effect is symbolically identified with the pinned `y0` ID/IDC engine, but is
not reported as numerically estimated without adequate registered data sources.
`y0` does not expose an internal rule-by-rule certificate for these calls, so
the audit records the algorithm, version, ADMG graph signature, expression, and
an explicit trace-unavailable statement.

