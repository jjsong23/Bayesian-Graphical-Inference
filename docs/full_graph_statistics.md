# Compact network statistics

## Purpose

This default-enabled stage describes the network produced by one configured
run. It is deliberately limited to a few familiar statistics so the output is
useful without requiring a graph-theory glossary.

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
- the posterior is retained as the edge weight.

## The four node measures

- **Degree:** the number of retained neighbors. High degree identifies a hub in
  this inferred graph.
- **Weighted degree (posterior strength):** the sum of incident edge
  posteriors. It distinguishes nodes with the same number of neighbors but
  different total edge support.
- **Clustering coefficient:** the fraction of possible connections among a
  node's neighbors that are present. High values indicate a tightly connected
  local neighborhood.
- **Betweenness centrality:** the fraction of posterior-weighted shortest routes
  that pass through the node. High values identify potential bridges between
  network regions.

Betweenness treats high-posterior edges as short routes using reliability
distance `-ln(p)`. Exact `p=1` values are clipped to `1-10^-12` only for this
calculation because weighted shortest-path algorithms require positive
distances. The stored posterior is unchanged.

For graphs above 500 nodes, local clustering samples up to 2,000 neighbor pairs
per node and betweenness uses a deterministic source sample. The JSON summary
labels either approximation and records its sample count.

## The four network descriptors

The ordinary overview reports:

- node count;
- edge count;
- graph density;
- number of connected components.

The live GUI and standalone HTML expose up to 100 ranked nodes for each of the
four node measures. Complete per-node values remain available in
`full_graph_node_statistics.tsv.gz`; the graph definition and descriptors are
stored in `full_graph_statistics_summary.json`.

The backend still recognizes the former advanced metric groups when loading
historical configurations. They are intentionally absent from new defaults and
the ordinary interface.

## Returned-path union statistics

Pathfinding produces a second, narrower graph: the exact union of transitions
appearing in every path returned by that run. No extra edge is added merely
because two participating nodes are connected in the full posterior matrix.

The compact interface reports five node rankings for this graph:

- number of returned paths containing the node;
- number of returned paths traversing through the node as an intermediate
  (fixed start and target appearances do not count);
- degree;
- weighted degree;
- betweenness centrality.

The overview reports returned-path count, unique-node count, unique undirected
edge count, and density. The full TSV additionally retains detailed path-use
columns such as internal-path count, path scores, mean position, and directed
in/out degree and strength.

“All returned paths” means all paths returned under the configured Top paths
limit, maximum hops, edge cutoff, intermediate-node rules, and directionality
constraints. It is not an enumeration of every mathematically possible simple
path. Outputs are `found_path_union_node_statistics.tsv.gz` and
`found_path_union_statistics_summary.json`.

## Interpretation cautions

Every statistic inherits the graph's evidence coverage, identifier mapping,
edge dependence, and cutoff. A node may appear central because it is better
studied or because a chosen cutoff retains its edges. These measures rank
network position; they do not prove biological importance, causality, or
physical binding.
