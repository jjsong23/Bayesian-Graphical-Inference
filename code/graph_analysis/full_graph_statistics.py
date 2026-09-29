"""Compute interpretable whole-graph and node-level network statistics.

The Bayesian adjacency matrix contains posterior edge probabilities.  This
module first makes an explicit, thresholded *undirected* existence graph: a
pair is an edge only when its posterior is strictly greater than the selected
cutoff.  Probability remains attached as an edge weight, while
``-log(probability)`` is used as a reliability distance for weighted shortest
path measures.

The implementation deliberately keeps graph description separate from path
inference.  These statistics describe the complete selected graph; they do not
alter node selection, edge posteriors, directionality, or path ranks.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd


SUPPORTED_METRICS = (
    "degree_strength",
    "clustering",
    "betweenness",
    "closeness_harmonic",
    "eigenvector_pagerank",
    "coreness",
    "connectivity",
    "communities",
    "robustness",
)

# The backend continues to understand older advanced configurations, but new
# runs and reports deliberately surface only a compact set of familiar,
# readily interpretable measures.
DEFAULT_METRIC_GROUPS = (
    "degree_strength",
    "clustering",
    "betweenness",
)

FULL_GRAPH_PRESENTATION_METRICS = (
    "degree",
    "posterior_strength",
    "local_clustering_coefficient",
    "betweenness_centrality",
)

PATH_UNION_PRESENTATION_METRICS = (
    "path_participation_count",
    "internal_path_count",
    "degree",
    "posterior_strength",
    "betweenness_centrality",
)

METRIC_LABELS = {
    "degree": "Degree (number of supported neighbors)",
    "degree_fraction": "Degree fraction",
    "posterior_strength": "Weighted degree (sum of incident edge posteriors)",
    "mean_incident_edge_probability": "Mean incident edge probability",
    "local_clustering_coefficient": "Local clustering coefficient",
    "weighted_clustering_coefficient": "Probability-weighted clustering coefficient",
    "betweenness_centrality": "Betweenness centrality (posterior-weighted routes)",
    "closeness_centrality": "Reliability-weighted closeness centrality",
    "harmonic_centrality": "Reliability-weighted harmonic centrality",
    "eigenvector_centrality": "Probability-weighted eigenvector centrality",
    "pagerank": "Probability-weighted PageRank",
    "core_number": "k-core number",
    "component_size": "Connected-component size",
    "articulation_point": "Articulation point",
    "largest_component_loss_if_removed": "Largest-component loss if removed",
    "community_size": "Community size",
    "path_participation_count": "Returned paths containing node",
    "path_participation_fraction": "Fraction of returned paths containing node",
    "internal_path_count": "Returned paths traversing through node (intermediate only)",
    "best_path_rank": "Best path rank containing node (lower is better)",
    "best_path_score": "Best path score containing node",
    "mean_path_score": "Mean score of paths containing node",
    "mean_position_from_start": "Mean position from starting node",
    "mean_position_to_target": "Mean position before target",
    "path_union_in_degree": "Directed in-degree in returned-path union",
    "path_union_out_degree": "Directed out-degree in returned-path union",
    "path_union_in_posterior_strength": "Incoming posterior strength in returned-path union",
    "path_union_out_posterior_strength": "Outgoing posterior strength in returned-path union",
    "directed_path_betweenness_centrality": "Directed betweenness in returned-path union",
}

PATH_UNION_METRICS = DEFAULT_METRIC_GROUPS

GUI_RANKING_LIMIT = 100
LARGE_GRAPH_NODE_THRESHOLD = 500
LARGE_GRAPH_LANDMARK_COUNT = 128
DENSE_GRAPH_LANDMARK_COUNT = 32
DENSE_GRAPH_THRESHOLD = 0.20
CLUSTERING_PAIR_SAMPLE_COUNT = 2000


def _safe_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def build_threshold_graph(
    matrix: pd.DataFrame,
    cutoff: float,
) -> nx.Graph:
    """Return the unique undirected graph strictly above ``cutoff``."""

    if matrix.shape[0] != matrix.shape[1]:
        raise ValueError("edge matrix must be square")
    symbols = matrix.index.astype(str).tolist()
    if symbols != matrix.columns.astype(str).tolist():
        raise ValueError("edge matrix row and column symbols must match in order")
    values = matrix.to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("edge matrix contains non-finite values")
    if ((values < 0.0) | (values > 1.0)).any():
        raise ValueError("edge probabilities must lie in [0, 1]")

    graph = nx.Graph()
    graph.add_nodes_from(symbols)
    upper_i, upper_j = np.triu_indices(len(symbols), k=1)
    probabilities = np.maximum(values[upper_i, upper_j], values[upper_j, upper_i])
    retained = probabilities > float(cutoff)
    for i, j, probability in zip(
        upper_i[retained], upper_j[retained], probabilities[retained]
    ):
        p = float(probability)
        graph.add_edge(
            symbols[int(i)],
            symbols[int(j)],
            probability=p,
            # NetworkX weighted shortest-path algorithms require strictly
            # positive distances. A posterior rounded to exactly 1 therefore
            # receives the smallest meaningful positive reliability cost.
            reliability_distance=-math.log(
                min(max(p, np.finfo(float).tiny), 1.0 - 1e-12)
            ),
        )
    return graph


def _component_annotations(graph: nx.Graph) -> tuple[dict[str, int], dict[str, int]]:
    components = sorted(
        nx.connected_components(graph),
        key=lambda members: (-len(members), min(str(value) for value in members)),
    )
    component_id: dict[str, int] = {}
    component_size: dict[str, int] = {}
    for identifier, members in enumerate(components, start=1):
        for node in members:
            component_id[str(node)] = identifier
            component_size[str(node)] = len(members)
    return component_id, component_size


def _removal_impact(graph: nx.Graph) -> tuple[set[str], dict[str, int]]:
    """Return articulation points and LCC node losses after each removal.

    Non-articulation nodes have zero additional loss.  Only articulation points
    require an explicit removal calculation, keeping this exact measure cheap.
    """

    articulation = {str(node) for node in nx.articulation_points(graph)}
    baseline = max((len(part) for part in nx.connected_components(graph)), default=0)
    impact = {str(node): 0 for node in graph}
    for node in articulation:
        reduced = graph.copy()
        reduced.remove_node(node)
        surviving = max(
            (len(part) for part in nx.connected_components(reduced)), default=0
        )
        impact[node] = max(0, baseline - 1 - surviving)
    return articulation, impact


def _communities(graph: nx.Graph) -> tuple[list[set[str]], float | None]:
    if graph.number_of_edges() == 0:
        return [{str(node)} for node in graph], None
    try:
        groups = nx.community.louvain_communities(
            graph,
            weight="probability",
            seed=0,
        )
    except AttributeError:
        groups = nx.community.greedy_modularity_communities(
            graph,
            weight="probability",
        )
    communities = [set(map(str, group)) for group in groups]
    communities.sort(key=lambda members: (-len(members), min(members)))
    modularity = nx.community.modularity(
        graph,
        communities,
        weight="probability",
    )
    return communities, float(modularity)


def _largest_component_fraction(graph: nx.Graph) -> float:
    if graph.number_of_nodes() == 0:
        return 0.0
    largest = max((len(part) for part in nx.connected_components(graph)), default=0)
    return float(largest / graph.number_of_nodes())


def _robustness_summary(
    graph: nx.Graph,
    fractions: Iterable[float] = (0.01, 0.05, 0.10, 0.20),
    random_replicates: int = 25,
) -> list[dict[str, Any]]:
    """Compare static degree-targeted removal with seeded random removal."""

    node_count = graph.number_of_nodes()
    if node_count == 0:
        return []
    degree_order = [
        node
        for node, _ in sorted(
            graph.degree,
            key=lambda item: (-int(item[1]), str(item[0])),
        )
    ]
    nodes = np.asarray(sorted(map(str, graph.nodes())), dtype=object)
    node_set = set(nodes.tolist())
    rng = np.random.default_rng(0)
    rows: list[dict[str, Any]] = []
    for fraction in fractions:
        removed_count = min(node_count, max(1, int(round(node_count * fraction))))
        targeted = graph.subgraph(
            node_set.difference(degree_order[:removed_count])
        )
        random_values: list[float] = []
        for _ in range(random_replicates):
            sampled = rng.choice(nodes, size=removed_count, replace=False).tolist()
            reduced = graph.subgraph(node_set.difference(sampled))
            random_values.append(_largest_component_fraction(reduced))
        rows.append(
            {
                "fraction_removed": float(fraction),
                "nodes_removed": int(removed_count),
                "targeted_largest_component_fraction_of_survivors": (
                    _largest_component_fraction(targeted)
                ),
                "random_largest_component_fraction_of_survivors_mean": float(
                    np.mean(random_values)
                ),
                "random_largest_component_fraction_of_survivors_sd": float(
                    np.std(random_values, ddof=1)
                    if len(random_values) > 1
                    else 0.0
                ),
                "random_replicates": int(random_replicates),
            }
        )
    return rows


def _large_graph_clustering(
    graph: nx.Graph,
    *,
    pair_samples: int = CLUSTERING_PAIR_SAMPLE_COUNT,
) -> tuple[dict[str, float], dict[str, float]]:
    """Estimate node clustering from reproducible neighbor-pair samples.

    Exact triangle enumeration becomes unnecessarily expensive for the dense
    posterior graphs produced by permissive evidence settings.  For each node
    this estimator samples neighbor pairs with replacement.  The ordinary
    coefficient is the fraction of sampled neighbor pairs that are connected;
    the weighted coefficient applies the same normalized geometric-mean
    definition used by NetworkX.  Low-degree nodes are evaluated exactly.
    """

    ordinary: dict[str, float] = {}
    weighted: dict[str, float] = {}
    max_weight = max(
        (float(data.get("probability", 1.0)) for _, _, data in graph.edges(data=True)),
        default=1.0,
    )
    max_weight = max(max_weight, np.finfo(float).tiny)
    for node_index, node in enumerate(sorted(graph.nodes(), key=str)):
        neighbors = sorted(graph.neighbors(node), key=str)
        degree = len(neighbors)
        if degree < 2:
            ordinary[str(node)] = 0.0
            weighted[str(node)] = 0.0
            continue
        pair_count = degree * (degree - 1) // 2
        if pair_count <= pair_samples:
            pairs = (
                (neighbors[left], neighbors[right])
                for left in range(degree - 1)
                for right in range(left + 1, degree)
            )
            denominator = pair_count
        else:
            rng = np.random.default_rng(node_index)
            sampled_pairs: list[tuple[str, str]] = []
            while len(sampled_pairs) < pair_samples:
                left = int(rng.integers(0, degree))
                right = int(rng.integers(0, degree - 1))
                if right >= left:
                    right += 1
                sampled_pairs.append((neighbors[left], neighbors[right]))
            pairs = iter(sampled_pairs)
            denominator = pair_samples
        connected = 0.0
        weighted_sum = 0.0
        node_weights = graph[node]
        for left, right in pairs:
            if not graph.has_edge(left, right):
                continue
            connected += 1.0
            product = (
                float(node_weights[left].get("probability", 1.0))
                * float(graph[left][right].get("probability", 1.0))
                * float(node_weights[right].get("probability", 1.0))
            ) / (max_weight**3)
            weighted_sum += float(np.cbrt(max(product, 0.0)))
        ordinary[str(node)] = connected / denominator
        weighted[str(node)] = weighted_sum / denominator
    return ordinary, weighted


def _landmark_distance_centralities(
    graph: nx.Graph,
    *,
    landmarks_per_component: int = LARGE_GRAPH_LANDMARK_COUNT,
) -> tuple[dict[str, float], dict[str, float], int]:
    """Approximate weighted closeness and harmonic centrality by landmarks."""

    node_count = graph.number_of_nodes()
    closeness = {str(node): 0.0 for node in graph}
    harmonic = {str(node): 0.0 for node in graph}
    total_landmarks = 0
    for component_index, members in enumerate(
        sorted(
            nx.connected_components(graph),
            key=lambda values: (-len(values), min(map(str, values))),
        )
    ):
        ordered = sorted(members, key=str)
        component_size = len(ordered)
        if component_size <= 1:
            continue
        sample_count = min(landmarks_per_component, component_size)
        if sample_count == component_size:
            landmarks = ordered
        else:
            rng = np.random.default_rng(component_index)
            positions = np.sort(
                rng.choice(component_size, size=sample_count, replace=False)
            )
            landmarks = [ordered[int(position)] for position in positions]
        total_landmarks += len(landmarks)
        distance_sums = {str(node): 0.0 for node in ordered}
        inverse_sums = {str(node): 0.0 for node in ordered}
        observed = {str(node): 0 for node in ordered}
        for landmark in landmarks:
            distances = nx.single_source_dijkstra_path_length(
                graph,
                landmark,
                weight="reliability_distance",
            )
            for node, distance in distances.items():
                if node == landmark or distance <= 0.0:
                    continue
                key = str(node)
                distance_sums[key] += float(distance)
                inverse_sums[key] += 1.0 / float(distance)
                observed[key] += 1
        disconnected_correction = (
            (component_size - 1) / (node_count - 1) if node_count > 1 else 0.0
        )
        for node in ordered:
            key = str(node)
            count = observed[key]
            if count == 0 or distance_sums[key] <= 0.0:
                continue
            mean_distance = distance_sums[key] / count
            closeness[key] = disconnected_correction / mean_distance
            harmonic[key] = inverse_sums[key] * (component_size - 1) / count
    return closeness, harmonic, total_landmarks


def _large_graph_source_count(graph: nx.Graph) -> int:
    """Use fewer landmarks when every traversal touches a dense edge set."""

    return min(
        (
            DENSE_GRAPH_LANDMARK_COUNT
            if nx.density(graph) >= DENSE_GRAPH_THRESHOLD
            else LARGE_GRAPH_LANDMARK_COUNT
        ),
        graph.number_of_nodes(),
    )


def _landmark_unweighted_path_summary(
    graph: nx.Graph,
    *,
    landmark_count: int = LARGE_GRAPH_LANDMARK_COUNT,
) -> tuple[float, int, int]:
    """Estimate mean unweighted distance and return a diameter lower bound."""

    node_count = graph.number_of_nodes()
    if node_count <= 1:
        return 0.0, 0, node_count
    sample_count = min(landmark_count, node_count)
    if sample_count == node_count:
        landmarks = sorted(graph.nodes(), key=str)
    else:
        ordered = np.asarray(sorted(graph.nodes(), key=str), dtype=object)
        rng = np.random.default_rng(0)
        positions = np.sort(rng.choice(node_count, size=sample_count, replace=False))
        landmarks = ordered[positions].tolist()
    distances: list[int] = []
    diameter_lower_bound = 0
    for landmark in landmarks:
        observed = nx.single_source_shortest_path_length(graph, landmark)
        values = [int(value) for node, value in observed.items() if node != landmark]
        distances.extend(values)
        if values:
            diameter_lower_bound = max(diameter_lower_bound, max(values))
    return (
        float(np.mean(distances)) if distances else 0.0,
        int(diameter_lower_bound),
        int(sample_count),
    )


def compute_full_graph_statistics(
    matrix: pd.DataFrame,
    metadata: pd.DataFrame,
    *,
    cutoff: float,
    metrics: Iterable[str] = SUPPORTED_METRICS,
    progress: Callable[[str], None] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    """Compute selected node-level and graph-level statistics.

    Returns ``(node_statistics, graph_summary, robustness_table)``.
    """

    selected = tuple(dict.fromkeys(str(value) for value in metrics))
    unknown = sorted(set(selected).difference(SUPPORTED_METRICS))
    if unknown:
        raise ValueError("unknown graph statistics: " + ", ".join(unknown))
    if progress:
        progress("Building thresholded full graph")
    graph = build_threshold_graph(matrix, cutoff)
    symbols = list(map(str, graph.nodes()))
    frame = pd.DataFrame({"symbol": symbols})
    metadata_columns = [
        column
        for column in ("symbol", "display_symbol", "name", "classes", "node_type")
        if column in metadata.columns
    ]
    if "symbol" in metadata_columns:
        frame = frame.merge(
            metadata[metadata_columns].drop_duplicates("symbol"),
            on="symbol",
            how="left",
            validate="one_to_one",
        )

    if progress:
        progress("Computing graph degree and components")
    component_id, component_size = _component_annotations(graph)
    frame["component_id"] = frame["symbol"].map(component_id).astype(int)
    frame["component_size"] = frame["symbol"].map(component_size).astype(int)

    if "degree_strength" in selected:
        degrees = dict(graph.degree())
        strengths = dict(graph.degree(weight="probability"))
        frame["degree"] = frame["symbol"].map(degrees).astype(int)
        denominator = max(1, graph.number_of_nodes() - 1)
        frame["degree_fraction"] = frame["degree"] / denominator
        frame["posterior_strength"] = frame["symbol"].map(strengths).astype(float)
        frame["mean_incident_edge_probability"] = np.divide(
            frame["posterior_strength"],
            frame["degree"],
            out=np.zeros(len(frame), dtype=float),
            where=frame["degree"].to_numpy() > 0,
        )

    large_graph = graph.number_of_nodes() > LARGE_GRAPH_NODE_THRESHOLD
    clustering_is_approximate = False
    clustering_pair_samples: int | None = None
    if "clustering" in selected:
        if progress:
            progress("Computing local clustering")
        if large_graph:
            local_clustering, weighted_clustering = _large_graph_clustering(graph)
            clustering_is_approximate = True
            clustering_pair_samples = CLUSTERING_PAIR_SAMPLE_COUNT
        else:
            local_clustering = nx.clustering(graph)
            weighted_clustering = nx.clustering(graph, weight="probability")
        frame["local_clustering_coefficient"] = frame["symbol"].map(
            local_clustering
        )
        frame["weighted_clustering_coefficient"] = frame["symbol"].map(
            weighted_clustering
        )

    if "betweenness" in selected:
        if progress:
            progress("Computing reliability-weighted betweenness")
        approximation_sources = (
            _large_graph_source_count(graph)
            if large_graph
            else None
        )
        frame["betweenness_centrality"] = frame["symbol"].map(
            nx.betweenness_centrality(
                graph,
                k=approximation_sources,
                normalized=True,
                weight="reliability_distance",
                seed=0,
            )
        )
    else:
        approximation_sources = None

    distance_centrality_is_approximate = False
    distance_centrality_landmarks: int | None = None
    if "closeness_harmonic" in selected:
        if progress:
            progress("Computing closeness and harmonic centrality")
        if large_graph:
            closeness, harmonic, distance_centrality_landmarks = (
                _landmark_distance_centralities(
                    graph,
                    landmarks_per_component=_large_graph_source_count(graph),
                )
            )
            distance_centrality_is_approximate = True
        else:
            closeness = nx.closeness_centrality(
                graph, distance="reliability_distance"
            )
            harmonic = nx.harmonic_centrality(
                graph, distance="reliability_distance"
            )
        frame["closeness_centrality"] = frame["symbol"].map(
            closeness
        )
        frame["harmonic_centrality"] = frame["symbol"].map(
            harmonic
        )

    if "eigenvector_pagerank" in selected:
        if progress:
            progress("Computing eigenvector centrality and PageRank")
        try:
            eigenvector = nx.eigenvector_centrality(
                graph,
                max_iter=1000,
                tol=1e-10,
                weight="probability",
            )
        except nx.PowerIterationFailedConvergence:
            eigenvector = {node: math.nan for node in graph}
        frame["eigenvector_centrality"] = frame["symbol"].map(eigenvector)
        try:
            pagerank = nx.pagerank(graph, weight="probability")
        except ModuleNotFoundError as exc:
            if exc.name != "scipy":
                raise
            # NetworkX 3.x delegates its public PageRank function to SciPy.
            # Retain an auditable pure-Python fallback for minimal installations.
            from networkx.algorithms.link_analysis.pagerank_alg import (
                _pagerank_python,
            )

            pagerank = _pagerank_python(graph, weight="probability")
        frame["pagerank"] = frame["symbol"].map(pagerank)

    if "coreness" in selected:
        frame["core_number"] = frame["symbol"].map(
            nx.core_number(graph) if graph.number_of_nodes() else {}
        ).fillna(0).astype(int)

    articulation: set[str] = set()
    if "connectivity" in selected:
        if progress:
            progress("Computing articulation and node-removal impact")
        articulation, removal_impact = _removal_impact(graph)
        frame["articulation_point"] = frame["symbol"].isin(articulation)
        frame["largest_component_loss_if_removed"] = frame["symbol"].map(
            removal_impact
        ).astype(int)

    community_count = None
    modularity = None
    if "communities" in selected:
        if progress:
            progress("Detecting probability-weighted communities")
        communities, modularity = _communities(graph)
        community_id: dict[str, int] = {}
        community_size: dict[str, int] = {}
        for identifier, members in enumerate(communities, start=1):
            for node in members:
                community_id[node] = identifier
                community_size[node] = len(members)
        frame["community_id"] = frame["symbol"].map(community_id).astype(int)
        frame["community_size"] = frame["symbol"].map(community_size).astype(int)
        community_count = len(communities)

    robustness_rows: list[dict[str, Any]] = []
    if "robustness" in selected:
        if progress:
            progress("Simulating random and degree-targeted node removal")
        robustness_rows = _robustness_summary(graph)
    robustness = pd.DataFrame(robustness_rows)

    components = list(nx.connected_components(graph))
    largest_nodes = max(components, key=len, default=set())
    largest_graph = graph.subgraph(largest_nodes).copy()
    degrees_array = np.asarray([value for _, value in graph.degree()], dtype=float)
    strengths_array = np.asarray(
        [value for _, value in graph.degree(weight="probability")], dtype=float
    )
    if graph.number_of_edges():
        assortativity = _safe_float(nx.degree_assortativity_coefficient(graph))
        if "local_clustering_coefficient" in frame:
            average_clustering = float(
                frame["local_clustering_coefficient"].mean()
            )
            weighted_average_clustering = float(
                frame["weighted_clustering_coefficient"].mean()
            )
            degree_values = np.asarray(
                [int(graph.degree(node)) for node in frame["symbol"]], dtype=float
            )
            wedges = degree_values * (degree_values - 1.0)
            wedge_total = float(np.sum(wedges))
            transitivity = (
                float(
                    np.sum(
                        frame["local_clustering_coefficient"].to_numpy(dtype=float)
                        * wedges
                    )
                    / wedge_total
                )
                if wedge_total > 0.0
                else 0.0
            )
        else:
            average_clustering = float(nx.average_clustering(graph))
            weighted_average_clustering = float(
                nx.average_clustering(graph, weight="probability")
            )
            transitivity = float(nx.transitivity(graph))
    else:
        assortativity = None
        average_clustering = 0.0
        weighted_average_clustering = 0.0
        transitivity = 0.0

    shortest_path_is_approximate = (
        largest_graph.number_of_nodes() > LARGE_GRAPH_NODE_THRESHOLD
    )
    shortest_path_landmarks: int | None = None
    if largest_graph.number_of_nodes() > 1 and shortest_path_is_approximate:
        average_path_length, diameter, shortest_path_landmarks = (
            _landmark_unweighted_path_summary(
                largest_graph,
                landmark_count=_large_graph_source_count(largest_graph),
            )
        )
    elif largest_graph.number_of_nodes() > 1:
        average_path_length = float(nx.average_shortest_path_length(largest_graph))
        diameter = int(nx.diameter(largest_graph))
    else:
        average_path_length = 0.0
        diameter = 0
    summary = {
        "graph_definition": (
            "Undirected unique-pair graph; edge retained iff posterior probability "
            f"> {float(cutoff):.9g}."
        ),
        "edge_probability_cutoff_exclusive": float(cutoff),
        "selected_metric_groups": list(selected),
        "node_count": int(graph.number_of_nodes()),
        "edge_count": int(graph.number_of_edges()),
        "density": float(nx.density(graph)),
        "connected_component_count": int(len(components)),
        "largest_component_node_count": int(len(largest_nodes)),
        "largest_component_fraction": (
            float(len(largest_nodes) / graph.number_of_nodes())
            if graph.number_of_nodes()
            else 0.0
        ),
        "isolate_count": int(nx.number_of_isolates(graph)),
        "average_degree": float(degrees_array.mean()) if len(degrees_array) else 0.0,
        "median_degree": float(np.median(degrees_array)) if len(degrees_array) else 0.0,
        "maximum_degree": int(degrees_array.max()) if len(degrees_array) else 0,
        "average_posterior_strength": (
            float(strengths_array.mean()) if len(strengths_array) else 0.0
        ),
        "average_clustering_coefficient": average_clustering,
        "weighted_average_clustering_coefficient": weighted_average_clustering,
        "transitivity": transitivity,
        "degree_assortativity": assortativity,
        "largest_component_average_shortest_path_length_unweighted": (
            average_path_length
        ),
        "largest_component_diameter_unweighted": diameter,
        "articulation_point_count": int(len(articulation)),
        "clustering_is_approximate": clustering_is_approximate,
        "clustering_neighbor_pair_samples_per_node": clustering_pair_samples,
        "betweenness_approximation_source_count": approximation_sources,
        "betweenness_is_approximate": bool(approximation_sources is not None),
        "distance_centrality_is_approximate": distance_centrality_is_approximate,
        "distance_centrality_landmark_count": distance_centrality_landmarks,
        "shortest_path_is_approximate": shortest_path_is_approximate,
        "shortest_path_landmark_count": shortest_path_landmarks,
        "diameter_is_lower_bound": shortest_path_is_approximate,
        "community_count": community_count,
        "probability_weighted_modularity": modularity,
        "distance_definition": "reliability distance = -ln(edge posterior probability)",
        "interpretation_warning": (
            "Statistics describe the inferred, thresholded graph and inherit its "
            "missing-data, evidence-dependence, and cutoff assumptions. Centrality "
            "is descriptive, not proof of causal or biological importance."
        ),
    }
    sort_column = "degree" if "degree" in frame else "component_size"
    frame = frame.sort_values(
        [sort_column, "symbol"],
        ascending=[False, True],
        kind="stable",
    ).reset_index(drop=True)
    return frame, summary, robustness


def compute_path_union_node_statistics(
    paths: pd.DataFrame,
    path_edges: pd.DataFrame,
    metadata: pd.DataFrame,
    *,
    progress: Callable[[str], None] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Describe the exact union of every returned path.

    Only transitions that occur in ``path_edges`` are admitted.  The ordinary
    structural measures use the corresponding unique-pair undirected graph;
    direction-aware degree, strength, and betweenness are added from the exact
    source-to-target traversals.  This deliberately does not add chords merely
    because two path nodes are connected in the full posterior matrix.
    """

    if paths.empty or path_edges.empty:
        return pd.DataFrame(), {
            "scope": "union_of_all_returned_paths",
            "returned_path_count": int(len(paths)),
            "node_count": 0,
            "edge_count": 0,
            "directed_transition_count": 0,
            "note": "No returned path edges were available.",
        }

    required_path_columns = {"rank", "path_symbols"}
    required_edge_columns = {
        "source_symbol",
        "target_symbol",
        "edge_probability",
    }
    if not required_path_columns.issubset(paths.columns):
        missing = sorted(required_path_columns.difference(paths.columns))
        raise ValueError("path table is missing columns: " + ", ".join(missing))
    if not required_edge_columns.issubset(path_edges.columns):
        missing = sorted(required_edge_columns.difference(path_edges.columns))
        raise ValueError("path-edge table is missing columns: " + ", ".join(missing))

    ordered_nodes: list[str] = []
    seen_nodes: set[str] = set()
    membership_rows: list[dict[str, Any]] = []
    score_column = (
        "primary_path_score"
        if "primary_path_score" in paths.columns
        else "geometric_mean_edge_probability"
    )
    for row in paths.itertuples(index=False):
        symbols = [
            item.strip()
            for item in str(getattr(row, "path_symbols")).split("->")
            if item.strip()
        ]
        rank = int(getattr(row, "rank"))
        score = float(getattr(row, score_column))
        last_position = len(symbols) - 1
        for position, symbol in enumerate(symbols):
            if symbol not in seen_nodes:
                ordered_nodes.append(symbol)
                seen_nodes.add(symbol)
            membership_rows.append(
                {
                    "symbol": symbol,
                    "path_rank": rank,
                    "path_score": score,
                    "position_from_start": position,
                    "position_to_target": last_position - position,
                    "is_internal": 0 < position < last_position,
                }
            )

    node_index = {symbol: index for index, symbol in enumerate(ordered_nodes)}
    matrix_values = np.zeros((len(ordered_nodes), len(ordered_nodes)), dtype=float)
    directed = nx.DiGraph()
    directed.add_nodes_from(ordered_nodes)
    for row in path_edges.itertuples(index=False):
        source = str(getattr(row, "source_symbol"))
        target = str(getattr(row, "target_symbol"))
        if source not in node_index or target not in node_index or source == target:
            continue
        probability = float(getattr(row, "edge_probability"))
        if not math.isfinite(probability) or not 0.0 < probability <= 1.0:
            raise ValueError("returned path edge probabilities must lie in (0, 1]")
        left = node_index[source]
        right = node_index[target]
        matrix_values[left, right] = max(matrix_values[left, right], probability)
        matrix_values[right, left] = max(matrix_values[right, left], probability)
        existing = directed.get_edge_data(source, target, default={}).get(
            "probability", 0.0
        )
        if probability >= float(existing):
            directed.add_edge(
                source,
                target,
                probability=probability,
                reliability_distance=-math.log(
                    min(max(probability, np.finfo(float).tiny), 1.0 - 1e-12)
                ),
            )

    union_matrix = pd.DataFrame(
        matrix_values,
        index=ordered_nodes,
        columns=ordered_nodes,
    )
    node_statistics, summary, _ = compute_full_graph_statistics(
        union_matrix,
        metadata,
        cutoff=0.0,
        metrics=PATH_UNION_METRICS,
        progress=progress,
    )

    membership = pd.DataFrame(membership_rows)
    participation = (
        membership.groupby("symbol", sort=False)
        .agg(
            path_participation_count=("path_rank", "nunique"),
            internal_path_count=("is_internal", "sum"),
            best_path_rank=("path_rank", "min"),
            best_path_score=("path_score", "max"),
            mean_path_score=("path_score", "mean"),
            mean_position_from_start=("position_from_start", "mean"),
            mean_position_to_target=("position_to_target", "mean"),
        )
        .reset_index()
    )
    participation["path_participation_fraction"] = (
        participation["path_participation_count"] / len(paths)
    )
    node_statistics = node_statistics.merge(
        participation,
        on="symbol",
        how="left",
        validate="one_to_one",
    )
    node_statistics["path_union_in_degree"] = node_statistics["symbol"].map(
        dict(directed.in_degree())
    ).astype(int)
    node_statistics["path_union_out_degree"] = node_statistics["symbol"].map(
        dict(directed.out_degree())
    ).astype(int)
    node_statistics["path_union_in_posterior_strength"] = node_statistics[
        "symbol"
    ].map(dict(directed.in_degree(weight="probability"))).astype(float)
    node_statistics["path_union_out_posterior_strength"] = node_statistics[
        "symbol"
    ].map(dict(directed.out_degree(weight="probability"))).astype(float)
    approximation_sources = (
        min(128, directed.number_of_nodes())
        if directed.number_of_nodes() > 500
        else None
    )
    node_statistics["directed_path_betweenness_centrality"] = node_statistics[
        "symbol"
    ].map(
        nx.betweenness_centrality(
            directed,
            k=approximation_sources,
            normalized=True,
            weight="reliability_distance",
            seed=0,
        )
    )

    unordered_pairs = {
        tuple(sorted((str(source), str(target))))
        for source, target in directed.edges()
    }
    bidirectional_pairs = sum(
        directed.has_edge(left, right) and directed.has_edge(right, left)
        for left, right in unordered_pairs
        if left != right
    )
    summary.update(
        {
            "scope": "union_of_all_returned_paths",
            "graph_definition": (
                "Exact union of transitions occurring in every returned path; "
                "no other edge from the posterior matrix is included."
            ),
            "returned_path_count": int(len(paths)),
            "unique_node_count": int(len(ordered_nodes)),
            "unique_undirected_path_edge_count": int(len(unordered_pairs)),
            "directed_transition_count": int(directed.number_of_edges()),
            "bidirectional_pair_count": int(bidirectional_pairs),
            "directed_betweenness_approximation_source_count": (
                approximation_sources
            ),
            "limited_by_path_search_settings": True,
            "interpretation_warning": (
                "These statistics describe all paths returned by this run, not "
                "every mathematically possible path. They depend on Top paths, "
                "maximum hops, edge cutoff, and propagation constraints."
            ),
        }
    )
    node_statistics = node_statistics.sort_values(
        ["path_participation_count", "degree", "symbol"],
        ascending=[False, False, True],
        kind="stable",
    ).reset_index(drop=True)
    return node_statistics, summary


def top_node_statistics(
    frame: pd.DataFrame,
    *,
    limit: int = GUI_RANKING_LIMIT,
    metrics: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a compact GUI payload for the requested numeric measures.

    Passing an explicit presentation list keeps the live GUI and standalone
    reports focused while preserving compatibility with richer historical
    tables.  ``None`` retains the older all-numeric behavior for callers that
    explicitly need it.
    """

    excluded = {"component_id", "community_id"}
    numeric_candidates = [
        column
        for column in frame.columns
        if column not in excluded
        and column not in {"symbol", "display_symbol", "name", "classes", "node_type"}
        and (
            pd.api.types.is_numeric_dtype(frame[column])
            or pd.api.types.is_bool_dtype(frame[column])
        )
    ]
    if metrics is None:
        candidates = numeric_candidates
    else:
        allowed = set(numeric_candidates)
        candidates = [str(metric) for metric in metrics if str(metric) in allowed]
    rankings: dict[str, list[dict[str, Any]]] = {}
    for column in candidates:
        lower_is_better = column in {"best_path_rank"}
        ranked = frame.sort_values(
            [column, "symbol"],
            ascending=[lower_is_better, True],
            kind="stable",
        ).head(limit)
        records = []
        for row in ranked.itertuples(index=False):
            value = getattr(row, column)
            records.append(
                {
                    "symbol": str(getattr(row, "symbol")),
                    "name": str(getattr(row, "name", "") or ""),
                    "value": bool(value) if isinstance(value, (bool, np.bool_)) else float(value),
                }
            )
        rankings[column] = records
    return {
        "available_metrics": [
            {"id": column, "label": METRIC_LABELS.get(column, column.replace("_", " ").title())}
            for column in candidates
        ],
        "top_nodes_by_metric": rankings,
        "display_limit": int(limit),
    }
