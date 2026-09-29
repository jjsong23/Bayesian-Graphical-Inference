"""Build a self-contained, offline HTML record of one completed GUI run.

The report is deliberately independent of the local HTTP server.  It embeds
the submitted configuration, backend result payload, inspected-evidence
history, human-readable summaries, compact SVG figures, and a compressed vault
containing every file produced in the run directory.  Vault downloads restore
the original bytes; SHA-256 hashes make that claim independently checkable.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import html
import json
import math
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPORT_PREFIX = "complete_session_"
REPORT_SCHEMA_VERSION = 4

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


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _number(value: Any, digits: int = 4) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number):
        return "—"
    return f"{number:.{digits}g}"


def _integer(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _percentile(position: dict[str, Any] | None) -> str:
    if not position:
        return "—"
    lower = _number(position.get("lower_percentile"), 3)
    upper = _number(position.get("upper_percentile"), 3)
    exactness = "exact" if position.get("exact") else "estimated"
    return f"{lower}–{upper}% ({exactness})"


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{int(value)} B"


def _safe_report_name(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(run_id)).strip("._") or "run"
    return f"{REPORT_PREFIX}{safe}.html"


def _inspection_key(item: dict[str, Any]) -> str:
    if item.get("kind") == "node":
        return f"node:{str(item.get('symbol', '')).casefold()}"
    left, right = sorted(
        (str(item.get("node_a", "")), str(item.get("node_b", ""))),
        key=str.casefold,
    )
    return f"edge:{left.casefold()}|{right.casefold()}"


def _json_pre(value: Any) -> str:
    text = json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return f"<pre>{_escape(text)}</pre>"


def _metric_card(label: str, value: Any, note: str = "") -> str:
    return (
        '<article class="metric"><span>'
        + _escape(label)
        + "</span><strong>"
        + _escape(value)
        + "</strong><small>"
        + _escape(note)
        + "</small></article>"
    )


def _observed_probability_range(values: Iterable[Any]) -> tuple[float, float] | None:
    probabilities: list[float] = []
    for value in values:
        try:
            probability = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(probability):
            probabilities.append(min(1.0, max(0.0, probability)))
    if not probabilities:
        return None
    return min(probabilities), max(probabilities)


def _probability_color(
    value: Any,
    available: bool = True,
    observed_range: tuple[float, float] | None = None,
    palette: str = "edge",
) -> str:
    if not available:
        return "#d98d56"
    try:
        probability = min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return "#8c9690"
    if observed_range is not None:
        minimum, maximum = observed_range
        span = maximum - minimum
        if span <= 1e-15:
            fraction = 0.5
        else:
            fraction = min(1.0, max(0.0, (probability - minimum) / span))
        if palette == "node":
            start, end = (220, 234, 245), (23, 63, 102)
        else:
            start, end = (216, 235, 225), (13, 59, 42)
    elif probability < 0.5:
        fraction = probability / 0.5
        start, end = (177, 62, 55), (139, 149, 143)
    else:
        fraction = (probability - 0.5) / 0.5
        start, end = (139, 149, 143), (38, 101, 71)
    rgb = tuple(round(a + (b - a) * fraction) for a, b in zip(start, end))
    return "#%02x%02x%02x" % rgb


def _histogram_svg(distribution: dict[str, Any] | None, title: str) -> str:
    if not distribution or not distribution.get("bin_counts"):
        return '<div class="empty-figure">Distribution unavailable</div>'
    counts = [max(0, int(value)) for value in distribution["bin_counts"]]
    edges = [float(value) for value in distribution.get("bin_edges", [])]
    if len(edges) != len(counts) + 1:
        return '<div class="empty-figure">Distribution bins are malformed</div>'
    width, height = 720, 260
    left, top, bottom, right = 52, 18, 42, 16
    plot_width = width - left - right
    plot_height = height - top - bottom
    transformed = [math.log10(value + 1) for value in counts]
    maximum = max(transformed, default=1.0) or 1.0
    bars: list[str] = []
    for index, (count, magnitude) in enumerate(zip(counts, transformed)):
        x = left + index * plot_width / len(counts)
        bar_width = max(0.8, plot_width / len(counts) - 1)
        bar_height = magnitude / maximum * plot_height
        midpoint = (edges[index] + edges[index + 1]) / 2
        bars.append(
            f'<rect x="{x:.2f}" y="{top + plot_height - bar_height:.2f}" '
            f'width="{bar_width:.2f}" height="{bar_height:.2f}" '
            f'fill="{_probability_color(midpoint)}"><title>{count:,} hypotheses; '
            f'{edges[index]:.3f}–{edges[index + 1]:.3f}</title></rect>'
        )
    cutoff = float(distribution.get("output_probability_cutoff_exclusive", 0.5))
    cutoff_x = left + min(1.0, max(0.0, cutoff)) * plot_width
    prior = float(distribution.get("prior_probability", 0.5))
    prior_x = left + min(1.0, max(0.0, prior)) * plot_width
    return f"""
      <figure class="figure-card">
        <figcaption><strong>{_escape(title)}</strong><span>{_integer(distribution.get('hypothesis_count'))} hypotheses</span></figcaption>
        <svg viewBox="0 0 {width} {height}" role="img" aria-label="{_escape(title)}">
          <line x1="{left}" y1="{top + plot_height}" x2="{width-right}" y2="{top + plot_height}" class="axis"/>
          {''.join(bars)}
          <line x1="{prior_x:.2f}" y1="{top}" x2="{prior_x:.2f}" y2="{top+plot_height}" class="prior-line"><title>Prior {prior:.4g}</title></line>
          <line x1="{cutoff_x:.2f}" y1="{top}" x2="{cutoff_x:.2f}" y2="{top+plot_height}" class="cutoff-line"><title>Output cutoff {cutoff:.4g}</title></line>
          <text x="{left}" y="{height-13}" class="axis-label">0</text>
          <text x="{left + plot_width/2}" y="{height-13}" text-anchor="middle" class="axis-label">Posterior probability</text>
          <text x="{width-right}" y="{height-13}" text-anchor="end" class="axis-label">1</text>
        </svg>
        <dl class="figure-stats">
          <div><dt>Minimum</dt><dd>{_number(distribution.get('minimum'))}</dd></div>
          <div><dt>Mean</dt><dd>{_number(distribution.get('mean'))}</dd></div>
          <div><dt>Maximum</dt><dd>{_number(distribution.get('maximum'))}</dd></div>
          <div><dt>Below prior</dt><dd>{_integer(distribution.get('below_prior_count'))}</dd></div>
          <div><dt>At prior</dt><dd>{_integer(distribution.get('at_exact_prior_count'))}</dd></div>
          <div><dt>Above cutoff</dt><dd>{_integer(distribution.get('above_output_cutoff_count'))}</dd></div>
        </dl>
      </figure>
    """


def _network_svg(
    network: dict[str, Any] | None, rank_limit: int | None = None
) -> str:
    if not network or not network.get("nodes"):
        return '<div class="empty-figure">Path network was not generated.</div>'
    available = int(network.get("visualized_path_count") or 0)
    effective_limit = min(max(int(rank_limit or min(10, available)), 0), available)

    def visible(item: dict[str, Any]) -> bool:
        ranks = [int(value) for value in item.get("path_ranks", [])]
        return not ranks or any(rank <= effective_limit for rank in ranks)

    nodes = [item for item in network.get("nodes", []) if visible(item)]
    visible_ids = {str(item.get("id", "")) for item in nodes}
    edges = [
        item
        for item in network.get("edges", [])
        if visible(item)
        and str(item.get("node_a", "")) in visible_ids
        and str(item.get("node_b", "")) in visible_ids
    ]
    node_range = _observed_probability_range(
        [
            node.get("posterior_probability")
            for node in nodes
            if bool(node.get("posterior_available", False))
        ]
    )
    edge_range = _observed_probability_range(
        [edge.get("edge_probability") for edge in edges]
    )
    width, height = 1020, max(430, min(820, 300 + len(nodes) * 7))
    left, right, top, bottom = 70, 190, 45, 45
    buckets: dict[int, list[dict[str, Any]]] = {}
    for node in nodes:
        position = float(node.get("mean_path_position", 0.5))
        bucket = min(12, max(0, round(position * 12)))
        buckets.setdefault(bucket, []).append(node)
    positions: dict[str, tuple[float, float]] = {}
    node_buckets: dict[str, int] = {}
    for bucket, members in buckets.items():
        members.sort(key=lambda item: (int(item.get("best_path_rank", 10**9)), str(item.get("id"))))
        for index, node in enumerate(members, 1):
            anchor_x = left + bucket / 12 * (width - left - right)
            spacing = min(34.0, 100.0 / max(1, len(members) - 1))
            offset = (index - 1 - (len(members) - 1) / 2) * spacing
            x = min(width - right, max(left, anchor_x + offset))
            y = top + index / (len(members) + 1) * (height - top - bottom)
            node_id = str(node.get("id"))
            positions[node_id] = (x, y)
            node_buckets[node_id] = bucket
    edge_marks: list[str] = []
    for edge in edges:
        source_id = str(edge.get("source", edge.get("node_a", "")))
        target_id = str(edge.get("target", edge.get("node_b", "")))
        if source_id not in positions or target_id not in positions:
            continue
        x1, y1 = positions[source_id]
        x2, y2 = positions[target_id]
        probability = edge.get("edge_probability")
        color = _probability_color(
            probability, observed_range=edge_range, palette="edge"
        )
        marker = ' marker-end="url(#arrow)"' if edge.get("directionality") == "uniquely_directed" else ""
        tooltip = (
            f"{edge.get('node_a')} — {edge.get('node_b')}; posterior "
            f"{_number(probability)}; {edge.get('directionality', 'unresolved')}"
        )
        evidence_key = _inspection_key(
            {"kind": "edge", "node_a": edge.get("node_a"), "node_b": edge.get("node_b")}
        )
        dx, dy = x2 - x1, y2 - y1
        distance = max((dx * dx + dy * dy) ** 0.5, 0.01)
        route_hash = int(
            hashlib.sha256(evidence_key.encode("utf-8")).hexdigest()[:8], 16
        )
        same_tier = node_buckets.get(source_id) == node_buckets.get(target_id)
        bend = (42 + route_hash % 39) if same_tier else (10 + route_hash % 17)
        direction = 1 if route_hash % 2 else -1
        control_x = (x1 + x2) / 2 - dy / distance * bend * direction
        control_y = (y1 + y2) / 2 + dx / distance * bend * direction
        path_data = (
            f"M{x1:.2f},{y1:.2f} Q{control_x:.2f},{control_y:.2f} "
            f"{x2:.2f},{y2:.2f}"
        )
        edge_marks.append(
            f'<g class="inspectable-mark" data-interpretation-key="{_escape(evidence_key)}" tabindex="0" role="button">'
            f'<path d="{path_data}" fill="none" stroke="{color}" stroke-width="3.2" opacity="0.82"{marker}><title>{_escape(tooltip)}; select for its evidence ledger</title></path>'
            f'<path d="{path_data}" fill="none" stroke="transparent" stroke-width="16" style="pointer-events:stroke"/></g>'
        )
    node_marks: list[str] = []
    for node in nodes:
        node_id = str(node.get("id", ""))
        x, y = positions[node_id]
        available = bool(node.get("posterior_available", False))
        color = _probability_color(
            node.get("posterior_probability"), available, node_range, "node"
        )
        radius = 8 if node.get("is_start") or node.get("is_target") else 6
        tooltip = (
            f"{node.get('label', node_id)}; posterior {_number(node.get('posterior_probability'))}; "
            f"roles {node.get('classes', 'unclassified')}"
        )
        label_y = y - radius - 4
        evidence_key = _inspection_key({"kind": "node", "symbol": node_id}) if available else ""
        interaction = (
            f' class="inspectable-mark" data-interpretation-key="{_escape(evidence_key)}" tabindex="0" role="button"'
            if evidence_key
            else ""
        )
        node_marks.append(
            f'<g{interaction}><circle cx="{x:.2f}" cy="{y:.2f}" r="{radius}" fill="{color}" stroke="#fff" stroke-width="1.4">'
            f'<title>{_escape(tooltip)}</title></circle><text x="{x:.2f}" y="{label_y:.2f}" '
            f'text-anchor="middle" class="node-label">{_escape(node.get("label", node_id))}</text></g>'
        )
    return f"""
      <figure class="figure-card network-card">
        <figcaption><strong>Merged top-path network</strong><span>{len(nodes):,} nodes · {len(edges):,} edges · top {_integer(effective_limit)} paths</span></figcaption>
        <svg viewBox="0 0 {width} {height}" role="img" aria-label="Merged top-path network">
          <defs>
            <marker id="arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L8 4 L0 8z" fill="#0d3b2a"/></marker>
            <linearGradient id="network-node-posterior-scale" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#dceaf5"/><stop offset="1" stop-color="#173f66"/></linearGradient>
            <linearGradient id="network-edge-posterior-scale" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#d8ebe1"/><stop offset="1" stop-color="#0d3b2a"/></linearGradient>
          </defs>
          {''.join(edge_marks)}{''.join(node_marks)}
          <text x="{width-156}" y="{top-14}" text-anchor="middle" class="axis-label">Nodes</text>
          <rect x="{width-165}" y="{top}" width="18" height="{height-top-bottom}" rx="5" fill="url(#network-node-posterior-scale)" stroke="#789083" stroke-width="0.8"/>
          <text x="{width-141}" y="{top+4}" class="axis-label">{_number(node_range[1]) if node_range else '—'}</text>
          <text x="{width-141}" y="{height-bottom}" class="axis-label">{_number(node_range[0]) if node_range else '—'}</text>
          <text x="{width-71}" y="{top-14}" text-anchor="middle" class="axis-label">Edges</text>
          <rect x="{width-80}" y="{top}" width="18" height="{height-top-bottom}" rx="5" fill="url(#network-edge-posterior-scale)" stroke="#789083" stroke-width="0.8"/>
          <text x="{width-56}" y="{top+4}" class="axis-label">{_number(edge_range[1]) if edge_range else '—'}</text>
          <text x="{width-56}" y="{height-bottom}" class="axis-label">{_number(edge_range[0]) if edge_range else '—'}</text>
        </svg>
        <p class="caption">Scored nodes and edges use separate monotone color scales so their different posterior ranges remain visible. The node scale spans {_number(node_range[0]) if node_range else 'unavailable'}–{_number(node_range[1]) if node_range else 'unavailable'}; the edge scale spans {_number(edge_range[0]) if edge_range else 'unavailable'}–{_number(edge_range[1]) if edge_range else 'unavailable'}. Within each scale, the minimum is lightest and the maximum is darkest. Orange nodes lack a Bayesian node posterior. Arrows show uniquely constrained traversal; unresolved relationships remain traversable in either direction. Select a protein node or any edge to open its frozen evidence interpretation below.</p>
      </figure>
    """


def _paths_table(paths: Iterable[dict[str, Any]]) -> str:
    rows: list[str] = []
    for path in paths:
        temporal = ""
        if path.get("temporal_n_scored") is not None:
            temporal = (
                f"{_integer(path.get('temporal_n_scored'))}; τ {_number(path.get('temporal_kendall_tau_mean'))} "
                f"[{_number(path.get('temporal_kendall_tau_low'))}, {_number(path.get('temporal_kendall_tau_high'))}]"
            )
        rows.append(
            "<tr>"
            f"<td>{_integer(path.get('rank'))}</td>"
            f"<td>{_escape(path.get('path_symbols', ''))}</td>"
            f"<td>{_integer(path.get('hop_count'))}</td>"
            f"<td>{_number(path.get('primary_path_score', path.get('geometric_mean_edge_probability', path.get('path_probability_product'))), 8)}</td>"
            f"<td>{_escape(temporal or '—')}</td>"
            "</tr>"
        )
    if not rows:
        rows.append('<tr><td colspan="5">No paths were produced.</td></tr>')
    return (
        '<div class="table-scroll"><table><thead><tr><th>Rank</th><th>Route</th><th>Hops</th>'
        '<th>Geometric-mean edge score (or node + edge when enabled)</th><th>Temporal annotation</th></tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _calibration_tables(calibration: dict[str, Any] | None) -> str:
    if not calibration or not calibration.get("enabled"):
        return "<p>Evidence calibration was disabled for this run.</p>"
    sections: list[str] = []
    for stage in ("node", "edge"):
        fit = calibration.get(stage)
        if not fit:
            continue
        rows = []
        for parameter in fit.get("parameters", []):
            rows.append(
                "<tr>"
                f"<td>{_escape(parameter.get('stream_id'))}</td>"
                f"<td>{_escape(parameter.get('parameter'))}</td>"
                f"<td>{_number(parameter.get('current'))}</td>"
                f"<td>{_number(parameter.get('preferred'))}</td>"
                f"<td>{_number(parameter.get('fitted'))}</td>"
                f"<td>{_number(parameter.get('lower_bound'))}–{_number(parameter.get('upper_bound'))}</td>"
                "</tr>"
            )
        sections.append(
            f"<h3>{stage.title()} evidence</h3><div class=\"table-scroll\"><table><thead><tr>"
            "<th>Stream</th><th>Parameter</th><th>Start</th><th>Preferred</th><th>Fitted</th><th>Bounds</th>"
            f"</tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )
    return "".join(sections) or "<p>No fitted parameter table was available.</p>"


def _configuration_tables(
    registry: dict[str, Any], configuration: dict[str, Any]
) -> str:
    """Render the submitted stream controls in the same conceptual groups as the GUI."""

    blocks: list[str] = []
    for title, registry_key, configuration_key in (
        ("Node selection", "node_streams", "node_streams"),
        ("Edge characterization", "edge_streams", "edge_streams"),
    ):
        definitions = registry.get(registry_key, [])
        states = configuration.get(configuration_key, {})
        rows: list[str] = []
        for definition in definitions:
            stream_id = str(definition.get("id", ""))
            state = states.get(stream_id, {})
            parameters = state.get("parameters", {})
            parameter_text = "; ".join(
                f"{key}={_number(value)}" for key, value in parameters.items()
            ) or "—"
            tq = state.get("tq_multiplier")
            preferred = state.get("preferred_tq_multiplier")
            rows.append(
                "<tr>"
                f"<td><strong>{_escape(definition.get('label', stream_id))}</strong><br><small>{_escape(definition.get('description', ''))}</small></td>"
                f"<td><span class=\"status {'supports' if state.get('enabled') else 'disabled'}\">{'Enabled' if state.get('enabled') else 'Disabled'}</span></td>"
                f"<td>{_number(state.get('weight'))}</td>"
                f"<td>{_number(tq) if tq is not None else '—'}</td>"
                f"<td>{_number(preferred) if preferred is not None else '—'}</td>"
                f"<td>{_escape(parameter_text)}</td>"
                "</tr>"
            )
        blocks.append(
            f"<h3>{title}</h3><div class=\"table-scroll settings-table\"><table><thead><tr>"
            "<th>Evidence stream</th><th>Used</th><th>Weight</th><th>Tq / scale</th>"
            f"<th>Preferred Tq</th><th>Stream parameters</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div>"
        )

    for title, key in (
        ("Node integration", "node_integration"),
        ("Edge integration", "edge_integration"),
        ("Full-graph statistics", "graph_statistics"),
        ("Path inference", "path"),
        ("Evidence calibration", "calibration"),
    ):
        values = configuration.get(key, {})
        compact = []
        for name, value in values.items():
            if isinstance(value, (dict, list)):
                continue
            compact.append(
                f"<div><dt>{_escape(name.replace('_', ' '))}</dt><dd>{_escape(value)}</dd></div>"
            )
        if compact:
            blocks.append(f"<h3>{title}</h3><dl class=\"configuration-grid\">{''.join(compact)}</dl>")
    return "".join(blocks)


def _graph_statistics_html(
    statistics: dict[str, Any] | None,
    *,
    preferred_metric: str = "degree",
    empty_message: str = "Full-graph statistics were disabled for this run.",
) -> str:
    if not statistics or not statistics.get("summary"):
        return f"<p>{_escape(empty_message)}</p>"
    summary = statistics["summary"]
    is_path_union = preferred_metric == "path_participation_count"
    if is_path_union:
        fields = (
            ("Returned paths", "returned_path_count"),
            ("Unique nodes", "node_count"),
            ("Unique undirected edges", "unique_undirected_path_edge_count"),
            ("Density", "density"),
        )
        presentation_metrics = set(PATH_UNION_PRESENTATION_METRICS)
        measures_heading = "Five interpretable path-network measures"
    else:
        fields = (
            ("Nodes", "node_count"),
            ("Edges", "edge_count"),
            ("Density", "density"),
            ("Components", "connected_component_count"),
        )
        presentation_metrics = set(FULL_GRAPH_PRESENTATION_METRICS)
        measures_heading = "Four interpretable node measures"
    cards = "".join(
        f"<div><dt>{_escape(label)}</dt><dd>{_number(summary.get(key), 8)}</dd></div>"
        for label, key in fields
        if summary.get(key) is not None
    )
    available = [
        item
        for item in (statistics.get("available_metrics") or [])
        if item.get("id") in presentation_metrics
    ]
    metric_id = preferred_metric if any(item.get("id") == preferred_metric for item in available) else (
        available[0].get("id") if available else None
    )
    all_rankings = statistics.get("top_nodes_by_metric") or {}
    rankings = all_rankings.get(metric_id, [])
    rows = "".join(
        f"<tr><td>{rank}</td><td>{_escape(row.get('symbol'))}</td><td>{_escape(row.get('name') or '—')}</td><td>{_number(row.get('value'), 8)}</td></tr>"
        for rank, row in enumerate(rankings, start=1)
    ) or '<tr><td colspan="4">No node ranking was available.</td></tr>'
    label = next(
        (item.get("label") for item in available if item.get("id") == metric_id),
        "Selected statistic",
    )
    approximation_notes: list[str] = []
    if summary.get("clustering_is_approximate"):
        approximation_notes.append(
            f"clustering sampled {_number(summary.get('clustering_neighbor_pair_samples_per_node'), 0)} neighbor pairs per node"
        )
    if summary.get("betweenness_is_approximate"):
        approximation_notes.append(
            f"betweenness used {_number(summary.get('betweenness_approximation_source_count'), 0)} sources"
        )
    approximation_html = (
        '<p class="caption"><strong>Large-graph approximations:</strong> '
        + _escape("; ".join(approximation_notes))
        + ".</p>"
        if approximation_notes
        else ""
    )
    metric_panels: list[str] = []
    for metric in available:
        candidate_id = metric.get("id")
        candidate_rows = all_rankings.get(candidate_id, [])
        if not candidate_rows:
            continue
        candidate_body = "".join(
            f"<tr><td>{rank}</td><td>{_escape(row.get('symbol'))}</td><td>{_escape(row.get('name') or '—')}</td><td>{_number(row.get('value'), 8)}</td></tr>"
            for rank, row in enumerate(candidate_rows, start=1)
        )
        metric_panels.append(
            f'<details><summary>Top {len(candidate_rows)} nodes by {_escape(metric.get("label", candidate_id))}</summary>'
            '<div class="table-scroll"><table><thead><tr><th>Rank</th><th>Node</th><th>Name</th><th>Value</th></tr></thead><tbody>'
            + candidate_body
            + "</tbody></table></div></details>"
        )
    return (
        f"<p class=\"lede\">{_escape(summary.get('graph_definition', ''))} "
        f"{_escape(summary.get('interpretation_warning', ''))}</p>"
        f"<dl class=\"configuration-grid\">{cards}</dl>"
        + approximation_html
        + f"<h3>Top nodes by {_escape(label)}</h3>"
        '<div class="table-scroll"><table><thead><tr><th>Rank</th><th>Node</th><th>Name</th><th>Value</th></tr></thead><tbody>'
        + rows
        + "</tbody></table></div>"
        + f"<h3>{_escape(measures_heading)}</h3>"
        + "".join(metric_panels)
        + "<p class=\"caption\">The embedded file vault contains the complete per-node statistics table.</p>"
    )


def _interpretation_selector(catalog: list[dict[str, Any]]) -> str:
    if not catalog:
        return '<p class="empty-figure">No evidence ledger could be frozen into this export.</p>'
    options: list[str] = []
    for item in catalog:
        label = (
            str(item.get("symbol", ""))
            if item.get("kind") == "node"
            else f"{item.get('node_a', '')} — {item.get('node_b', '')}"
        )
        options.append(
            f'<option value="{_escape(_inspection_key(item))}">{_escape(item.get("kind", "").title())}: {_escape(label)}</option>'
        )
    return f"""
      <div class="interpretation-toolbar">
        <label for="interpretation-select"><strong>Inspect a saved hypothesis</strong><span>Every selectable mark in the archived network is included.</span></label>
        <select id="interpretation-select">{''.join(options)}</select>
      </div>
      <div id="interpretation-summary" class="evidence-summary-grid"></div>
      <p id="interpretation-reconciliation" class="reconciliation"></p>
      <article id="interpretation-literature" class="literature-card selected-literature hidden">
        <div class="literature-card-head"><div><span>Literature interpretation</span><h3 id="interpretation-literature-title">Selected hypothesis</h3></div><strong id="interpretation-literature-model">—</strong></div>
        <div id="interpretation-literature-summary" class="literature-summary-grid"></div>
        <p id="interpretation-literature-takeaway" class="takeaway"></p>
        <div class="table-scroll literature-assessment"><table><thead><tr><th>Interpretive dimension</th><th>Assessment</th></tr></thead><tbody id="interpretation-literature-body"></tbody></table></div>
        <div id="interpretation-literature-sources" class="source-links"></div>
        <p class="caption">This literature summary is downstream commentary and did not alter the Bayesian result or path ranking.</p>
      </article>
      <div class="table-scroll"><table><thead><tr><th>Evidence stream</th><th>Call</th><th>Record / negative scope</th><th>BF</th><th>Weight</th><th>Δ log₂ odds</th><th>BF percentile</th></tr></thead><tbody id="interpretation-ledger"></tbody></table></div>
      <p class="caption"><strong>Record versus negative scope:</strong> a retained record supplies direct evidence. Negative scope asks whether a missing record would have been interpretable enough to lower the hypothesis odds. A positive record can therefore be used even when absence would not have been scorable.</p>
      <p id="interpretation-detail" class="stream-detail">Select an evidence row for its complete scientific interpretation.</p>
      <div id="interpretation-provenance" class="provenance-box hidden"></div>
      <figure id="interpretation-distribution" class="factor-figure hidden"><figcaption><strong>Applied Bayes-factor distribution</strong><span id="interpretation-distribution-label"></span></figcaption><svg id="interpretation-distribution-svg" viewBox="0 0 720 180" role="img" aria-label="Evidence-stream Bayes-factor distribution"></svg><p id="interpretation-distribution-summary" class="caption"></p></figure>
      <details><summary>Complete machine-readable ledger</summary><pre id="interpretation-json"></pre></details>
    """


def _inspection_history(history: list[dict[str, Any]]) -> str:
    if not history:
        return "<p>No individual node or edge hypotheses were inspected before export.</p>"
    blocks: list[str] = []
    for item in history:
        hypothesis = item.get("symbol") if item.get("kind") == "node" else f"{item.get('node_a')} — {item.get('node_b')}"
        rows = []
        for stream in item.get("streams", []):
            rows.append(
                "<tr>"
                f"<td>{_escape(stream.get('label', stream.get('stream_id', '')))}</td>"
                f"<td><span class=\"status { _escape(stream.get('status', 'neutral')) }\">{_escape(stream.get('status', ''))}</span></td>"
                f"<td>{_number(stream.get('applied_bayes_factor'))}</td>"
                f"<td>{_number(stream.get('weight'))}</td>"
                f"<td>{_number(stream.get('weighted_log2_odds_contribution'))}</td>"
                f"<td>{_escape(_percentile(stream.get('distribution_position')))}</td>"
                f"<td>{_escape(stream.get('note', ''))}</td>"
                "</tr>"
            )
        blocks.append(
            f"<details><summary>{_escape(hypothesis)} · posterior {_number(item.get('stored_posterior_probability'))}</summary>"
            '<div class="table-scroll"><table><thead><tr><th>Evidence stream</th><th>Call</th><th>BF</th><th>Weight</th><th>Δ log₂ odds</th><th>BF percentile</th><th>Note</th></tr></thead>'
            f"<tbody>{''.join(rows)}</tbody></table></div>{_json_pre(item)}</details>"
        )
    return "".join(blocks)


def _safe_web_link(url: object, label: object) -> str:
    text = str(url or "").strip()
    if not text.startswith(("https://", "http://")):
        return ""
    return (
        f'<a href="{_escape(text)}" target="_blank" rel="noopener noreferrer">'
        f"{_escape(label or text)}</a>"
    )


def _literature_interpretations_html(items: list[dict[str, Any]]) -> str:
    if not items:
        return (
            '<p class="empty-figure">No on-demand literature interpretation was '
            "requested before this session was exported.</p>"
        )
    blocks: list[str] = []
    for record in items:
        result = record.get("interpretation") or {}
        hypothesis = result.get("hypothesis_label") or " — ".join(
            str(record.get("hypothesis", {}).get(key, ""))
            for key in ("node_a", "node_b")
            if record.get("hypothesis", {}).get(key)
        ) or record.get("hypothesis", {}).get("symbol", "Hypothesis")
        claims: list[str] = []
        for claim in result.get("contextual_evidence", []) or []:
            links = " · ".join(
                link
                for link in (
                    _safe_web_link(url, f"source {index}")
                    for index, url in enumerate(claim.get("source_urls", []) or [], start=1)
                )
                if link
            )
            claims.append(
                "<li><strong>"
                + _escape(str(claim.get("scope", "")).replace("_", " "))
                + " · "
                + _escape(claim.get("biological_context", "Context not specified"))
                + " · "
                + _escape(claim.get("support", ""))
                + ":</strong> "
                + _escape(claim.get("claim", ""))
                + (f'<div class="source-links">{links}</div>' if links else "")
                + "</li>"
            )
        source_links: list[str] = []
        seen: set[str] = set()
        for source in [*(result.get("sources", []) or []), *(record.get("web_sources", []) or [])]:
            url = str(source.get("url", ""))
            if not url or url in seen:
                continue
            seen.add(url)
            link = _safe_web_link(url, source.get("title") or url)
            if link:
                source_links.append(f"<li>{link}</li>")
        list_sections = []
        for title, key in (
            ("Conflicting or missing evidence", "conflicting_or_missing_evidence"),
            ("Caveats", "caveats"),
        ):
            values = result.get(key, []) or []
            if values:
                list_sections.append(
                    f"<h4>{_escape(title)}</h4><ul>"
                    + "".join(f"<li>{_escape(value)}</li>" for value in values)
                    + "</ul>"
                )
        blocks.append(
            '<article class="literature-card">'
            f'<div class="literature-card-head"><div><span>{_escape(str(result.get("classification", "uncertain")).replace("_", " "))}</span><h3>{_escape(hypothesis)}</h3></div><strong>{_number(float(result.get("confidence", 0)) * 100, 3)}% confidence</strong></div>'
            f'<p class="takeaway">{_escape(result.get("one_sentence_takeaway", ""))}</p>'
            f'<p><strong>Context:</strong> {_escape(record.get("cell_type", ""))}<br><strong>Purpose:</strong> {_escape(record.get("signaling_purpose", ""))}</p>'
            + (f'<h4>Literature findings across contexts</h4><ul class="claim-list">{"".join(claims)}</ul>' if claims else "")
            + f'<h4>Novelty interpretation</h4><p>{_escape(result.get("novelty_interpretation", ""))}</p>'
            + f'<h4>Mechanistic interpretation</h4><p>{_escape(result.get("mechanistic_interpretation", ""))}</p>'
            + f'<h4>Bayesian evidence summary</h4><p>{_escape(result.get("bayesian_evidence_summary", ""))}</p>'
            + f'<h4>Database traceback summary</h4><p>{_escape(result.get("database_trace_summary", ""))}</p>'
            + "".join(list_sections)
            + (f'<h4>Sources</h4><ul class="source-list">{"".join(source_links)}</ul>' if source_links else "")
            + f'<p class="caption">Generated {_escape(record.get("generated_at", ""))} with {_escape(record.get("model", ""))} ({_escape(record.get("reasoning_effort", ""))} reasoning). This interpretation is downstream commentary and did not modify the Bayesian result.</p>'
            + "</article>"
        )
    return "".join(blocks)


def _network_literature_analyses_html(analyses: list[dict[str, Any]]) -> str:
    if not analyses:
        return ""
    blocks: list[str] = []
    for analysis in analyses:
        synthesis = analysis.get("synthesis") or {}
        counts = analysis.get("classification_counts") or {}
        count_cards = "".join(
            _metric_card(str(label).replace("_", " "), _integer(value))
            for label, value in sorted(counts.items(), key=lambda item: (-int(item[1]), item[0]))
        )
        blocks.append(
            '<article class="literature-card">'
            '<div class="literature-card-head"><div><span>whole displayed network</span>'
            f'<h3>{_integer(analysis.get("node_count"))} nodes · {_integer(analysis.get("edge_count"))} edges</h3></div>'
            f'<strong>{_escape(analysis.get("model", ""))} · {_escape(analysis.get("reasoning_effort", ""))}</strong></div>'
            f'<p class="takeaway">{_escape(synthesis.get("overall_summary", ""))}</p>'
            f'<div class="metrics">{count_cards}</div>'
            + '<p class="notice">Detailed LLM results are shown one hypothesis at a time in the Evidence section. Select a node or edge in the network, or use the hypothesis selector there.</p>'
            + '<p class="caption">This audit covered every unique hypothesis in the submitted top-path scope. It did not alter the graph.</p>'
            + "</article>"
        )
    return "".join(blocks)


def _pack_file(path: Path, output_directory: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    packed = gzip.compress(raw, compresslevel=6, mtime=0)
    use_gzip = len(raw) >= 4096 and len(packed) + 128 < len(raw)
    payload = packed if use_gzip else raw
    relative = path.relative_to(output_directory).as_posix()
    return {
        "name": relative,
        "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        "original_size": len(raw),
        "stored_size": len(payload),
        "encoding": "gzip" if use_gzip else "identity",
        "sha256": hashlib.sha256(raw).hexdigest(),
        "base64": base64.b64encode(payload).decode("ascii"),
    }


def build_session_report(
    session: dict[str, Any],
    output_directory: Path | str,
    *,
    report_name: str | None = None,
) -> dict[str, Any]:
    """Write and describe a self-contained HTML export for one run."""

    output = Path(output_directory).resolve()
    if not output.is_dir():
        raise FileNotFoundError(f"run output directory does not exist: {output}")
    run_id = str(session.get("job", {}).get("job_id") or session.get("run_id") or output.name)
    name = report_name or _safe_report_name(run_id)
    if Path(name).name != name or not name.lower().endswith(".html"):
        raise ValueError("report_name must be a plain .html file name")
    destination = output / name

    source_files = [
        path
        for path in sorted(output.rglob("*"))
        if path.is_file()
        and path.resolve() != destination.resolve()
        and not path.name.startswith(REPORT_PREFIX)
        and not path.name.endswith(".tmp")
    ]
    packed_files = [_pack_file(path, output) for path in source_files]
    snapshot = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": session,
        "embedded_file_manifest": [
            {key: value for key, value in item.items() if key != "base64"}
            for item in packed_files
        ],
    }
    snapshot_raw = (json.dumps(snapshot, indent=2, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    snapshot_packed = gzip.compress(snapshot_raw, compresslevel=6, mtime=0)
    packed_files.insert(
        0,
        {
            "name": "session_snapshot.json",
            "mime_type": "application/json",
            "original_size": len(snapshot_raw),
            "stored_size": len(snapshot_packed),
            "encoding": "gzip",
            "sha256": hashlib.sha256(snapshot_raw).hexdigest(),
            "base64": base64.b64encode(snapshot_packed).decode("ascii"),
        },
    )

    job = session.get("job", {})
    preview = job.get("preview") or session.get("preview") or {}
    summary = job.get("summary") or session.get("summary") or {}
    configuration = session.get("configuration") or {}
    registry = session.get("registry") or {}
    metrics = preview.get("metrics", {})
    distributions = preview.get("probability_distributions", {})
    inspections = session.get("inspection_history") or []
    literature_interpretations = session.get("literature_interpretations") or []
    network_literature_analyses = session.get("network_literature_analyses") or []
    interpretation_catalog = session.get("interpretation_catalog") or inspections
    interpretation_scope = session.get("interpretation_scope") or {}
    interpretation_errors = session.get("interpretation_errors") or []
    warnings = list(preview.get("warnings") or summary.get("warnings") or [])
    warnings.extend(
        f"Offline interpretation snapshot: {message}"
        for message in interpretation_errors
    )
    visible_path_limit = int(
        session.get("client_state", {}).get("visible_path_rank_limit")
        or min(50, int((preview.get("path_network") or {}).get("visualized_path_count") or 0))
    )
    original_bytes = sum(item["original_size"] for item in packed_files)
    stored_bytes = sum(item["stored_size"] for item in packed_files)

    manifest_rows = "".join(
        "<tr data-file-row>"
        f"<td>{_escape(item['name'])}</td>"
        f"<td>{_human_bytes(item['original_size'])}</td>"
        f"<td>{_human_bytes(item['stored_size'])}</td>"
        f"<td>{_escape(item['encoding'])}</td>"
        f"<td><code>{item['sha256']}</code></td>"
        f"<td><button type=\"button\" data-download=\"{_escape(item['name'])}\">Download</button></td>"
        "</tr>"
        for item in packed_files
    )
    vault = {
        item["name"]: {
            "mime_type": item["mime_type"],
            "encoding": item["encoding"],
            "base64": item["base64"],
        }
        for item in packed_files
    }
    vault_json = json.dumps(vault, ensure_ascii=True, separators=(",", ":")).replace("</", "<\\/")
    interpretation_json = json.dumps(
        {_inspection_key(item): item for item in interpretation_catalog},
        ensure_ascii=True,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    literature_json = json.dumps(
        {
            _inspection_key(record.get("hypothesis") or {}): record
            for record in literature_interpretations
            if (record.get("hypothesis") or {}).get("kind") in {"node", "edge"}
        },
        ensure_ascii=True,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    warning_html = "".join(f"<li>{_escape(warning)}</li>" for warning in warnings)

    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Complete Bayesian session · {_escape(run_id)}</title>
<style>
:root{{--ink:#17261f;--muted:#68736d;--line:#dce2dd;--paper:#fbfcfa;--surface:#fff;--green:#266547;--red:#b13e37;--orange:#c77b43}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}
header{{padding:42px max(28px,calc((100vw - 1180px)/2));background:#173d2d;color:#fff}} header p{{max-width:850px;color:#d7e4dd}} h1,h2,h3{{font-family:Georgia,serif;font-weight:500}} h1{{margin:.15em 0;font-size:40px}} h2{{margin:0 0 15px;font-size:28px}} h3{{margin:22px 0 8px}} nav{{position:sticky;top:0;z-index:5;display:flex;gap:18px;padding:11px max(28px,calc((100vw - 1180px)/2));overflow:auto;border-bottom:1px solid var(--line);background:rgba(255,255,255,.96)}} nav a{{color:var(--green);font-size:12px;font-weight:700;text-decoration:none;white-space:nowrap}}
main{{max-width:1180px;margin:auto;padding:32px 28px 70px}} section{{margin:0 0 38px;padding:26px;border:1px solid var(--line);border-radius:14px;background:var(--surface)}} .eyebrow{{margin:0;color:var(--orange);font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}} .lede{{max-width:850px;color:var(--muted)}} .metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:20px}} .metric{{padding:15px;border:1px solid var(--line);border-radius:10px;background:#f7f9f7}} .metric span,.metric small{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.06em}} .metric strong{{display:block;margin:4px 0;font:500 25px Georgia,serif}} .notice{{padding:13px 15px;border-left:4px solid var(--green);background:#edf5f0}} .warning-list{{color:#7d3d26}} .figure-grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} .figure-card{{margin:0;padding:14px;border:1px solid var(--line);border-radius:10px}} figcaption{{display:flex;justify-content:space-between;gap:15px}} figcaption span,.caption{{color:var(--muted);font-size:11px}} svg{{display:block;width:100%;height:auto;margin-top:8px;background:#fff}} .axis{{stroke:#8b958f}} .prior-line{{stroke:#7d8781;stroke-dasharray:4 4}} .cutoff-line{{stroke:var(--orange);stroke-width:2;stroke-dasharray:5 3}} .axis-label,.node-label{{fill:var(--muted);font-size:10px}} .node-label{{fill:var(--ink);paint-order:stroke;stroke:#fff;stroke-width:3px;font-weight:700}} .figure-stats{{display:grid;grid-template-columns:repeat(6,1fr);gap:7px}} .figure-stats div{{padding:7px;background:#f5f7f5}} dt{{color:var(--muted);font-size:9px;text-transform:uppercase}} dd{{margin:2px 0 0;font-weight:700}} .network-card{{overflow:auto}} .network-card svg{{min-width:900px}}
.table-scroll{{max-height:620px;overflow:auto;border:1px solid var(--line);border-radius:8px}} table{{width:100%;border-collapse:collapse;font-size:12px}} th{{position:sticky;top:0;background:#eef2ef;text-align:left}} th,td{{padding:9px;border-bottom:1px solid var(--line);vertical-align:top}} tr[data-stream-key]{{cursor:pointer}} tr[data-stream-key]:hover,tr[data-stream-key].selected{{background:#f0f6f2}} code{{font-size:10px;word-break:break-all}} pre{{max-height:620px;overflow:auto;padding:16px;border-radius:8px;background:#101a15;color:#dce8e0;font:11px/1.45 ui-monospace,monospace;white-space:pre-wrap;word-break:break-word}} details{{margin:10px 0;border:1px solid var(--line);border-radius:8px;padding:11px}} summary{{cursor:pointer;font-weight:750}} button,select{{padding:8px 10px;border:1px solid #98aa9f;border-radius:6px;background:#fff;color:var(--green);font-weight:700}} button{{cursor:pointer}} input[type=search]{{width:min(440px,100%);padding:10px;border:1px solid var(--line);border-radius:7px}} .status{{font-size:10px;font-weight:800;text-transform:uppercase}} .status.supports{{color:var(--green)}} .status.refutes{{color:var(--red)}} .status.neutral,.status.disabled{{color:var(--muted)}} .empty-figure{{padding:40px;color:var(--muted);text-align:center}} .inspectable-mark{{cursor:pointer;outline:none}} .inspectable-mark:focus,.inspectable-mark:hover{{filter:drop-shadow(0 0 3px #1e5f43)}} .interpretation-toolbar{{display:grid;grid-template-columns:1fr minmax(270px,420px);gap:18px;align-items:end;margin:18px 0}} .interpretation-toolbar label span{{display:block;color:var(--muted);font-size:11px;font-weight:400}} .evidence-summary-grid,.literature-summary-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}} .literature-summary-grid{{grid-template-columns:1.1fr .55fr 1.8fr}} .evidence-summary-grid div,.literature-summary-grid div{{padding:12px;border:1px solid var(--line);border-radius:8px;background:#f7f9f7}} .evidence-summary-grid span,.literature-summary-grid span{{display:block;color:var(--muted);font-size:9px;letter-spacing:.06em;text-transform:uppercase}} .evidence-summary-grid strong{{font:500 18px Georgia,serif}} .literature-summary-grid strong{{display:block;margin-top:4px;font-size:12px}} .literature-assessment th:first-child{{width:24%;color:var(--green)}} .literature-assessment ul{{margin:0;padding-left:18px}} .selected-literature.hidden{{display:none}} .reconciliation,.stream-detail{{padding:11px 13px;border-left:3px solid var(--green);background:#edf5f0}} .reconciliation.warning{{border-color:var(--red);background:#f8ecea}} .factor-figure{{margin:15px 0;padding:14px;border:1px solid var(--line);border-radius:9px}} .factor-figure.hidden,.provenance-box.hidden{{display:none}} .provenance-box{{margin:12px 0;padding:14px;border:1px solid var(--line);border-radius:9px;background:#f8faf8}} .provenance-box ul{{margin:.4rem 0}} .factor-axis{{stroke:#8b958f}} .factor-neutral{{stroke:#68736d;stroke-dasharray:4 4}} .factor-selected{{stroke:var(--orange);stroke-width:2.5}} .factor-bar.supports{{fill:#3c8663}} .factor-bar.refutes{{fill:#bf625a}} .factor-bar.neutral{{fill:#98a19c}} .configuration-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}} .configuration-grid div{{padding:10px;border:1px solid var(--line);border-radius:7px;background:#f7f9f7}} .configuration-grid dd{{word-break:break-word}} .settings-table small{{color:var(--muted)}} .literature-card{{margin:16px 0;padding:20px;border:1px solid var(--line);border-radius:12px;background:#fcfdfc}} .literature-card-head{{display:flex;justify-content:space-between;gap:20px;align-items:start}} .literature-card-head span{{color:var(--orange);font-size:10px;font-weight:800;text-transform:uppercase}} .literature-card-head h3{{margin:3px 0}} .takeaway{{padding:13px;border-left:4px solid var(--green);background:#edf5f0;font-weight:700}} .claim-list li{{margin:9px 0}} .source-links,.source-list{{font-size:12px}} .source-links a,.source-list a{{color:var(--green)}} footer{{padding:25px;text-align:center;color:var(--muted);font-size:11px}}
@media(max-width:760px){{.metrics,.figure-grid,.evidence-summary-grid,.literature-summary-grid,.interpretation-toolbar,.configuration-grid{{grid-template-columns:1fr}}.figure-stats{{grid-template-columns:repeat(3,1fr)}}section{{padding:18px}}}}
@media print{{nav,button,input{{display:none!important}}body{{background:#fff}}section{{break-inside:avoid;border-color:#bbb}}pre,.table-scroll{{max-height:none;overflow:visible}}}}
</style></head><body>
<header><p class="eyebrow">Graphical Bayesian Inference · Version 1</p><h1>Complete analysis session</h1><p>Run <strong>{_escape(run_id)}</strong> · exported {_escape(snapshot['generated_at'])}. This is a standalone scientific record: it makes no network requests and does not require the Python server.</p></header>
<nav><a href="#overview">Overview</a><a href="#settings">Settings</a><a href="#figures">Distributions</a><a href="#graph-statistics">Graph statistics</a><a href="#path-union-statistics">Path-union statistics</a><a href="#network">Network</a><a href="#interpretation">Evidence</a><a href="#literature">Literature</a><a href="#paths">Paths</a><a href="#calibration">Calibration</a><a href="#configuration">Raw records</a><a href="#vault">Embedded files</a></nav>
<main>
<section id="overview"><p class="eyebrow">Run identity</p><h2>Overview</h2><div class="notice">The HTML includes {_integer(len(packed_files))} recoverable artifacts. Original content totals {_human_bytes(original_bytes)} and is stored as {_human_bytes(stored_bytes)} before base64 encoding. Large text files are gzip-compressed internally; Download restores their original names and bytes.</div>
<div class="metrics">{_metric_card('Selected nodes', _integer(metrics.get('selected_nodes')))}{_metric_card('Supported edges', _integer(metrics.get('supported_edges')), 'unique unordered pairs')}{_metric_card('Ranked paths', _integer(metrics.get('ranked_paths')))}{_metric_card('Evidence ledgers', _integer(len(interpretation_catalog)), f'{len(literature_interpretations)} literature reports')}</div>
<h3>Warnings and reporting cautions</h3><ul class="warning-list">{warning_html or '<li>None recorded by the workflow.</li>'}</ul></section>
<section id="settings"><p class="eyebrow">Submitted analysis</p><h2>Settings and evidence streams</h2><p class="lede">This is the readable counterpart of the live GUI controls. It records which streams ran, their evidence weights, their Tq/reference multipliers, preferred calibration anchors, and the integration controls used for this exact result.</p>{_configuration_tables(registry, configuration)}</section>
<section id="figures"><p class="eyebrow">Posterior state</p><h2>Probability distributions</h2><div class="figure-grid">{_histogram_svg(distributions.get('nodes'), 'Node posterior probabilities')}{_histogram_svg(distributions.get('edges'), 'Edge posterior probabilities')}</div><p class="caption">Bar height is log₁₀(count + 1). Gray dashed lines mark the prior; orange dashed lines mark the configured exclusive output cutoff.</p></section>
<section id="graph-statistics"><p class="eyebrow">Complete selected graph</p><h2>Network statistics</h2>{_graph_statistics_html(preview.get('full_graph_statistics'))}</section>
<section id="path-union-statistics"><p class="eyebrow">Exact union of all returned paths</p><h2>Found-path node statistics</h2>{_graph_statistics_html(preview.get('found_path_union_statistics'), preferred_metric='path_participation_count', empty_message='No returned-path graph was available for this run.')}</section>
<section id="network"><p class="eyebrow">Path result</p><h2>Predicted network</h2>{_network_svg(preview.get('path_network'), visible_path_limit)}</section>
<section id="interpretation"><p class="eyebrow">Evidence transparency</p><h2>Interpret one node or edge</h2><p class="lede">This is the archived form of the live evidence inspector. A Bayes factor above 1 supports the selected node/edge, a factor below 1 refutes it, and 1 is neutral. The fitted or user-supplied weight scales that evidence on the log-odds scale: <strong>Δ log₂ odds = weight × log₂(BF)</strong>. The posterior is obtained by adding every enabled stream's contribution to the prior log-odds.</p><div class="notice">{_escape(interpretation_scope.get('coverage', 'Saved manual interpretation history.'))} Network rank limit at export: {_integer(interpretation_scope.get('visible_path_rank_limit', visible_path_limit))}. Click an inspectable node or edge in the network above, or choose it below.</div>{_interpretation_selector(interpretation_catalog)}</section>
<section id="literature"><p class="eyebrow">Context and novelty</p><h2>Literature audit overview</h2><p class="lede">The compact overview below confirms the scope and classification balance of each requested whole-network audit. Detailed results are linked to the corresponding node or edge in the Evidence section instead of being repeated as one long page.</p>{_network_literature_analyses_html(network_literature_analyses)}</section>
<section id="paths"><p class="eyebrow">Ranked candidates</p><h2>Most likely paths</h2>{_paths_table(preview.get('top_paths', []))}</section>
<section id="calibration"><p class="eyebrow">Evidence confidence</p><h2>Calibration parameters</h2>{_calibration_tables(preview.get('calibration'))}</section>
<section id="configuration"><p class="eyebrow">Reproducibility</p><h2>Exact machine-readable records</h2><details><summary>Exact submitted configuration</summary>{_json_pre(configuration)}</details><details><summary>Complete backend summary</summary>{_json_pre(summary)}</details><details><summary>Complete GUI result payload</summary>{_json_pre(preview)}</details><details><summary>Client view state at export</summary>{_json_pre(session.get('client_state', {}))}</details><details><summary>Manually inspected hypotheses</summary>{_inspection_history(inspections)}</details><details><summary>Whole-network literature audit records</summary>{_json_pre(network_literature_analyses)}</details><details><summary>Literature interpretation records</summary>{_json_pre(literature_interpretations)}</details></section>
<section id="vault"><p class="eyebrow">Recoverable run bundle</p><h2>Embedded file vault</h2><p class="lede">Every generated run artifact is embedded here. Search by name, download any file, and compare its SHA-256 hash with the manifest. <strong>session_snapshot.json</strong> contains the configuration, summaries, registry snapshot, view state, inspection history, and this manifest.</p><input id="file-search" type="search" placeholder="Filter files…" aria-label="Filter embedded files"><p id="download-status" class="lede"></p><div class="table-scroll"><table><thead><tr><th>File</th><th>Original</th><th>Stored</th><th>Encoding</th><th>SHA-256</th><th></th></tr></thead><tbody>{manifest_rows}</tbody></table></div></section>
</main><footer>Generated locally by Graphical Bayesian Inference Version 1 · report schema {REPORT_SCHEMA_VERSION}</footer>
<script>
const vault={vault_json};
function decode64(value){{const binary=atob(value);const bytes=new Uint8Array(binary.length);for(let i=0;i<binary.length;i+=1)bytes[i]=binary.charCodeAt(i);return bytes}}
const interpretations={interpretation_json};
const literatureInterpretations={literature_json};
async function restore(name){{const status=document.getElementById('download-status');const item=vault[name];if(!item)return;status.textContent=`Restoring ${{name}}…`;try{{let bytes=decode64(item.base64);if(item.encoding==='gzip'){{if(!('DecompressionStream' in window))throw new Error('This browser cannot decompress the embedded gzip payload. Open the report in a current Edge, Chrome, Firefox, or Safari release.');const stream=new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));bytes=new Uint8Array(await new Response(stream).arrayBuffer())}}const url=URL.createObjectURL(new Blob([bytes],{{type:item.mime_type}}));const link=document.createElement('a');link.href=url;link.download=name.split('/').pop();document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);status.textContent=`Restored ${{name}}.`}}catch(error){{status.textContent=`Could not restore ${{name}}: ${{error.message}}`}}}}
document.querySelectorAll('[data-download]').forEach(button=>button.addEventListener('click',()=>restore(button.dataset.download)));
document.getElementById('file-search').addEventListener('input',event=>{{const query=event.target.value.trim().toLowerCase();document.querySelectorAll('[data-file-row]').forEach(row=>row.hidden=!row.cells[0].textContent.toLowerCase().includes(query))}});
function evidenceNumber(value){{const number=Number(value);if(!Number.isFinite(number))return '—';if(number===0)return '0';if(Math.abs(number)>=10000||Math.abs(number)<0.001)return number.toExponential(3);return String(Number(number.toPrecision(5)))}}
function percentileText(position){{if(!position)return '—';return `${{evidenceNumber(position.lower_percentile)}}–${{evidenceNumber(position.upper_percentile)}}% (${{position.exact?'exact':'estimated'}})`}}
function scopeText(stream,kind){{if(!stream.enabled)return 'Disabled';if(kind==='node'){{if(stream.observed)return 'Observed';if(stream.fixed_absence_penalty_applied)return 'Not observed · penalty applied';if(stream.negative_evidence_eligible===false)return 'Not observed · absence not scorable';return 'Not observed · eligible'}}if(stream.source_record_retained)return stream.negative_evidence_eligible===false?'Record retained · absence not scorable':'Source record retained';if(stream.fixed_absence_penalty_applied||stream.absence_penalty_applied)return 'No record · penalty applied';if(stream.continuous_negative_evidence_applied)return 'No record · continuous penalty';if(stream.negative_evidence_eligible===false)return 'No record · no absence penalty';return stream.derived?'Rule not triggered':'No record · eligible'}}
function statusLabel(status){{return status==='supports'?'Supports':status==='refutes'?'Refutes':status==='disabled'?'Disabled':'Neutral'}}
function addSummary(label,value){{const box=document.createElement('div');const name=document.createElement('span');const strong=document.createElement('strong');name.textContent=label;strong.textContent=value;box.append(name,strong);document.getElementById('interpretation-summary').appendChild(box)}}
function addLiteratureSummary(label,value){{const box=document.createElement('div');const name=document.createElement('span');const strong=document.createElement('strong');name.textContent=label;strong.textContent=value;box.append(name,strong);document.getElementById('interpretation-literature-summary').appendChild(box)}}
function addLiteratureRow(label,value){{if(!value||(Array.isArray(value)&&!value.length))return;const row=document.createElement('tr');const heading=document.createElement('th');const body=document.createElement('td');heading.textContent=label;if(Array.isArray(value)){{const list=document.createElement('ul');value.forEach(item=>{{const entry=document.createElement('li');entry.textContent=String(item);list.appendChild(entry)}});body.appendChild(list)}}else body.textContent=String(value);row.append(heading,body);document.getElementById('interpretation-literature-body').appendChild(row)}}
function renderLiteratureInterpretation(key){{const panel=document.getElementById('interpretation-literature');const record=literatureInterpretations[key];if(!record){{panel.classList.add('hidden');return}}const result=record.interpretation||{{}};panel.classList.remove('hidden');document.getElementById('interpretation-literature-title').textContent=result.hypothesis_label||'Selected hypothesis';document.getElementById('interpretation-literature-model').textContent=`${{record.model||'LLM'}} · ${{record.reasoning_effort||''}}`;const summary=document.getElementById('interpretation-literature-summary');summary.innerHTML='';addLiteratureSummary('Classification',String(result.classification||'uncertain').replaceAll('_',' '));addLiteratureSummary('Confidence',`${{Math.round(Number(result.confidence||0)*100)}}%`);addLiteratureSummary('Biological context',record.cell_type||'Not specified');document.getElementById('interpretation-literature-takeaway').textContent=result.one_sentence_takeaway||'No concise takeaway was returned.';const body=document.getElementById('interpretation-literature-body');body.innerHTML='';addLiteratureRow('Signaling purpose',record.signaling_purpose);addLiteratureRow('Novelty',result.novelty_interpretation);addLiteratureRow('Mechanism',result.mechanistic_interpretation);addLiteratureRow('Bayesian evidence',result.bayesian_evidence_summary);addLiteratureRow('Database tracebacks',result.database_trace_summary);addLiteratureRow('Literature findings across contexts',(result.contextual_evidence||[]).map(claim=>`${{String(claim.scope||'').replaceAll('_',' ')}} · ${{claim.biological_context||'context not specified'}} · ${{String(claim.support||'').replaceAll('_',' ')}}: ${{claim.claim||''}}`));addLiteratureRow('Conflicting or missing evidence',result.conflicting_or_missing_evidence);addLiteratureRow('Caveats',result.caveats);const links=document.getElementById('interpretation-literature-sources');links.innerHTML='';const seen=new Set();[...(result.sources||[]),...(record.web_sources||[])].forEach(source=>{{const url=String(source.url||'');if(!url.startsWith('http')||seen.has(url))return;seen.add(url);const link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=source.title||url;links.appendChild(link)}})}}
function renderProvenance(trace){{const box=document.getElementById('interpretation-provenance');box.innerHTML='';if(!trace){{box.classList.add('hidden');return}}box.classList.remove('hidden');const title=document.createElement('h3');title.textContent=`${{trace.database||'Database'}} source traceback`;const summary=document.createElement('p');summary.textContent=trace.summary||'';box.append(title,summary);if(trace.score_derivation){{const derivation=document.createElement('p');derivation.textContent=[trace.score_derivation.formula,trace.score_derivation.important_limit].filter(Boolean).join(' ');box.appendChild(derivation)}}if(trace.factors?.length){{const heading=document.createElement('h4');heading.textContent='Native factors';const list=document.createElement('ul');trace.factors.forEach(factor=>{{const item=document.createElement('li');item.textContent=`${{factor.factor}}: ${{typeof factor.value==='boolean'?(factor.value?'yes':'no'):String(factor.value??'—')}}${{factor.description?` — ${{factor.description}}`:''}}`;list.appendChild(item)}});box.append(heading,list)}}if(trace.records?.length){{const details=document.createElement('details');const label=document.createElement('summary');label.textContent=`Source records (${{trace.records.length}} shown)`;const pre=document.createElement('pre');pre.textContent=JSON.stringify(trace.records,null,2);details.append(label,pre);box.appendChild(details)}}if(trace.links?.length){{const links=document.createElement('p');trace.links.forEach((source,index)=>{{if(index)links.append(' · ');const anchor=document.createElement('a');anchor.href=source.url;anchor.target='_blank';anchor.rel='noopener noreferrer';anchor.textContent=source.label||source.url;links.appendChild(anchor)}});box.appendChild(links)}}}}
function renderFactorDistribution(stream){{const figure=document.getElementById('interpretation-distribution');const svg=document.getElementById('interpretation-distribution-svg');const summary=document.getElementById('interpretation-distribution-summary');const distribution=stream.factor_distribution;if(!distribution||!distribution.bin_counts?.length){{figure.classList.add('hidden');return}}figure.classList.remove('hidden');svg.innerHTML='';const edges=distribution.bin_edges_log2.map(Number);const counts=distribution.bin_counts.map(Number);const width=720,height=180,left=44,right=16,top=18,bottom=36,plotWidth=width-left-right,plotHeight=height-top-bottom;const minimum=Math.min(...edges),maximum=Math.max(...edges);const x=value=>left+((value-minimum)/Math.max(maximum-minimum,1e-12))*plotWidth;const maxCount=Math.max(...counts.map(value=>Math.log10(value+1)),1);const ns='http://www.w3.org/2000/svg';function mark(tag,attributes){{const element=document.createElementNS(ns,tag);Object.entries(attributes).forEach(([key,value])=>element.setAttribute(key,String(value)));svg.appendChild(element);return element}}counts.forEach((count,index)=>{{const x0=x(edges[index]),x1=x(edges[index+1]);const magnitude=Math.log10(count+1)/maxCount*plotHeight;const midpoint=(edges[index]+edges[index+1])/2;const cls=midpoint<-1e-12?'refutes':midpoint>1e-12?'supports':'neutral';const bar=mark('rect',{{x:x0+0.5,y:top+plotHeight-magnitude,width:Math.max(0.5,x1-x0-1),height:magnitude,class:`factor-bar ${{cls}}`}});const tooltip=document.createElementNS(ns,'title');tooltip.textContent=`${{count.toLocaleString()}} hypotheses; BF ${{evidenceNumber(2**edges[index])}}–${{evidenceNumber(2**edges[index+1])}}`;bar.appendChild(tooltip)}});mark('line',{{x1:left,y1:top+plotHeight,x2:width-right,y2:top+plotHeight,class:'factor-axis'}});mark('line',{{x1:x(0),y1:top,x2:x(0),y2:top+plotHeight,class:'factor-neutral'}});const factor=Number(stream.applied_bayes_factor);if(Number.isFinite(factor)&&factor>0)mark('line',{{x1:x(Math.max(minimum,Math.min(maximum,Math.log2(factor)))),y1:top-3,x2:x(Math.max(minimum,Math.min(maximum,Math.log2(factor)))),y2:top+plotHeight,class:'factor-selected'}});document.getElementById('interpretation-distribution-label').textContent=stream.label||stream.stream_id;const scope=distribution.distribution_scope||'modeled hypotheses';summary.textContent=`Applied BF ${{evidenceNumber(factor)}} is at ${{percentileText(stream.distribution_position)}} among ${{Number(distribution.hypothesis_count||0).toLocaleString()}} ${{scope}}. Refuting: ${{Number(distribution.refuting_count||0).toLocaleString()}}; neutral: ${{Number(distribution.neutral_count||0).toLocaleString()}}; supporting: ${{Number(distribution.supporting_count||0).toLocaleString()}}.`}}
function renderInterpretation(key){{const payload=interpretations[key];if(!payload)return;const selector=document.getElementById('interpretation-select');if(selector)selector.value=key;const summary=document.getElementById('interpretation-summary');summary.innerHTML='';const hypothesis=payload.kind==='node'?payload.symbol:`${{payload.node_a}} — ${{payload.node_b}}`;const included=payload.kind==='node'?payload.selected_in_graph:payload.supported_above_output_cutoff;addSummary('Hypothesis',hypothesis);addSummary('Prior',evidenceNumber(payload.prior_probability));addSummary('Posterior',evidenceNumber(payload.stored_posterior_probability));addSummary('Decision',included?'Included':'Below cutoff');const difference=Number(payload.reconciliation_absolute_difference||0);const reconciliation=document.getElementById('interpretation-reconciliation');reconciliation.classList.toggle('warning',difference>1e-7);reconciliation.textContent=difference<=1e-7?`Arithmetic check passed: stored ${{evidenceNumber(payload.stored_posterior_probability)}}; reconstructed ${{evidenceNumber(payload.reconstructed_posterior_probability)}} from the displayed contributions.`:`Arithmetic warning: stored ${{evidenceNumber(payload.stored_posterior_probability)}}; reconstructed ${{evidenceNumber(payload.reconstructed_posterior_probability)}}; absolute difference ${{evidenceNumber(difference)}}.`;renderLiteratureInterpretation(key);const body=document.getElementById('interpretation-ledger');body.innerHTML='';let first=null;(payload.streams||[]).forEach((stream,index)=>{{const row=document.createElement('tr');row.dataset.streamKey=String(index);const values=[stream.label||stream.stream_id,statusLabel(stream.status),scopeText(stream,payload.kind),evidenceNumber(stream.applied_bayes_factor),evidenceNumber(stream.weight),evidenceNumber(stream.weighted_log2_odds_contribution),percentileText(stream.distribution_position)];values.forEach((value,column)=>{{const cell=document.createElement('td');if(column===1){{const badge=document.createElement('span');badge.className=`status ${{stream.status||'neutral'}}`;badge.textContent=value;cell.appendChild(badge)}}else cell.textContent=value;row.appendChild(cell)}});const show=()=>{{body.querySelectorAll('tr').forEach(candidate=>candidate.classList.toggle('selected',candidate===row));const parts=[stream.note,stream.description];if(stream.raw_value!==null&&stream.raw_value!==undefined)parts.push(`${{stream.raw_value_label||'Raw value'}}: ${{evidenceNumber(stream.raw_value)}}.`);if(stream.source_factor!==null&&stream.source_factor!==undefined)parts.push(`Source factor: ${{evidenceNumber(stream.source_factor)}}.`);if(stream.normalization_reference)parts.push(`Reference: ${{stream.normalization_reference}}${{stream.tq_multiplier===null||stream.tq_multiplier===undefined?'':`; scale ${{evidenceNumber(stream.tq_multiplier)}}`}}.`);if(stream.dependence_group)parts.push(`Shared-source group: ${{stream.dependence_group}}.`);document.getElementById('interpretation-detail').textContent=`${{stream.label||stream.stream_id}}: ${{parts.filter(Boolean).join(' ')}}`;renderProvenance(stream.provenance_trace);renderFactorDistribution(stream)}};row.addEventListener('click',show);body.appendChild(row);if(!first&&stream.enabled)first=show}});document.getElementById('interpretation-json').textContent=JSON.stringify(payload,null,2);if(first)first();else{{document.getElementById('interpretation-distribution').classList.add('hidden');renderProvenance(null)}}document.getElementById('interpretation').scrollIntoView({{behavior:'smooth',block:'start'}})}}
const interpretationSelect=document.getElementById('interpretation-select');if(interpretationSelect){{interpretationSelect.addEventListener('change',event=>renderInterpretation(event.target.value));renderInterpretation(interpretationSelect.value)}}
document.querySelectorAll('[data-interpretation-key]').forEach(mark=>{{const open=()=>renderInterpretation(mark.dataset.interpretationKey);mark.addEventListener('click',open);mark.addEventListener('keydown',event=>{{if(event.key==='Enter'||event.key===' '){{event.preventDefault();open()}}}})}});
</script></body></html>"""

    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(document, encoding="utf-8")
    temporary.replace(destination)
    report_size = destination.stat().st_size
    return {
        "file": destination.name,
        "path": str(destination),
        "embedded_file_count": len(packed_files),
        "embedded_original_bytes": original_bytes,
        "embedded_stored_bytes": stored_bytes,
        "interpretation_hypothesis_count": len(interpretation_catalog),
        "literature_interpretation_count": len(literature_interpretations),
        "report_bytes": report_size,
        "report_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "generated_at": snapshot["generated_at"],
    }
