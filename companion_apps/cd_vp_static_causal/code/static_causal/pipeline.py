from __future__ import annotations

import json
import html as html_lib
from pathlib import Path
import time
from typing import Callable
from collections import Counter

import pandas as pd

from .analysis import (ambiguity_classes,batch_designs,build_query_bank,do_calculus_rows,ghost_proposals,
                       ko_discrimination,run_local_tests,score_candidates)
from .data import load_inputs,write_registries,DDAVP_NODE
from .identification import engine_status


def _write(rows, path: Path):
    pd.DataFrame(rows).to_csv(path,sep="\t",index=False)


def candidate_graph_payload(bundle, ledger, scores) -> dict[str,object]:
    """Build a compact, auditable graph-explorer payload.

    Candidate arrows originate in the V1 integrated-evidence graph. The local
    phosphoproteomic overlay reports agreement with graph implications, not
    proof of arrow direction or direct causation.
    """
    score_map={str(row["graph_id"]):row for row in scores}
    tests_by_graph_pair={}
    rows_by_graph={}
    for row in ledger:
        gid=str(row.get("graph_id","")); pair=tuple(sorted((str(row.get("x","")),str(row.get("y","")))))
        rows_by_graph.setdefault(gid,[]).append(row)
        tests_by_graph_pair.setdefault((gid,pair),[]).append(row)
    graphs=[]
    for graph in bundle.candidates:
        edge_rows=[]; counts=Counter()
        for source,target in sorted(graph.edges):
            source_meta=bundle.node_metadata.get(source,{})
            target_meta=bundle.node_metadata.get(target,{})
            if source_meta.get("node_type")==target_meta.get("node_type")=="latent_activity": relation="activity_causal"
            elif source_meta.get("node_type")=="latent_activity" and target_meta.get("node_type") in {"observed_phosphosite","observed_protein_signal"}: relation="activity_to_observed_measurement"
            elif target_meta.get("node_type")=="latent_activity": relation="experimental_input"
            else: relation="design_relation"
            if relation=="activity_causal":
                source_site=bundle.representative_sites.get(str(source_meta.get("gene","")))
                target_site=bundle.representative_sites.get(str(target_meta.get("gene","")))
                related=tests_by_graph_pair.get((graph.graph_id,tuple(sorted((source_site,target_site)))),[]) if source_site and target_site else []
            else:
                related=[]
            calls={str(row.get("ledger_call","")) for row in related}
            if relation=="activity_to_observed_measurement": status="measurement_assumption"
            elif relation in {"experimental_input","design_relation"}: status="design_assumption"
            elif "contradiction" in calls: status="contradicted"
            elif "agreement" in calls: status="supported"
            elif related: status="tested_inconclusive"
            else: status="untested"
            counts[status]+=1
            edge_rows.append({"source":source,"target":target,"status":status,"relation":relation,
              "query_ids":[str(row.get("query_id","")) for row in related],
              "observed_statuses":sorted({str(row.get("observed_status","")) for row in related})})
        checks=[]
        for row in rows_by_graph.get(graph.graph_id,[]):
            x_meta=bundle.node_metadata.get(str(row.get("x","")),{})
            y_meta=bundle.node_metadata.get(str(row.get("y","")),{})
            conditioned_ids=[value for value in str(row.get("conditioned","")).split(";") if value]
            conditioned_label="; ".join(str((bundle.node_metadata.get(value) or {}).get("short_label",value)) for value in conditioned_ids)
            checks.append({"query_id":row.get("query_id",""),"x":row.get("x",""),"y":row.get("y",""),
              "x_label":x_meta.get("short_label",row.get("x","")),"y_label":y_meta.get("short_label",row.get("y","")),
              "conditioned":row.get("conditioned",""),"conditioned_label":conditioned_label,"predicted":row.get("predicted",""),
              "observed_status":row.get("observed_status",""),"ledger_call":row.get("ledger_call",""),
              "partial_r":row.get("partial_r",""),"q_value":row.get("q_value","")})
        node_rows=[]
        for node_id in sorted(graph.nodes):
            meta=bundle.node_metadata.get(node_id,{"node_id":node_id,"label":node_id,"short_label":node_id,"node_type":"unknown"})
            node_rows.append({"id":node_id,"label":meta.get("label",node_id),"short_label":meta.get("short_label",node_id),
              "node_type":meta.get("node_type","unknown"),"gene":meta.get("gene",""),"site":meta.get("site",""),
              "observed":bool(meta.get("observed",False)),"latent":bool(meta.get("latent",False))})
        graphs.append({"graph_id":graph.graph_id,"signature":graph.signature,"node_count":len(graph.nodes),
          "edge_count":len(graph.edges),"cycle":graph.cycle(),"metadata":graph.metadata,
          "score":score_map.get(graph.graph_id,{}),"nodes":node_rows,"edges":edge_rows,
          "relationship_checks":checks,"edge_status_counts":dict(counts)})
    default_focus=DDAVP_NODE if DDAVP_NODE in bundle.node_metadata else ""
    return {"schema_version":3,"default_focus":default_focus,
      "semantics":{"activity_node":"Unobserved protein-activity state.",
        "phosphosite_node":"Observed site-specific phosphorylation response.",
        "protein_signal_node":"Observed standardized gene-level readout; phosphosite aggregate in phosphoproteomic regimes and total abundance in the Camkk2 regime.",
        "candidate_edge":"Activity-to-activity arrow admitted by the V1 integrated-evidence threshold and candidate orientation rule.",
        "measurement_assumption":"Activity-to-observed-readout link. It is a modeling convention, not evidence that the measured readout activates the protein.",
        "experimental_input":"Preregistered experimental-design constraint: dDAVP enters the biological activity network only through PRKACA/PKA activity.",
        "supported":"A bounded local phosphoproteomic test agreed with the graph implication; this does not prove arrow direction.",
        "contradicted":"A bounded local phosphoproteomic test contradicted the graph implication.",
        "tested_inconclusive":f"The relationship was tested but the available complete observations / {bundle.independent_block_count} dependence blocks were not decisive.",
        "untested":"No bounded local multiregime query directly assessed this edge."},
      "graphs":graphs}


def run(config: dict[str,object], output: str|Path, progress: Callable[[str,int],None]|None=None) -> dict[str,object]:
    out=Path(output).resolve(); out.mkdir(parents=True,exist_ok=True)
    update=lambda text,pct: progress(text,pct) if progress else None
    update("Loading the full V1 registry and multiregime protein measurements",5)
    bundle=load_inputs(config); write_registries(bundle,out)
    update("Building a shared, bounded conditional-independence query bank",20)
    queries=build_query_bank(bundle,int(config.get("query_budget",80)),int(config.get("max_conditioning_size",3)),int(config.get("random_seed",17)))
    _write([q.record() for q in queries],out/"query_bank.tsv")
    update("Testing standardized multiregime readouts with dependence-block inference",35)
    tests=run_local_tests(bundle,queries,float(config.get("equivalence_margin",.25)))
    _write([{"query_id":query.query_id,**test.record()} for query,test in zip(queries,tests)],out/"local_test_results.tsv")
    update("Scoring candidate graphs and writing the contradiction ledger",50)
    ledger,scores,predictions=score_candidates(bundle,queries,tests,float(config.get("complexity_penalty",.02)))
    _write(ledger,out/"constraint_ledger.tsv"); _write(scores,out/"candidate_scores.tsv")
    graph_view=candidate_graph_payload(bundle,ledger,scores)
    (out/"candidate_graph_view.json").write_text(json.dumps(graph_view,indent=2),encoding="utf-8")
    ambiguity=ambiguity_classes(scores,predictions,bundle.candidates); _write(ambiguity,out/"ambiguity_classes.tsv")
    update("Auditing cross-regime comparability and proposing local ghost repairs",60)
    cross=[]
    for regime in bundle.regimes:
        if regime.get("kind") not in {"intact","randomized_treatment"}: cross.append({"regime_id":regime.get("regime_id"),"status":"unavailable",
          "reason":"raw replicate-level KO measurements were not established; summary contrasts are not covariance matrices"})
    _write(cross,out/"cross_regime_tests.tsv"); _write(ghost_proposals(bundle,ledger),out/"ghost_proposals.tsv")
    update("Ranking feasible single knockouts with exact hard-intervention surgery",70)
    ko_query_budget=int(config.get("ko_query_budget",16))
    ko_rows,pair_rows,signatures,surgeries=ko_discrimination(bundle,queries[:ko_query_budget],scores,config.get("maximum_feasible_kos"))
    _write(pair_rows,out/"ko_discrimination.tsv"); _write(ko_rows,out/"ko_unique_counts.tsv")
    _write(surgeries,out/"intervention_surgery_audit.tsv")
    unresolved=[]
    for (a,b),rows in __import__('itertools').groupby(sorted(pair_rows,key=lambda r:(r['graph_a'],r['graph_b'])),key=lambda r:(r['graph_a'],r['graph_b'])):
        group=list(rows)
        if not any(r['mutually_distinguishable'] for r in group): unresolved.append({"graph_a":a,"graph_b":b,"status":"unresolved_by_allowed_KOs_and_query_bank"})
    _write(unresolved,out/"unresolved_graph_pairs.tsv")
    batches=batch_designs(ko_rows,signatures,scores,int(config.get("maximum_batch_size",3))); _write(batches,out/"batch_designs.tsv")
    update("Recording do-calculus availability and derivation status",90)
    do_rows=do_calculus_rows(bundle,queries,int(config.get("do_query_budget",24))); _write(do_rows,out/"do_calculus_derivations.tsv")
    id_engine=engine_status()
    manifest={"schema_version":3,"created_at":time.strftime("%Y-%m-%dT%H:%M:%S%z"),"status":"complete",
      "source_v1_run":str(bundle.source_run),"registry_node_count":len(bundle.nodes),"registry_edge_count":len(bundle.edges),
      "causal_node_count":len(bundle.node_metadata),"latent_activity_node_count":len(bundle.activity_nodes),
      "observed_phosphosite_node_count":sum(1 for meta in bundle.node_metadata.values() if meta.get("node_type")=="observed_phosphosite"),
      "observed_protein_signal_node_count":sum(1 for meta in bundle.node_metadata.values() if meta.get("node_type")=="observed_protein_signal"),
      "observed_readout_node_count":sum(len(v) for v in bundle.sites_by_gene.values()),
      "causal_representation":"latent protein activity + observed standardized multiregime protein readouts",
      "site_link_mode":bundle.site_link_mode,
      "observed_feature_count":len(bundle.features),"proxy_node_count":len(bundle.representative_sites),"intact_sample_count":len(bundle.samples),
      "independent_matched_block_count":bundle.independent_block_count,"normalization":bundle.normalization,
      "treatment_entry_node":DDAVP_NODE,"treatment_activity_target":bundle.design_edges[2][1] if len(bundle.design_edges)>2 else "",
      "candidate_graph_count":len(bundle.candidates),"query_count":len(queries),"feasible_ko_count":len(ko_rows),
      "ko_design_query_count":min(len(queries),ko_query_budget),
      "identification_engine":id_engine,
      "warnings":["Scores are composite compatibility rankings, not graph posteriors or global p-values.",
        "Protein activity is explicitly latent. Standardized gene-level measurements are observed readout nodes, not direct activity measurements.",
        "Unannotated activity-to-readout arrows are measurement-model assumptions and do not claim that the measured signal activates its protein.",
        "The primary matrix uses the workbook's gene-level protein readouts: phosphosite medians in phosphoproteomic regimes and total abundance for Camkk2 controls.",
        f"The joint model contains {len(bundle.samples)} measurement-level observations in {bundle.independent_block_count} dependence blocks; inferential effective n uses those blocks rather than claiming every column is independent.",
        "dDAVP, log2 sampling time, and their interaction are explicit observed design variables. dDAVP has only one direct biological activity target: PRKACA/PKA.",
        "KO workbooks lacking raw replicates are audited but not converted into covariance tests.",
        ("y0 ID/IDC performs symbolic identification; numerical effect estimation remains separate." if id_engine.get("available") else
         "General do-calculus is unavailable because the established y0 ID/IDC engine could not be loaded.")],
      "outputs":[p.name for p in sorted(out.iterdir()) if p.is_file() and p.name!="run_manifest.json"]}
    render_report(out,manifest,scores,ambiguity,ko_rows,batches,do_rows,graph_view)
    manifest["outputs"]=[p.name for p in sorted(out.iterdir()) if p.is_file() and p.name!="run_manifest.json"]
    manifest["outputs"].append("run_manifest.json")
    (out/"run_manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    update("Complete",100); return manifest


def graph_explorer_html(graph_view:dict[str,object]) -> str:
    data=json.dumps(graph_view,separators=(",",":")).replace("</","<\\/")
    template="""
<section id="graph-explorer"><h2>Biologically explicit candidate graph</h2>
<p>The multiregime model keeps all measurement columns separate and preserves their experiment, assay layer, clone, and pairing metadata. Explicit <strong>dDAVP</strong>, <strong>sampling-time</strong>, and <strong>dDAVP × time</strong> design nodes are defined only for the Kirby/Leo paired experiment; dDAVP enters the biological activity network only through latent PRKACA/PKA activity. Every modeled protein has a hidden <strong>activity</strong> node and an observed standardized protein-readout node.</p>
<div class="graph-controls"><label>Candidate graph<select id="graph-select"></select></label><label>Focus node<input id="graph-focus" list="graph-nodes" value=""></label><datalist id="graph-nodes"></datalist><label>Neighbor limit<select id="graph-limit"><option>20</option><option selected>50</option><option>100</option></select></label><button type="button" id="graph-go">Show neighborhood</button></div>
<div class="node-legend"><span class="treatment-key">T: randomized treatment</span><span class="design-key">D: time / derived design</span><span class="latent-key">A: latent activity</span><span class="site-key">R: observed protein readout</span></div>
<div class="graph-legend" aria-label="Relationship evidence legend"><span class="design-assumption">Experimental-design constraint</span><span class="supported">Activity relation agrees with local data</span><span class="contradicted">Activity relation contradicted locally</span><span class="tested-inconclusive">Tested, inconclusive</span><span class="untested">Not locally tested</span><span class="measurement-assumption">Measurement assumption</span></div>
<p id="graph-summary" class="graph-summary" aria-live="polite"></p>
<svg id="candidate-graph" viewBox="0 0 980 520" role="img" aria-labelledby="graph-title graph-description"><title id="graph-title">Biologically explicit candidate causal graph neighborhood</title><desc id="graph-description">Latent activity nodes and observed standardized protein-readout nodes around the selected entity.</desc></svg>
<div class="graph-caveat"><strong>Interpretation guardrail:</strong> dDAVP→PRKACA is a preregistered constraint for the Kirby/Leo experiment, not a relationship discovered from these measurements. The interaction node is deterministically derived from treatment and log2(time). An A→R arrow means “the standardized protein signal is treated as a readout of this protein's hidden activity”; it does not make the readout itself causal. For the phosphoproteomic regimes, R summarizes retained localized phosphosites; for Camkk2 it is total abundance.</div>
<details open><summary>Data-tested graph implications</summary><p id="relationship-summary"></p><div class="table-scroll"><table><thead><tr><th>Observed relationship</th><th>Conditioned on</th><th>Graph predicts</th><th>Data result</th><th>Assessment</th><th>partial r</th><th>q</th></tr></thead><tbody id="relationship-body"></tbody></table></div></details>
<script type="application/json" id="candidate-graph-data">__GRAPH_DATA__</script>
<script>
(()=>{
 const payload=JSON.parse(document.getElementById('candidate-graph-data').textContent);
 const select=document.getElementById('graph-select'), focusInput=document.getElementById('graph-focus'), limitInput=document.getElementById('graph-limit');
 const svg=document.getElementById('candidate-graph'), ns='http://www.w3.org/2000/svg';
 const statusLabel={supported:'agrees with local data',contradicted:'contradicted locally',tested_inconclusive:'tested, inconclusive',untested:'not locally tested',measurement_assumption:'measurement-model assumption',design_assumption:'experimental-design assumption'};
 function graph(){return payload.graphs.find(g=>g.graph_id===select.value)||payload.graphs[0]}
 function node(g,id){return g.nodes.find(n=>n.id===id)}
 function label(g,id){const n=node(g,id);return n?n.short_label:id}
 function add(tag,attrs,parent=svg){const node=document.createElementNS(ns,tag);Object.entries(attrs||{}).forEach(([k,v])=>node.setAttribute(k,v));parent.appendChild(node);return node}
 function populate(){payload.graphs.forEach(g=>{const o=document.createElement('option');o.value=g.graph_id;o.textContent=`${g.graph_id} (${g.edge_count.toLocaleString()} edges)`;select.appendChild(o)});focusInput.value=payload.default_focus||'';refreshNodes();draw()}
 function refreshNodes(){const list=document.getElementById('graph-nodes');list.replaceChildren();graph().nodes.forEach(n=>{const o=document.createElement('option');o.value=n.id;o.label=n.label;list.appendChild(o)})}
 function draw(){const g=graph(), requested=focusInput.value.trim();const match=g.nodes.find(n=>n.id===requested||n.label===requested||n.short_label===requested||n.gene===requested.toUpperCase());const focal=(match&&match.id)||(g.nodes.some(n=>n.id===payload.default_focus)?payload.default_focus:g.nodes[0].id);focusInput.value=focal;const limit=Number(limitInput.value)||50;
  const parents=g.edges.filter(e=>e.target===focal).sort(edgeSort).slice(0,Math.floor(limit/2));const used=new Set(parents.map(e=>e.source));
  const children=g.edges.filter(e=>e.source===focal&&!used.has(e.target)).sort(edgeSort).slice(0,Math.max(0,limit-parents.length));
  const visible=new Set([focal,...parents.map(e=>e.source),...children.map(e=>e.target)]);const edges=g.edges.filter(e=>visible.has(e.source)&&visible.has(e.target));
  const maxSide=Math.max(parents.length,children.length,1),height=Math.max(440,Math.min(1000,90+maxSide*38));svg.setAttribute('viewBox',`0 0 980 ${height}`);svg.replaceChildren();
  const defs=add('defs',{});const marker=add('marker',{id:'arrowhead',markerWidth:'8',markerHeight:'8',refX:'7',refY:'4',orient:'auto',markerUnits:'strokeWidth'},defs);add('path',{d:'M0,0 L8,4 L0,8 z',class:'arrowhead'},marker);
  const positions=new Map([[focal,{x:490,y:height/2}]]);place(parents.map(e=>e.source),210,height,positions);place(children.map(e=>e.target),770,height,positions);
  edges.forEach(e=>{const a=positions.get(e.source),b=positions.get(e.target);if(!a||!b)return;const line=add('line',{x1:a.x+(b.x>a.x?72:-72),y1:a.y,x2:b.x+(b.x>a.x?-72:72),y2:b.y,class:`graph-edge ${e.status} ${e.relation} ${e.source===focal||e.target===focal?'primary-edge':'secondary-edge'}`,'marker-end':'url(#arrowhead)'});const title=add('title',{},line);title.textContent=`${label(g,e.source)} → ${label(g,e.target)}: ${statusLabel[e.status]}`});
   [...positions].forEach(([name,p])=>{const n=node(g,name),type=n?n.node_type:'unknown';const group=add('g',{class:`graph-node ${type} ${name===focal?'focus-node':''}`,role:'button','aria-label':`Focus graph on ${label(g,name)}`});add('rect',{x:p.x-72,y:p.y-17,width:144,height:34,rx:(type==='observed_phosphosite'||type==='observed_protein_signal')?17:7},group);const text=add('text',{x:p.x,y:p.y+4,'text-anchor':'middle'},group);text.textContent=label(g,name);const title=add('title',{},group);title.textContent=n?n.label:name;group.addEventListener('click',()=>{focusInput.value=name;draw()})});
   const counts=g.edge_status_counts||{},focalNode=node(g,focal);document.getElementById('graph-summary').textContent=`${g.graph_id}: ${g.node_count.toLocaleString()} total explicit nodes and ${g.edge_count.toLocaleString()} arrows. Showing ${visible.size} nodes around ${focalNode?focalNode.label:focal}: ${parents.length} parents and ${children.length} children. Whole graph: ${counts.design_assumption||0} experimental-design arrows, ${counts.supported||0} locally agreeing activity arrows, ${counts.contradicted||0} contradicted, ${counts.tested_inconclusive||0} inconclusive, ${counts.untested||0} untested, and ${counts.measurement_assumption||0} activity-to-readout measurement links.`;
  renderChecks(g)
 }
 function edgeSort(a,b){const rank={design_assumption:0,measurement_assumption:1,supported:2,contradicted:3,tested_inconclusive:4,untested:5};return (rank[a.status]-rank[b.status])||a.source.localeCompare(b.source)||a.target.localeCompare(b.target)}
 function place(names,x,height,map){if(!names.length)return;const gap=(height-70)/(names.length+1);names.forEach((n,i)=>map.set(n,{x,y:35+gap*(i+1)}))}
  function renderChecks(g){const rows=[...g.relationship_checks].sort((a,b)=>callRank(a.ledger_call)-callRank(b.ledger_call)||a.query_id.localeCompare(b.query_id));const counts={agreement:0,contradiction:0,uninformative:0};rows.forEach(r=>counts[r.ledger_call]=(counts[r.ledger_call]||0)+1);document.getElementById('relationship-summary').textContent=`${counts.agreement||0} data-agreeing implications; ${counts.contradiction||0} contradictions; ${counts.uninformative||0} inconclusive/uninformative checks. Endpoints are observed standardized protein readouts; hidden activities mediate the graph implication.`;const body=document.getElementById('relationship-body');body.replaceChildren();rows.slice(0,40).forEach(r=>{const tr=document.createElement('tr');[`${r.x_label||r.x} ↔ ${r.y_label||r.y}`,r.conditioned_label||'—',r.predicted,r.observed_status,r.ledger_call,fmt(r.partial_r),fmt(r.q_value)].forEach((v,i)=>{const td=document.createElement('td');td.textContent=v; if(i===4)td.className=`call-${r.ledger_call}`;tr.appendChild(td)});body.appendChild(tr)})}
 function callRank(v){return v==='agreement'?0:v==='contradiction'?1:2} function fmt(v){const n=Number(v);return Number.isFinite(n)?n.toFixed(3):'—'}
 document.getElementById('graph-go').addEventListener('click',draw);focusInput.addEventListener('keydown',e=>{if(e.key==='Enter')draw()});select.addEventListener('change',()=>{refreshNodes();draw()});limitInput.addEventListener('change',draw);populate();
})();
</script></section>
"""
    return template.replace("__GRAPH_DATA__",data)


def render_report(out:Path,manifest,scores,ambiguity,kos,batches,do_rows,graph_view):
    esc=lambda value: html_lib.escape(str(value))
    def table(rows,cols):
        head="".join(f"<th>{esc(c)}</th>" for c in cols)
        body="".join("<tr>"+"".join(f"<td>{esc(r.get(c,''))}</td>" for c in cols)+"</tr>" for r in rows)
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    engine=manifest.get("identification_engine",{})
    engine_label=(f"{engine.get('engine','y0')} {engine.get('version','')}" if engine.get("available") else "Unavailable")
    id_counts=Counter(str(row.get("status","unknown")) for row in do_rows)
    algorithm_counts=Counter(str(row.get("algorithm","unknown")) for row in do_rows)
    id_summary=", ".join(f"{count} {status.replace('_',' ')}" for status,count in sorted(id_counts.items())) or "No queries attempted"
    algorithm_summary=", ".join(f"{count} {algorithm}" for algorithm,count in sorted(algorithm_counts.items()))
    id_preview=[{
      "query":row.get("query",""),"algorithm":row.get("algorithm",""),"status":row.get("status",""),
      "nodes retained":f"{row.get('identification_node_count','')}/{row.get('original_node_count','')}",
      "numerical status":row.get("numerical_estimability","")
    } for row in do_rows[:24]]
    html=f"""<!doctype html><meta charset='utf-8'><title>Static causal analysis</title><style>
body{{font:15px system-ui;max-width:1200px;margin:40px auto;color:#15332c;background:#f4f5ef}}section{{background:white;padding:22px;margin:18px 0;border-radius:14px}}table{{border-collapse:collapse;width:100%;font-size:13px}}th,td{{padding:8px;border-bottom:1px solid #dbe4df;text-align:left;vertical-align:top}}.warn{{background:#fff1d6;padding:12px;border-left:4px solid #d68b16}}
.graph-controls{{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:18px 0}}.graph-controls label{{display:grid;gap:5px;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#62736e;font-weight:700}}.graph-controls select,.graph-controls input{{min-width:190px;padding:9px;border:1px solid #bfcfc8;border-radius:8px;background:#fff;color:#15332c}}.graph-controls button{{padding:10px 14px;border:0;border-radius:8px;background:#08765d;color:white;font-weight:700;cursor:pointer}}.graph-legend,.node-legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;margin:10px 0}}.graph-legend span::before{{content:'';display:inline-block;width:24px;height:3px;margin-right:6px;vertical-align:middle;background:#aab7b2}}.graph-legend .design-assumption::before{{background:#1769aa;box-shadow:0 0 0 1px #1769aa}}.graph-legend .supported::before{{background:#14805e}}.graph-legend .contradicted::before{{background:#bc4a3f}}.graph-legend .tested-inconclusive::before{{background:#c58a22}}.graph-legend .untested::before{{background:#b8c3be}}.graph-legend .measurement-assumption::before{{background:#7b62a3;border-top:2px dashed white}}.node-legend span{{padding:5px 9px;border-radius:999px;font-weight:700}}.treatment-key{{background:#dcecff;color:#0b4e85}}.design-key{{background:#e8eaf4;color:#444f85}}.latent-key{{background:#dff1e9;color:#075b49}}.site-key{{background:#fce8c7;color:#7a4b00}}.graph-summary{{color:#51645e}}#candidate-graph{{width:100%;max-height:820px;border:1px solid #dbe4df;border-radius:10px;background:#fbfcf8}}.graph-edge{{stroke-width:2;fill:none}}.graph-edge.design_assumption{{stroke:#1769aa;stroke-width:3}}.graph-edge.supported{{stroke:#14805e}}.graph-edge.contradicted{{stroke:#bc4a3f}}.graph-edge.tested_inconclusive{{stroke:#c58a22;stroke-dasharray:6 4}}.graph-edge.untested{{stroke:#b8c3be;opacity:.55}}.graph-edge.measurement_assumption{{stroke:#7b62a3;stroke-dasharray:4 4}}.secondary-edge{{opacity:.24}}.arrowhead{{fill:#647670}}.graph-node rect{{fill:white;stroke:#9aada6}}.graph-node.observed_treatment rect{{fill:#dcecff;stroke:#1769aa}}.graph-node.observed_design rect,.graph-node.derived_design rect{{fill:#e8eaf4;stroke:#626da8}}.graph-node.latent_activity rect{{fill:#dff1e9;stroke:#08765d}}.graph-node.observed_phosphosite rect,.graph-node.observed_protein_signal rect{{fill:#fce8c7;stroke:#c27a0a}}.graph-node text{{font-size:12px;fill:#15332c;pointer-events:none}}.graph-node{{cursor:pointer}}.focus-node rect{{stroke:#052f27!important;stroke-width:3!important}}.focus-node text{{font-weight:700}}.graph-caveat{{margin:12px 0;padding:11px;background:#fff6de;border-left:4px solid #d89c22}}.table-scroll{{overflow:auto;max-height:520px}}.call-agreement{{color:#08765d;font-weight:700}}.call-contradiction{{color:#a3342b;font-weight:700}}.call-uninformative{{color:#687973}}details{{margin-top:16px}}summary{{font-weight:700;cursor:pointer}}@media(max-width:700px){{body{{margin:18px auto}}section{{padding:15px}}.graph-controls{{display:grid}}.graph-controls select,.graph-controls input{{width:100%}}}}
</style><h1>Static causal graph discrimination</h1><p>Joint 43-observation multiregime model</p>
<section><h2>Scope</h2><p>{manifest['registry_node_count']} proteins are expanded into <strong>{manifest.get('latent_activity_node_count',0)} latent activity nodes</strong>, <strong>{manifest.get('observed_readout_node_count',0)} observed standardized protein-readout nodes</strong>, and three explicit Kirby/Leo experimental-design nodes ({manifest.get('causal_node_count',0)} causal nodes total). The structural prior contains {manifest['registry_edge_count']} available V1 protein relationships. The panel contains <strong>{manifest['intact_sample_count']} measurement-level observations</strong> arranged as <strong>{manifest.get('independent_matched_block_count',0)} dependence blocks</strong>, including {manifest.get('normalization',{}).get('matched_kirby_block_count',0)} paired Kirby/Leo vehicle–dDAVP blocks.</p><p><strong>Normalization:</strong> {esc(manifest.get('normalization',{}).get('transform',''))}. <strong>Treatment encoding:</strong> dDAVP, log2(time in minutes), and dDAVP×time are defined only for Kirby/Leo observations; dDAVP's sole direct biological activity target is PRKACA/PKA.</p><div class='warn'>{'<br>'.join(esc(w) for w in manifest['warnings'])}</div></section>
{graph_explorer_html(graph_view)}
<section><h2>Candidate compatibility</h2>{table(scores,['graph_id','status','composite_score','activity_edge_count','edge_count','agreements','contradictions'])}</section>
<section><h2>Ambiguity classes</h2>{table(ambiguity,['ambiguity_class','graph_count','graphs'])}</section>
<section><h2>Leading knockout designs</h2>{table(kos[:25],['knockout','N_unique','W_unique','pairwise_splits','one_sided_invariance_opportunities'])}</section>
<section><h2>Greedy batches</h2>{table(batches,['batch_size','batch','N_unique','W_unique','largest_ambiguity_class'])}</section>
<section><h2>Causal identification</h2><p><strong>Engine:</strong> {esc(engine_label)} · <strong>Algorithms:</strong> {esc(algorithm_summary or 'none')}</p><p><strong>Results:</strong> {esc(id_summary)}. Identification is symbolic; numerical effect estimation is assessed separately.</p>{table(id_preview,['query','algorithm','status','nodes retained','numerical status'])}<p>The complete symbolic estimands and provenance are in <a href="do_calculus_derivations.tsv">do_calculus_derivations.tsv</a>.</p></section>
<section><h2>Downloads</h2><ul>{''.join(f'<li><a href="{p.name}">{p.name}</a></li>' for p in sorted(out.iterdir()) if p.is_file() and p.name!='report.html')}</ul></section>"""
    (out/"report.html").write_text(html,encoding="utf-8")

