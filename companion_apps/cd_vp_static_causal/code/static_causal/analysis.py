from __future__ import annotations

import csv
from dataclasses import dataclass
import itertools
import json
import hashlib
import math
import random
from pathlib import Path

import numpy as np
import pandas as pd

from .data import InputBundle, DDAVP_NODE, TIME_NODE, INTERACTION_NODE
from .graph import CausalGraph
from .stats import PartialCorrelationResult, bh_adjust, log_bic_evidence, partial_correlation
from .identification import identify_effect


@dataclass(frozen=True)
class Query:
    query_id: str
    x: str
    y: str
    conditioned: tuple[str,...]

    def record(self) -> dict[str,object]:
        return {"query_id":self.query_id,"x":self.x,"y":self.y,"conditioned":";".join(self.conditioned),"conditioning_size":len(self.conditioned)}


def build_query_bank(bundle: InputBundle, budget: int, max_conditioning: int, seed: int) -> list[Query]:
    measured=sorted(node for node in bundle.matrix
      if (bundle.node_metadata.get(node) or {}).get("node_type") in {"observed_phosphosite","observed_protein_signal"})
    if len(measured)<2: return []
    rng=random.Random(seed)
    clusters=np.asarray([str(sample.get("block_id",sample.get("sample_id",""))) for sample in bundle.samples],object)
    def overlap_blocks(nodes: tuple[str,...]) -> int:
        mask=np.ones(len(bundle.samples),dtype=bool)
        for node in nodes:
            mask &= np.isfinite(bundle.matrix[node])
        return len(set(clusters[mask].tolist()))
    pairs=list(itertools.combinations(measured,2)); rng.shuffle(pairs)
    candidate_edges=[g.edges for g in bundle.candidates]
    activity_for={node:str((bundle.node_metadata.get(node) or {}).get("activity_node_id",node)) for node in measured}
    site_for_activity={activity:bundle.representative_sites.get(gene) for gene,activity in bundle.activity_nodes.items()}
    activity_degree={activity:0 for activity in activity_for.values()}
    for graph in bundle.candidates:
        for a,b in graph.edges:
            if a in activity_degree and b in graph.latent: activity_degree[a]+=1
            if b in activity_degree and a in graph.latent: activity_degree[b]+=1
    degree={node:activity_degree.get(activity_for[node],0) for node in measured}
    scored=[]
    for a,b in pairs:
        complete_blocks=overlap_blocks((a,b))
        if complete_blocks<=max_conditioning+3:
            continue
        aa,bb=activity_for[a],activity_for[b]
        assertions=sum(((aa,bb) in edges or (bb,aa) in edges) for edges in candidate_edges)
        disagreement=min(assertions,len(candidate_edges)-assertions)
        local_degree=degree.get(a,0)+degree.get(b,0)
        scored.append((disagreement,complete_blocks,local_degree,rng.random(),a,b))
    scored.sort(reverse=True)
    queries=[]; seen=set()
    def add_query(x: str, y: str, cond: tuple[str,...]) -> bool:
        key=(x,y,cond)
        if key in seen: return False
        seen.add(key); queries.append(Query(f"Q{len(queries)+1:04d}",x,y,cond)); return len(queries)>=budget

    # Randomized treatment-to-readout implications are the most directly
    # causal tests in the joint model, so reserve the first quarter of the
    # bounded bank for them.
    treatment_budget=max(8,budget//4)
    # Guarantee that the known entry point and canonical collecting-duct
    # readout are considered before generic high-degree readouts.
    priority_genes={"PRKACA","AQP2"}
    treatment_measured=[site for site in measured if overlap_blocks((DDAVP_NODE,site))>=8]
    ranked_sites=sorted(treatment_measured,key=lambda site:(
      str((bundle.node_metadata.get(site) or {}).get("gene","")) in priority_genes,
      degree.get(site,0),site),reverse=True)
    for site in ranked_sites:
        for cond in ((),(TIME_NODE,)):
            if overlap_blocks((DDAVP_NODE,site,*cond))<=len(cond)+3: continue
            if add_query(DDAVP_NODE,site,cond): return queries
            if len(queries)>=treatment_budget: break
        if len(queries)>=treatment_budget: break
    for _,_,_,_,a,b in scored:
        conditions=[(),(DDAVP_NODE,)]
        if max_conditioning>=2: conditions.append((DDAVP_NODE,TIME_NODE))
        if max_conditioning>=3: conditions.append((DDAVP_NODE,TIME_NODE,INTERACTION_NODE))
        if max_conditioning:
            neighbor_activities=set()
            for graph in bundle.candidates:
                aa,bb=activity_for[a],activity_for[b]
                neighbor_activities |= (graph.parents(aa)|graph.parents(bb)|graph.children(aa)|graph.children(bb))
            neighbors=sorted({site_for_activity.get(node) for node in neighbor_activities if site_for_activity.get(node)}-{a,b})[:6]
            conditions += [(DDAVP_NODE,n) for n in neighbors]
        for cond in conditions:
            if overlap_blocks((a,b,*tuple(cond)))<=len(cond)+3: continue
            if add_query(a,b,tuple(cond)): return queries
    return queries


def run_local_tests(bundle: InputBundle, queries: list[Query], equivalence_margin: float) -> list[PartialCorrelationResult]:
    results=[]
    clusters=np.asarray([str(sample.get("block_id",sample.get("sample_id",""))) for sample in bundle.samples],object)
    for q in queries:
        z=np.column_stack([bundle.matrix[n] for n in q.conditioned]) if q.conditioned else None
        result=partial_correlation(bundle.matrix[q.x],bundle.matrix[q.y],z,(q.x,q.y,";".join(q.conditioned)),clusters=clusters)
        if result.status=="inconclusive_low_power" and math.isfinite(result.ci_low) and result.ci_low>=-equivalence_margin and result.ci_high<=equivalence_margin:
            result.status="supported_zero"; result.detail=f"equivalence CI contained in ±{equivalence_margin:g}"
        results.append(result)
    bh_adjust(results)
    for r in results:
        if r.status=="supported_zero" and math.isfinite(r.q_value):
            r.detail += "; FDR applies to nonzero tests and does not convert this to a posterior probability"
    return results


def score_candidates(bundle: InputBundle, queries: list[Query], tests: list[PartialCorrelationResult], complexity_penalty: float):
    test_map={q.query_id:t for q,t in zip(queries,tests)}; ledger=[]; scores=[]; predictions={}
    for graph in bundle.candidates:
        cycle=graph.cycle(); composite=0.0; contradictions=0; agreements=0; available=0; graph_pred={}
        if cycle:
            scores.append({"graph_id":graph.graph_id,"status":"unsupported_cyclic","cycle":" -> ".join(cycle),"composite_score":math.nan,
              "edge_count":len(graph.edges),"agreements":0,"contradictions":0,"available_queries":0,
              "interpretation":"preserved but not scored with DAG d-separation"})
            predictions[graph.graph_id]=graph_pred; continue
        for q in queries:
            sep,explanation=graph.d_separated(q.x,q.y,q.conditioned); graph_pred[q.query_id]=sep
            test=test_map[q.query_id]
            row={**q.record(),"graph_id":graph.graph_id,"predicted":"independent" if sep else "dependent" if sep is False else "unavailable",
                 "graph_explanation":explanation,"observed_status":test.status,"partial_r":test.partial_r,"p_value":test.p_value,"q_value":test.q_value,
                 "n_complete":test.n_complete,"effective_n":test.effective_n,"ledger_call":"uninformative","composite_contribution":0.0}
            if sep is not None and math.isfinite(test.partial_r):
                available+=1; contribution=log_bic_evidence(test.partial_r,test.effective_n,sep)
                row["composite_contribution"]=contribution; composite+=contribution
                if (sep and test.status=="supported_nonzero") or (not sep and test.status=="supported_zero"):
                    row["ledger_call"]="contradiction"; contradictions+=1
                elif (sep and test.status=="supported_zero") or (not sep and test.status=="supported_nonzero"):
                    row["ledger_call"]="agreement"; agreements+=1
            ledger.append(row)
        structural_edges=int(graph.metadata.get("activity_edge_count",len(graph.edges)))
        penalty=complexity_penalty*structural_edges*math.log(max(len(bundle.samples),2))/max(len(bundle.nodes),1)
        scores.append({"graph_id":graph.graph_id,"status":"scored_composite_not_posterior","composite_score":composite-penalty,
          "raw_local_score":composite,"complexity_penalty":penalty,"edge_count":len(graph.edges),"activity_edge_count":structural_edges,"agreements":agreements,
          "contradictions":contradictions,"available_queries":available,"registry_nodes":len(bundle.nodes),"registry_edges":len(bundle.edges),
          "interpretation":"overlapping local tests; ranking score is not a likelihood, posterior, or global p-value"})
        predictions[graph.graph_id]=graph_pred
    scores.sort(key=lambda x:float(x["composite_score"]) if math.isfinite(float(x["composite_score"])) else -math.inf,reverse=True)
    return ledger,scores,predictions


def ambiguity_classes(scores: list[dict[str,object]], predictions: dict[str,dict[str,bool|None]], graphs: list[CausalGraph]|None=None) -> list[dict[str,object]]:
    graph_map={g.graph_id:g for g in (graphs or [])}
    groups={}
    for score in scores:
        gid=str(score["graph_id"]); signature=tuple(sorted(predictions.get(gid,{}).items()))
        groups.setdefault(signature,[]).append(gid)
    rows=[]
    for i,(signature,gids) in enumerate(groups.items(),1):
        agreed=set.intersection(*(graph_map[g].edges for g in gids)) if gids and all(g in graph_map for g in gids) else set()
        rows.append({"ambiguity_class":f"A{i}","graph_count":len(gids),"graphs":";".join(gids),"query_prediction_signature":json.dumps(signature),
          "agreed_directed_edges":";".join(f"{a}->{b}" for a,b in sorted(agreed)),
          "unresolved_graph_pairs":";".join(f"{a}|{b}" for a,b in itertools.combinations(gids,2)),
          "scope_note":"relative to supplied candidates, assumptions, observed panel, interventions, and bounded query bank"})
    return rows


def ghost_proposals(bundle: InputBundle, ledger: list[dict[str,object]], limit: int=50) -> list[dict[str,object]]:
    probability={(min(a,b),max(a,b)):p for a,b,p in bundle.edges}
    rows=[]
    observed_types={"observed_phosphosite","observed_protein_signal"}
    contradictions=sorted((r for r in ledger if r["ledger_call"]=="contradiction" and
      (bundle.node_metadata.get(str(r["x"])) or {}).get("node_type") in observed_types and
      (bundle.node_metadata.get(str(r["y"])) or {}).get("node_type") in observed_types),
      key=lambda r:abs(float(r["partial_r"])),reverse=True)
    for row in contradictions[:limit]:
        x,y=str(row["x"]),str(row["y"])
        gx=str((bundle.node_metadata.get(x) or {}).get("gene",x)); gy=str((bundle.node_metadata.get(y) or {}).get("gene",y))
        direct=probability.get((min(gx,gy),max(gx,gy)))
        base={"graph_id":row["graph_id"],"query_id":row["query_id"],"failure":f"{x} _||_ {y} | {row['conditioned']} contradicted",
              "repaired_failure_count":"requires_refit","new_failure_count":"requires_refit"}
        if direct is not None:
            rows.append({**base,"proposal_type":"unused_existing_edge","proposal":f"evaluate activity({gx})->activity({gy}) and the reverse","registry_probability":direct,
              "falsifiability_loss":"adds one asserted edge"})
        ghost="L_"+hashlib.sha256(f"{min(x,y)}|{max(x,y)}|common".encode()).hexdigest()[:8]
        rows.append({**base,"proposal_type":"anonymous_latent_common_cause","proposal":f"{ghost}->{x};{ghost}->{y}","registry_probability":"",
          "falsifiability_loss":"may explain marginal association while reducing testable independences"})
        mediator="M_"+hashlib.sha256(f"{x}|{y}|mediator".encode()).hexdigest()[:8]
        rows.append({**base,"proposal_type":"anonymous_mediator","proposal":f"{x}->{mediator}->{y}","registry_probability":"",
          "falsifiability_loss":"adds an unmeasured mechanism and removes at least one independence"})
    return rows


def _weights(scores: list[dict[str,object]]) -> dict[str,float]:
    valid=[s for s in scores if math.isfinite(float(s["composite_score"]))]
    if not valid: return {str(s["graph_id"]):1/max(len(scores),1) for s in scores}
    high=max(float(s["composite_score"]) for s in valid); raw={str(s["graph_id"]):math.exp(max(float(s["composite_score"])-high,-700)) for s in valid}; total=sum(raw.values())
    return {k:v/total for k,v in raw.items()}


def ko_discrimination(bundle: InputBundle, queries: list[Query], scores: list[dict[str,object]], max_kos: int|None=None):
    graphs=[g for g in bundle.candidates if not g.cycle()]
    weights=_weights(scores)
    feasible=sorted(bundle.activity_nodes.items()) if bundle.activity_nodes else [(n,n) for n in sorted(bundle.nodes)]
    if max_kos: feasible=feasible[:max_kos]
    pair_rows=[]; ko_rows=[]; signatures={}; surgery_rows=[]
    for gene,k in feasible:
        per_graph={}; surgeries={}
        for graph in graphs:
            mg,audit=graph.hard_intervention(k); surgeries[graph.graph_id]=audit
            surgery_rows.append({"knockout":gene,"activity_node":k,"graph_id":graph.graph_id,"intervention":audit["intervention"],
              "target_was_latent":audit["target_was_latent"],"removed_incoming_edges":";".join(audit["removed_incoming_edges"]),
              "preserved_outgoing_edges":";".join(audit["preserved_outgoing_edges"]),"fixed_node_excluded":True,
              "biological_zero_warning":audit["biological_zero_warning"]})
            sig=[]
            for q in queries:
                if k in {q.x,q.y,*q.conditioned}: sig.append((q.query_id,None)); continue
                sep,_=mg.d_separated(q.x,q.y,q.conditioned)
                sig.append((q.query_id,sep))
            per_graph[graph.graph_id]=tuple(sig)
        signatures[gene]=per_graph
        groups={}
        for gid,sig in per_graph.items(): groups.setdefault(sig,[]).append(gid)
        singleton=[ids[0] for ids in groups.values() if len(ids)==1]
        n_unique=len(singleton); w_unique=sum(weights.get(g,0) for g in singleton)
        pair_split=0; one_sided=0
        for g1,g2 in itertools.combinations(graphs,2):
            diffs=[qid for (qid,p1),(_,p2) in zip(per_graph[g1.graph_id],per_graph[g2.graph_id]) if p1 is not None and p2 is not None and p1!=p2]
            d1=g1.descendants({k}); d2=g2.descendants({k})
            # A readout constrained as a nondescendant in only one graph is a
            # one-sided invariance opportunity, never an automatic split.
            opp=any(((q.x in d1)!=(q.x in d2)) or ((q.y in d1)!=(q.y in d2)) for q in queries if k not in {q.x,q.y})
            if diffs: pair_split+=1
            if opp: one_sided+=1
            pair_rows.append({"knockout":gene,"activity_node":k,"graph_a":g1.graph_id,"graph_b":g2.graph_id,"mutually_distinguishable":bool(diffs),
              "distinguishing_queries":";".join(diffs),"requires_faithfulness":bool(diffs),"one_sided_invariance_opportunity":opp,
              "singleton_credit_from_opportunity":False})
        ko_rows.append({"knockout":gene,"activity_node":k,"N_unique":n_unique,"W_unique":w_unique,"pairwise_splits":pair_split,
          "one_sided_invariance_opportunities":one_sided,"ambiguity_group_sizes":";".join(map(str,sorted(map(len,groups.values())))),
          "fixed_target_excluded":True,"incoming_edges_removed":sum(len(s["removed_incoming_edges"]) for s in surgeries.values()),
          "outgoing_edges_preserved":sum(len(s["preserved_outgoing_edges"]) for s in surgeries.values())})
    ko_rows.sort(key=lambda r:(r["N_unique"],r["W_unique"],r["pairwise_splits"]),reverse=True)
    return ko_rows,pair_rows,signatures,surgery_rows


def batch_designs(ko_rows: list[dict[str,object]], signatures: dict[str,dict[str,tuple]], scores: list[dict[str,object]], max_size: int=3):
    weights=_weights(scores); selected=[]; rows=[]
    for step in range(1,max_size+1):
        best=None
        for ko in list(signatures):
            if ko in selected: continue
            trial=selected+[ko]; groups={}
            gids=list(signatures[ko])
            for gid in gids:
                signature=tuple(signatures[k][gid] for k in trial); groups.setdefault(signature,[]).append(gid)
            singleton=[g[0] for g in groups.values() if len(g)==1]
            score=(len(singleton),sum(weights.get(g,0) for g in singleton),-max(map(len,groups.values())))
            if best is None or score>best[0]: best=(score,ko,groups,singleton)
        if best is None: break
        score,ko,groups,singleton=best; selected.append(ko)
        rows.append({"objective":"task4_singleton_count","batch_size":step,"added_knockout":ko,"batch":";".join(selected),"N_unique":score[0],"W_unique":score[1],
          "largest_ambiguity_class":-score[2],"singleton_graphs":";".join(singleton),"optimization_rule":"lexicographic N_unique then W_unique"})
    selected=[]
    for step in range(1,max_size+1):
        best=None
        for ko in signatures:
            if ko in selected: continue
            trial=selected+[ko]; gids=list(signatures[ko]); split=0
            for a,b in itertools.combinations(gids,2):
                if any(signatures[k][a]!=signatures[k][b] for k in trial): split+=1
            score=(split,)
            if best is None or score>best[0]: best=(score,ko)
        if best is None: break
        _,ko=best; selected.append(ko)
        rows.append({"objective":"task2_pair_coverage","batch_size":step,"added_knockout":ko,"batch":";".join(selected),
          "pairwise_splits":best[0][0],"note":"greedy justified approximation; distinct from singleton objective"})
    return rows


def do_calculus_rows(bundle: InputBundle, queries: list[Query], budget: int=24) -> list[dict[str,object]]:
    rows=[]
    combinations=itertools.product(bundle.candidates,queries)
    for graph,query in itertools.islice(combinations,budget):
        result=identify_effect(graph,{query.x},{query.y},set(query.conditioned),observed_proxies=set(bundle.matrix),sample_count=len(bundle.samples))
        row=result.record(); row["query_id"]=query.query_id
        query_type=(bundle.node_metadata.get(query.x) or {}).get("node_type")
        if query_type=="observed_phosphosite" and not graph.children(query.x):
            # In the explicit biology model, phosphosite nodes are terminal
            # measurements. Surgically changing one cannot propagate to any
            # other node unless a future model supplies an outgoing mechanism.
            conditions=", ".join(query.conditioned)
            row.update({"engine":"graph surgery","algorithm":"terminal-readout rule","status":"identified",
              "identified_expression":f"P({query.y}"+(f" | {conditions}" if conditions else "")+")",
              "derivation_trace":"The intervention target is a terminal observed phosphosite with no outgoing arrows; graph surgery leaves the outcome-generating graph unchanged.",
              "numerical_estimability":"observational distribution available subject to the matched-block small-sample limitation",
              "note":"This is a null propagation result for a measurement node, not evidence that the corresponding protein activity has no causal effect."})
            row["intervention_semantics"]="site-specific phosphorylation intervention on a terminal measurement node"
            row["latent_activity_effect_status"]="does not estimate a protein-activity intervention; activity is a separate latent node"
        elif query.x==DDAVP_NODE:
            # dDAVP was randomized within each time×replicate block. Its
            # total effect therefore has a design-based adjustment formula
            # even when a generic IDC implementation struggles with the
            # deterministic treatment×time node.
            if row["status"]=="identification_error" and set(query.conditioned).issubset({TIME_NODE,INTERACTION_NODE}):
                conditions=", ".join(query.conditioned)
                row.update({"engine":"randomized experimental design","algorithm":"design g-formula",
                  "status":"identified","identified_expression":f"P({query.y} | {DDAVP_NODE}"+(f", {conditions}" if conditions else "")+")",
                  "derivation_trace":"Treatment was randomized within matched time×replicate blocks; adjustment uses only preregistered design variables.",
                  "numerical_estimability":"identified in principle; matched-block numerical effect estimator is not yet implemented",
                  "note":"Design-based fallback used because the generic IDC engine failed on a deterministic interaction node."})
            row["intervention_semantics"]="randomized dDAVP intervention entering the biological activity network only through latent PRKACA/PKA activity"
            row["latent_activity_effect_status"]="total dDAVP effect is identifiable under the randomized-design and PKA-only-entry assumptions; PKA mediation itself remains latent"
        else:
            row["intervention_semantics"]="site-specific phosphorylation intervention; not a protein-activity knockout"
            row["latent_activity_effect_status"]="not directly identifiable from the observational distribution because protein activity is explicitly latent"
        rows.append(row)
    return rows

