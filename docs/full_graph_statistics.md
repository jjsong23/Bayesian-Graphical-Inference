# Full-graph descriptive statistics

## Purpose

This default-enabled stage describes the complete graph produced by one configured
run. It was motivated by the protein-network discussion in [Graph Theory in
Biology](https://learngraphtheory.org/articles/graph-theory-biology.html),
especially its distinction between hubs (degree), bridges (betweenness), local
modules (clustering), global reachability (components/path length/diameter), and
robustness to random versus targeted node removal.

These values are **descriptive outputs**, not evidence streams. They never
change a node posterior, edge posterior, propagation direction, or path rank.

## Graph definition

The input is the symmetric posterior edge matrix from the configured run. The
analysis constructs a simple undirected graph:

- every selected graph node is retained, including isolates;
- every unordered pair appears at most once;
- self-edges are excluded;
- an edge is retained only when its posterior is **strictly greater than** the
  graph-statistics cutoff;
- the posterior is retained as edge weight `p`;
- weighted shortest-path measures use reliability distance `-ln(p)`.

The final detail makes a high-probability edge a short distance. Exact `p=1`
values are clipped to `1-10^-12` only for this distance calculation, because
weighted shortest-path algorithms require positive distances. The stored edge
posterior is not changed.

## Node-level measures

- **Degree:** number of supported neighbors. This is the direct hub measure.
- **Degree fraction:** degree divided by the maximum possible degree.
- **Posterior strength:** sum of incident edge posteriors. This distinguishes
  nodes with the same degree but different total edge support.
- **Mean incident posterior:** average posterior among retained incident edges.
- **Clustering coefficient:** fraction of possible neighbor-neighbor triangles
  that are present. The weighted form also accounts for edge posterior.
- **Betweenness centrality:** fraction of reliability-weighted shortest paths
  that pass through the node. High values flag candidate bridges/cross-talk.
  It is exact through 500 nodes; larger graphs use a deterministic landmark
  approximation (32 sources when density is at least 0.20, otherwise up to
  128) and record the actual source count in the JSON summary.
- **Closeness centrality:** inverse reliability-weighted distance to the graph.
- **Harmonic centrality:** a disconnected-graph-friendly reachability measure.
- **Eigenvector centrality and PageRank:** connection to other central nodes,
  weighted by edge posterior.
- **Core number:** deepest `k`-core containing the node.
- **Component ID and size:** disconnected-subgraph membership.
- **Articulation point:** whether deleting the node increases the component
  count.
- **Largest-component loss if removed:** additional nodes separated from the
  largest surviving component by removing that node.
- **Community ID and size:** probability-weighted Louvain module assignment
  (with a greedy-modularity fallback for older NetworkX versions).

## Whole-graph measures

The JSON summary reports node/edge counts, density, components, largest
component, isolates, degree and strength summaries, average clustering,
weighted clustering, transitivity, degree assortativity, unweighted average
shortest-path length and diameter within the largest component, community
count, weighted modularity, and articulation count.

The optional robustness table removes 1%, 5%, 10%, and 20% of nodes. It compares
a static descending-degree removal order with 25 deterministic random-removal
replicates and reports the largest surviving component as a fraction of
surviving nodes. It is a sensitivity description, not a knockout prediction.

For graphs above 500 nodes, computationally costly measures are explicitly
approximated rather than silently omitted: local/weighted clustering samples
up to 2,000 neighbor pairs per node; betweenness and weighted
closeness/harmonic centrality use deterministic landmarks; unweighted mean
path length uses landmark-to-all distances; and the reported diameter is a
sampled lower bound. Dense graphs use 32 landmarks and other large graphs use
up to 128. The JSON includes separate approximation flags and sample counts.
Exact measures remain in use for graphs with at most 500 nodes.

The live GUI and standalone HTML expose up to 100 ranked nodes for every
available measure, plus top-10 at-a-glance panels. Complete per-node values,
not only the displayed rankings, remain available in the TSV outputs.

## Interpretation cautions

Every statistic inherits the graph's evidence coverage, identifier mapping,
edge-dependence, and cutoff. A node can appear central because its protein is
better studied, because a database is denser in its neighborhood, or because a
chosen cutoff happens to retain its edges. Centrality is therefore a
hypothesis-ranking feature—not evidence that a protein is essential, causal,
or physically bound to all of its inferred neighbors. Re-run the description
at multiple cutoffs before reporting a ranking as robust.

## Outputs

- `full_graph_node_statistics.tsv.gz`: one row per selected node.
- `full_graph_statistics_summary.json`: graph definition and global measures.
- `full_graph_robustness.tsv`: removal-sensitivity results when enabled.

## Returned-path union statistics

Pathfinding produces a second, deliberately narrower graph. It is the exact
union of transitions appearing in every path returned by that run. No chord or
other edge is added merely because two participating nodes are connected in
the full posterior matrix. Its per-node output includes the structural
measures above plus path participation count and fraction, intermediate-path
count, best and mean associated path score, mean position from each endpoint,
directed in/out degree and strength, and directed reliability-weighted
betweenness.

The phrase “all returned paths” is important: this graph is conditioned on the
configured Top paths limit, maximum hops, edge cutoff, intermediate-node rules,
and directionality constraints. It is not an enumeration of every possible
simple path. Outputs are `found_path_union_node_statistics.tsv.gz` and
`found_path_union_statistics_summary.json`.
