from __future__ import annotations

from dataclasses import dataclass, asdict
import importlib.metadata
from pathlib import Path
import sys
from typing import Iterable

from .graph import CausalGraph


ROOT=Path(__file__).resolve().parents[2]
VENDOR=ROOT/".vendor"
if VENDOR.exists() and str(VENDOR) not in sys.path:
    # Append so the application's configured NumPy/Pandas remain authoritative;
    # packages absent from the host (y0/networkx) resolve from the private tree.
    sys.path.append(str(VENDOR))


@dataclass
class IdentificationResult:
    graph_id: str
    graph_signature: str
    query: str
    treatments: str
    outcomes: str
    conditions: str
    registered_sources: str
    engine: str
    engine_version: str
    algorithm: str
    status: str
    identified_expression: str
    derivation_trace: str
    numerical_estimability: str
    expression_variable_count: int | None
    original_node_count: int
    identification_node_count: int
    note: str

    def record(self) -> dict[str,object]: return asdict(self)


def engine_status() -> dict[str,object]:
    try:
        from y0.algorithm.identify import identify_outcomes  # noqa:F401
        from y0.graph import NxMixedGraph  # noqa:F401
        return {"available":True,"engine":"y0","version":importlib.metadata.version("y0"),
                "algorithms":["ID","IDC"],"vendor_path":str(VENDOR)}
    except Exception as exc:
        return {"available":False,"engine":"y0","version":"","algorithms":[],"vendor_path":str(VENDOR),"error":repr(exc)}


def _base(graph:CausalGraph,treatments, outcomes, conditions, **kwargs) -> IdentificationResult:
    treatments=tuple(sorted(treatments)); outcomes=tuple(sorted(outcomes)); conditions=tuple(sorted(conditions))
    query=f"P({', '.join(outcomes)} | do({', '.join(treatments)}=0)"+(f", {', '.join(conditions)}" if conditions else "")+")"
    return IdentificationResult(graph.graph_id,graph.signature,query,";".join(treatments),";".join(outcomes),";".join(conditions),
      "intact observational distribution; fixed-zero KO summaries are not registered as raw interventional distributions",
      kwargs.get("engine","y0"),kwargs.get("engine_version",""),"IDC" if conditions else "ID",kwargs.get("status","inconclusive"),
      kwargs.get("identified_expression",""),kwargs.get("derivation_trace",""),kwargs.get("numerical_estimability","unavailable"),
      kwargs.get("expression_variable_count"),len(graph.nodes),kwargs.get("identification_node_count",len(graph.nodes)),kwargs.get("note",""))


def _to_y0(graph:CausalGraph):
    from y0.dsl import Variable
    from y0.graph import NxMixedGraph

    cycle=graph.cycle()
    if cycle: raise ValueError("unsupported cyclic graph: "+" -> ".join(cycle))
    observed=set(graph.nodes)-set(graph.latent)
    variables={n:Variable(n) for n in observed}
    def observed_descendants_through_latent(start: str) -> set[str]:
        reached=set(); stack=list(graph.children(start)); seen=set()
        while stack:
            node=stack.pop()
            if node in seen: continue
            seen.add(node)
            if node in observed:
                reached.add(node)
            elif node in graph.latent:
                stack.extend(graph.children(node))
        return reached

    # Latent-project the DAG into an ADMG. This supports mechanistic activity
    # states with latent parents/children rather than only parentless hidden
    # common causes.
    directed=[]; bidirected=set()
    for source in observed:
        for target in observed_descendants_through_latent(source):
            directed.append((variables[source],variables[target]))
    for latent in graph.latent:
        descendants=sorted(observed_descendants_through_latent(latent))
        for i,a in enumerate(descendants):
            for b in descendants[i+1:]: bidirected.add((variables[a],variables[b]))
    mixed=NxMixedGraph.from_edges(nodes=variables.values(),directed=directed,undirected=sorted(bidirected,key=lambda e:(e[0].name,e[1].name)))
    return mixed,variables


def identify_effect(
    graph:CausalGraph,
    treatments:Iterable[str],
    outcomes:Iterable[str],
    conditions:Iterable[str]=(),
    *,
    observed_proxies:set[str]|None=None,
    sample_count:int=0,
) -> IdentificationResult:
    treatments=set(treatments); outcomes=set(outcomes); conditions=set(conditions)
    status=engine_status(); version=str(status.get("version",""))
    if not status["available"]:
        return _base(graph,treatments,outcomes,conditions,engine_version=version,status="engine_unavailable",
          derivation_trace="trace unavailable",note=str(status.get("error","y0 is not installed")))
    unknown=(treatments|outcomes|conditions)-graph.nodes
    forbidden=(treatments|outcomes|conditions)&graph.latent
    overlap=treatments&(outcomes|conditions) | outcomes&conditions
    if unknown or forbidden or overlap or not treatments or not outcomes:
        detail=[]
        if unknown: detail.append("unknown nodes: "+", ".join(sorted(unknown)))
        if forbidden: detail.append("latent query variables: "+", ".join(sorted(forbidden)))
        if overlap: detail.append("overlapping query roles: "+", ".join(sorted(overlap)))
        if not treatments or not outcomes: detail.append("treatments and outcomes are required")
        return _base(graph,treatments,outcomes,conditions,engine_version=version,status="invalid_query",derivation_trace="not run",note="; ".join(detail))
    # Keep the full graph in the registry, but marginalize nodes outside the
    # treatment/outcome relevance set before symbolic identification. Every
    # ancestor of an outcome/condition (including latent ancestors) remains.
    relevant=treatments|outcomes|conditions|graph.ancestors(treatments|outcomes|conditions)
    reduced=CausalGraph(f"{graph.graph_id}__id_relevant",set(relevant),
      {(a,b) for a,b in graph.edges if a in relevant and b in relevant},graph.latent&relevant,
      {**graph.metadata,"identification_reduction_of":graph.signature})
    try:
        mixed,variables=_to_y0(reduced)
    except ValueError as exc:
        return _base(graph,treatments,outcomes,conditions,engine_version=version,status="unsupported_graph",derivation_trace="not run",
          identification_node_count=len(relevant),note=str(exc))
    try:
        from y0.algorithm.identify import identify_outcomes
        expression=identify_outcomes(mixed,treatments={variables[x] for x in treatments},outcomes={variables[x] for x in outcomes},
                                     conditions={variables[x] for x in conditions} if conditions else None)
    except Exception as exc:
        return _base(graph,treatments,outcomes,conditions,engine_version=version,status="identification_error",identification_node_count=len(relevant),
          derivation_trace="engine raised an exception",note=f"{type(exc).__name__}: {exc}")
    if expression is None:
        return _base(graph,treatments,outcomes,conditions,engine_version=version,status="certified_nonidentification",identification_node_count=len(relevant),
          derivation_trace=f"y0 {version} returned no estimand using {'IDC' if conditions else 'ID'}",
          note="The result is graph/query-specific; it is not evidence that every causal query is nonidentifiable.")
    expression_text=str(expression); expression_variables={v.name for v in expression.get_variables()}
    missing_proxies=expression_variables-set(observed_proxies or ())
    if missing_proxies:
        numerical="symbolically_identified_but_missing_observed_proxies"
    elif sample_count < max(8,len(expression_variables)+3):
        numerical="symbolically_identified_but_numerically_unsupported_low_n"
    else:
        numerical="symbolically_identified; numerical distribution estimator not implemented in covariance workflow"
    return _base(graph,treatments,outcomes,conditions,engine_version=version,status="identified",identified_expression=expression_text,
      derivation_trace=(f"Ancestral relevance reduction retained {len(relevant)}/{len(graph.nodes)} registry nodes; "
        f"y0 {version} {'IDC' if conditions else 'ID'} completed on ADMG converted from graph signature {graph.signature}; "
        "internal rule-by-rule certificate unavailable from engine"),
      numerical_estimability=numerical,expression_variable_count=len(expression_variables),identification_node_count=len(relevant),
      note=("All three do-calculus rule types are delegated to the established ID/IDC implementation. "
            "Symbolic identification is kept separate from numerical estimation and graph discrimination."))

