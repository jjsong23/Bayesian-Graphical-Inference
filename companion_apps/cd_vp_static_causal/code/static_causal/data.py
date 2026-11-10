from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .graph import CausalGraph, orient_by_order


TIME_SHEETS = (("1 min",1,1),("2 min",2,2),("5 min",5,3),("15 min",15,4))
GENE, UNIPROT, SITE = "Gene Symbol", "Protein UniProt ID", "Position in Protein (A)"
DDAVP_NODE = "TREATMENT::DDAVP"
TIME_NODE = "DESIGN::LOG2_TIME_MINUTES"
INTERACTION_NODE = "DESIGN::DDAVP_X_LOG2_TIME"


def activity_node_id(gene: str) -> str:
    return f"ACTIVITY::{norm(gene)}"


def phosphosite_node_id(gene: str, uniprot: str, site: str) -> str:
    clean=lambda value: str(value).strip().replace("::","_")
    return f"PHOS::{norm(gene)}::{clean(uniprot)}::{clean(site)}"


def protein_signal_node_id(gene: str) -> str:
    return f"PROTEIN_SIGNAL::{norm(gene)}"


def norm(value: object) -> str:
    value=str(value).strip()
    return "" if not value or value.casefold() in {"nan","none","null"} else value.upper()


def sha256_file(path: Path) -> str:
    digest=hashlib.sha256()
    with path.open("rb") as handle:
        while block:=handle.read(1024*1024): digest.update(block)
    return digest.hexdigest()


@dataclass
class InputBundle:
    nodes: dict[str,dict[str,object]]
    edges: list[tuple[str,str,float]]
    features: list[dict[str,object]]
    matrix: dict[str,np.ndarray]
    proxy: dict[str,dict[str,object]]
    samples: list[dict[str,object]]
    regimes: list[dict[str,object]]
    candidates: list[CausalGraph]
    source_run: Path
    node_metadata: dict[str,dict[str,object]] = field(default_factory=dict)
    activity_nodes: dict[str,str] = field(default_factory=dict)
    representative_sites: dict[str,str] = field(default_factory=dict)
    sites_by_gene: dict[str,list[str]] = field(default_factory=dict)
    site_link_mode: str = "activity_to_phosphosite"
    design_nodes: dict[str,dict[str,object]] = field(default_factory=dict)
    design_edges: list[tuple[str,str]] = field(default_factory=list)
    independent_block_count: int = 0
    normalization: dict[str,object] = field(default_factory=dict)


def latest_v1_run(v1_root: Path) -> Path:
    runs=[]
    for path in (v1_root/"results"/"gui_runs").glob("*"):
        if (path/"supported_edges.tsv.gz").exists() and (path/"full_graph_node_statistics.tsv.gz").exists():
            runs.append(path)
    if not runs: raise FileNotFoundError("No complete V1 whole-network export was found")
    return max(runs,key=lambda p:(p/"supported_edges.tsv.gz").stat().st_mtime)


def load_registry(run: Path) -> tuple[dict[str,dict[str,object]],list[tuple[str,str,float]]]:
    nodes={}
    with gzip.open(run/"full_graph_node_statistics.tsv.gz","rt",encoding="utf-8") as handle:
        for row in csv.DictReader(handle,delimiter="\t"):
            symbol=norm(row["symbol"])
            if symbol: nodes[symbol]=dict(row)
    edges=[]
    with gzip.open(run/"supported_edges.tsv.gz","rt",encoding="utf-8") as handle:
        for row in csv.DictReader(handle,delimiter="\t"):
            a,b=norm(row["node_a"]),norm(row["node_b"])
            if a and b and a!=b: edges.append((a,b,float(row["edge_probability"])))
    for a,b,_ in edges:
        nodes.setdefault(a,{"symbol":a,"classes":"","node_type":"protein"})
        nodes.setdefault(b,{"symbol":b,"classes":"","node_type":"protein"})
    posterior_path=run/"node_posteriors.tsv.gz"
    if posterior_path.exists():
        with gzip.open(posterior_path,"rt",encoding="utf-8") as handle:
            for row in csv.DictReader(handle,delimiter="\t"):
                symbol=norm(row.get("gene_symbol",""))
                if symbol in nodes:
                    # These are retained with their source names.  The graph
                    # explorer treats gui_posterior as a soft preference, not
                    # as a calibrated participation probability.
                    nodes[symbol]["gui_posterior"]=row.get("gui_posterior","")
                    nodes[symbol]["gui_rank"]=row.get("gui_rank","")
                    nodes[symbol]["gui_selected_in_graph"]=row.get("gui_selected_in_graph","")
    return nodes,edges


def load_phospho(path: Path) -> tuple[list[dict[str,object]],dict[str,np.ndarray],dict[str,dict[str,object]],list[dict[str,object]]]:
    records: dict[tuple[str,str,str],np.ndarray]={}
    magnitudes: dict[tuple[str,str,str],list[float]]={}
    sample_rows=[]
    for sheet,minutes,index in TIME_SHEETS:
        frame=pd.read_excel(path,sheet_name=sheet)
        cc=[f"C{index}_Replicate{r}_1" for r in (1,2,3)]
        vv=[f"V{index}_Replicate{r}_1" for r in (1,2,3)]
        missing={GENE,UNIPROT,SITE,*cc,*vv}-set(frame.columns)
        if missing: raise ValueError(f"{sheet} missing columns: {sorted(missing)}")
        raw=frame[cc+vv].apply(pd.to_numeric,errors="coerce").to_numpy(float)
        with np.errstate(divide="ignore",invalid="ignore"):
            logged=np.where(raw>0,np.log2(raw),np.nan)
        channel_medians=np.nanmedian(logged,axis=0)
        normalized=logged-channel_medians
        for condition,code,start in (("control",0,0),("dDAVP",1,3)):
            for rep in range(3):
                channel=start+rep
                sample_rows.append({"sample_id":f"{condition}_T{minutes}_R{rep+1}","experiment_id":"Kirby_Leo_dDAVP",
                  "regime":"joint_control_dDAVP","condition":condition,"treatment_code":code,"time_minutes":minutes,
                  "log2_time_minutes":math.log2(minutes),"treatment_time_interaction":code*math.log2(minutes),
                  "biological_replicate":rep+1,"block_id":f"T{minutes}_R{rep+1}","paired_block":True,
                  "channel_log2_median":float(channel_medians[channel]),
                  "normalization":"positive intensity -> log2 -> within-channel median centering"})
        offset=(index-1)*6
        for i,row in frame.iterrows():
            gene=norm(row[GENE]); uniprot=str(row[UNIPROT]).strip(); site=str(row[SITE]).strip()
            if not gene or uniprot.casefold()=="nan" or not site: continue
            key=(gene,uniprot,site)
            arr=records.setdefault(key,np.full(24,np.nan)); arr[offset:offset+6]=normalized[i,:]
            magnitudes.setdefault(key,[]).extend(abs(v) for v in normalized[i,:] if math.isfinite(v))
    features=[]; by_gene: dict[str,list[tuple[tuple[str,str,str],np.ndarray]]]={}
    for key,values in records.items():
        gene,uniprot,site=key; feature_id=f"{uniprot}|{site}"; causal_node=phosphosite_node_id(gene,uniprot,site)
        features.append({"feature_id":feature_id,"graph_node":gene,"protein_gene":gene,"causal_node":causal_node,
          "node_type":"observed_phosphosite","uniprot":uniprot,"site":site,
          "assay":"TMT_phosphoproteomics","scale":"log2 positive intensity, median-centered within TMT channel",
          "n_complete":int(np.isfinite(values).sum())})
        by_gene.setdefault(gene,[]).append((key,values))
    matrix={}; proxy={}
    for gene,items in by_gene.items():
        chosen=max(items,key=lambda item:(int(np.isfinite(item[1]).sum()),np.nanmean(abs(item[1])) if np.isfinite(item[1]).any() else -1))
        key,values=chosen; fid=f"{key[1]}|{key[2]}"; causal_node=phosphosite_node_id(*key); matrix[causal_node]=values
        proxy[gene]={"feature_id":fid,"mapping":"exact" if len(items)==1 else "measurement_sensitive_representative_site",
          "causal_node":causal_node,"activity_node":activity_node_id(gene),"available_site_count":len(items),
          "selection_rule":"maximum completeness, then mean absolute response"}
    return features,matrix,proxy,sample_rows


def _optional_float(value: object) -> float:
    try:
        result=float(value)
    except (TypeError,ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def load_pooled_workbook(path: Path) -> tuple[
    list[dict[str,object]],dict[str,np.ndarray],dict[str,dict[str,object]],
    list[dict[str,object]],dict[str,object]
]:
    """Load the cleaned 43-observation multiregime workbook.

    The workbook's primary matrix deliberately contains one standardized
    protein-level readout per gene. For phosphoproteomic regimes this is the
    median of retained localized sites; for the four Camkk2 controls it is
    total-protein abundance. The assay layer remains explicit in the sample
    manifest so these columns are never described as exchangeable replicates.
    """
    if not path.exists():
        raise FileNotFoundError(path)
    manifest=pd.read_excel(path,sheet_name="Sample manifest")
    required_manifest={"sample_id","experiment","condition","measurement_layer","clone_id","treatment_code","time_minutes","paired_block"}
    missing_manifest=required_manifest-set(manifest.columns)
    if missing_manifest:
        raise ValueError(f"Sample manifest missing columns: {sorted(missing_manifest)}")
    sample_ids=[str(value).strip() for value in manifest["sample_id"]]
    if len(sample_ids)!=len(set(sample_ids)):
        raise ValueError("Sample manifest contains duplicate sample_id values")

    samples=[]
    for _,row in manifest.iterrows():
        sample_id=str(row["sample_id"]).strip()
        experiment=str(row.get("experiment","")).strip()
        layer=str(row.get("measurement_layer","")).strip()
        clone=str(row.get("clone_id",sample_id)).strip()
        paired=str(row.get("paired_block","")).strip()
        if paired.casefold() in {"nan","none"}: paired=""
        is_kirby="Kirby-Leo" in layer
        time_minutes=_optional_float(row.get("time_minutes")) if is_kirby else math.nan
        treatment_code=_optional_float(row.get("treatment_code")) if is_kirby else math.nan
        if paired:
            block_id=f"Kirby::{paired}"
        elif "PKA single-knockout" in experiment:
            block_id=f"PKA::{clone}"
        else:
            block_id=sample_id
        samples.append({
          "sample_id":sample_id,"experiment_id":experiment,"regime":layer,
          "condition":str(row.get("condition","")).strip(),
          "treatment_code":treatment_code,"time_minutes":time_minutes,
          "log2_time_minutes":math.log2(time_minutes) if math.isfinite(time_minutes) and time_minutes>0 else math.nan,
          "treatment_time_interaction":treatment_code*math.log2(time_minutes)
            if math.isfinite(treatment_code) and math.isfinite(time_minutes) and time_minutes>0 else math.nan,
          "biological_replicate":clone,"clone_id":clone,"block_id":block_id,
          "paired_block":bool(paired),"paired_block_id":paired,
          "measurement_layer":layer,"species":str(row.get("species","")).strip(),
          "cell_model":str(row.get("cell_model","")).strip(),
          "source_file":str(row.get("source_file","")).strip(),
          "source_sheet":str(row.get("source_sheet","")).strip(),
          "normalization":str(row.get("transform","")).strip(),
        })

    frame=pd.read_excel(path,sheet_name="Protein observations z")
    required_protein={"gene","species","experiments",*sample_ids}
    missing_protein=required_protein-set(frame.columns)
    if missing_protein:
        raise ValueError(f"Protein observations z missing columns: {sorted(missing_protein)}")
    features=[]; matrix={}; proxy={}
    for _,row in frame.iterrows():
        gene=norm(row.get("gene"))
        if not gene: continue
        values=pd.to_numeric(row[sample_ids],errors="coerce").to_numpy(float)
        if not np.isfinite(values).any(): continue
        node=protein_signal_node_id(gene)
        feature_id=f"PROTEIN_SIGNAL|{gene}"
        features.append({
          "feature_id":feature_id,"graph_node":gene,"protein_gene":gene,
          "causal_node":node,"node_type":"observed_protein_signal",
          "uniprot":"","site":"","species":str(row.get("species","")).strip(),
          "experiments":str(row.get("experiments","")).strip(),
          "site_count":_optional_float(row.get("site_count")),
          "assay":"multiregime_protein_readout",
          "scale":"within-acquisition-block z score",
          "measurement_semantics":"phosphosite-median protein signal except Camkk2 total-protein controls",
          "n_complete":int(np.isfinite(values).sum()),
        })
        matrix[node]=values
        proxy[gene]={
          "feature_id":feature_id,"mapping":"gene_level_multiregime_readout",
          "causal_node":node,"activity_node":activity_node_id(gene),
          "available_site_count":int(_optional_float(row.get("site_count"))) if math.isfinite(_optional_float(row.get("site_count"))) else 0,
          "selection_rule":"precomputed gene-level matrix: median localized phosphosites; Camkk2 total-protein abundance",
        }

    layer_counts={str(key):int(value) for key,value in manifest.groupby("measurement_layer").size().items()}
    normalization={
      "input":"cleaned multiregime workbook; Protein observations z",
      "transform":"source-aware log2/centering followed by within-acquisition-block feature z score",
      "sample_normalization":"performed upstream and documented in workbook Cleaning summary",
      "observation_count":len(samples),
      "control_observation_count":sum(str(row["condition"]).casefold()=="control" for row in samples),
      "ddavp_observation_count":sum(str(row["condition"]).casefold()=="ddavp" for row in samples),
      "independent_cluster_count":len({str(row["block_id"]) for row in samples}),
      "matched_kirby_block_count":len({str(row["block_id"]) for row in samples if row["paired_block"]}),
      "measurement_layer_counts":layer_counts,
      "missing_values":"retained as NaN; complete-case per query",
      "important_semantics":"columns are measurement-level observations, not exchangeable biological replicates",
      "source_workbook":str(path.resolve()),"source_sha256":sha256_file(path),
    }
    return features,matrix,proxy,samples,normalization


def _tier(node: dict[str,object], symbol: str) -> int:
    classes=set(str(node.get("classes","")).split(";"))
    for rank,label in enumerate(("ligand","receptor","gtpase","cyclase","kinase","phosphatase","transcription_factor")):
        if label in classes: return rank
    return 20


def make_candidates(
    nodes: dict[str,dict[str,object]],
    edges: list[tuple[str,str,float]],
    thresholds: list[float],
    sites_by_gene: dict[str,list[str]]|None=None,
    site_link_mode: str="activity_to_phosphosite",
    design_nodes: dict[str,dict[str,object]]|None=None,
    design_edges: list[tuple[str,str]]|None=None,
) -> list[CausalGraph]:
    if site_link_mode not in {"activity_to_phosphosite","activity_to_observed_readout"}:
        raise ValueError("Only acyclic activity-to-observed-readout measurement models are currently supported")
    names=set(nodes)
    tier_order={n:(_tier(nodes[n],n),n) for n in names}
    reverse_order={n:(-tier_order[n][0],n) for n in names}
    activities={gene:activity_node_id(gene) for gene in names}
    site_nodes={site for sites in (sites_by_gene or {}).values() for site in sites}
    causal_nodes=set(activities.values())|site_nodes|set(design_nodes or {})
    measurement_edges={(activities[gene],site) for gene,sites in (sites_by_gene or {}).items() if gene in activities for site in sites}
    experimental_edges=set(design_edges or [])
    latent=set(activities.values())
    result=[]
    for threshold in thresholds:
        selected=[(a,b) for a,b,p in edges if p>=threshold]
        protein_graph=orient_by_order(f"protein_tier_p{threshold:g}",names,selected,tier_order)
        activity_edges={(activities[a],activities[b]) for a,b in protein_graph.edges}
        g=CausalGraph(f"tier_oriented_p{threshold:g}",causal_nodes,activity_edges|measurement_edges|experimental_edges,latent)
        g.metadata={"edge_probability_threshold":threshold,"orientation":"biological class tier then stable symbol",
                    "edge_assertions":len(selected),"activity_edge_count":len(activity_edges),
                    "measurement_edge_count":len(measurement_edges),"experimental_design_edge_count":len(experimental_edges),"protein_registry_node_count":len(names),
                    "registry_edge_count":len(edges),"representation":"latent protein activity + observed molecular readout",
                    "site_link_mode":site_link_mode}
        result.append(g)
    selected=[(a,b) for a,b,p in edges if p>=max(thresholds)]
    protein_graph=orient_by_order("protein_reverse_tier",names,selected,reverse_order)
    activity_edges={(activities[a],activities[b]) for a,b in protein_graph.edges}
    g=CausalGraph(f"reverse_tier_sensitivity_p{max(thresholds):g}",causal_nodes,activity_edges|measurement_edges|experimental_edges,latent)
    g.metadata={"edge_probability_threshold":max(thresholds),"orientation":"reverse-tier sensitivity analysis",
                "edge_assertions":len(selected),"activity_edge_count":len(activity_edges),
                "measurement_edge_count":len(measurement_edges),"experimental_design_edge_count":len(experimental_edges),"protein_registry_node_count":len(names),
                "registry_edge_count":len(edges),"representation":"latent protein activity + observed molecular readout",
                "site_link_mode":site_link_mode}
    result.append(g)
    return result


def audit_ko_workbook(path: Path) -> dict[str,object]:
    if not path.exists(): return {"path":str(path),"exists":False,"raw_replicate_eligible":False}
    book=pd.ExcelFile(path); sheets=[]
    for name in book.sheet_names:
        frame=pd.read_excel(path,sheet_name=name,nrows=5)
        columns=[str(c) for c in frame.columns]
        replicate_like=[c for c in columns if "replicate" in c.casefold() or "intensity" in c.casefold()]
        sheets.append({"name":name,"columns":columns,"replicate_like_columns":replicate_like})
    return {"path":str(path.resolve()),"exists":True,"sha256":sha256_file(path),"sheets":sheets,
      "raw_replicate_eligible":False,"status":"summary contrast workbook; not treated as raw KO covariance data"}


def load_inputs(config: dict[str,object]) -> InputBundle:
    v1=Path(str(config["v1_root"])).resolve()
    run=Path(str(config.get("v1_network_run") or latest_v1_run(v1))).resolve()
    nodes,edges=load_registry(run)
    pooled_path=str(config.get("pooled_workbook","")).strip()
    if pooled_path:
        features,matrix,proxy,samples,normalization=load_pooled_workbook(Path(pooled_path).resolve())
        pooled_mode=True
    else:
        features,matrix,proxy,samples=load_phospho(Path(str(config["phospho_workbook"])).resolve())
        normalization={"input":"positive TMT phosphopeptide intensities","transform":"log2",
          "sample_normalization":"median centering within each TMT channel","observation_count":len(samples),
          "missing_values":"retained as NaN; complete-case per query"}
        pooled_mode=False
    activity_nodes={gene:activity_node_id(gene) for gene in nodes}
    treatment_target_gene=norm(config.get("treatment_target_gene","PRKACA"))
    if treatment_target_gene not in activity_nodes:
        raise ValueError(f"dDAVP treatment target {treatment_target_gene!r} is absent from the V1 protein registry")
    design_nodes={
      DDAVP_NODE:{"node_id":DDAVP_NODE,"label":"dDAVP treatment","short_label":"dDAVP","node_type":"observed_treatment","observed":True,"latent":False},
      TIME_NODE:{"node_id":TIME_NODE,"label":"Sampling time (log2 minutes)","short_label":"Time","node_type":"observed_design","observed":True,"latent":False},
      INTERACTION_NODE:{"node_id":INTERACTION_NODE,"label":"dDAVP × sampling time","short_label":"dDAVP × time","node_type":"derived_design","observed":True,"latent":False},
    }
    design_edges=[(DDAVP_NODE,INTERACTION_NODE),(TIME_NODE,INTERACTION_NODE),
      (DDAVP_NODE,activity_nodes[treatment_target_gene]),(INTERACTION_NODE,activity_nodes[treatment_target_gene])]
    sites_by_gene: dict[str,list[str]]={}
    node_metadata={activity:{"node_id":activity,"label":f"Activity: {gene}","short_label":f"A: {gene}",
      "node_type":"latent_activity","gene":gene,"observed":False,"latent":True}
      for gene,activity in activity_nodes.items()}
    node_metadata.update(design_nodes)
    for feature in features:
        gene=str(feature["protein_gene"]); site_node=str(feature["causal_node"])
        included=gene in nodes; feature["included_in_candidate_graph"]=included
        if not included: continue
        sites_by_gene.setdefault(gene,[]).append(site_node)
        node_type=str(feature.get("node_type","observed_phosphosite"))
        if node_type=="observed_protein_signal":
            label=f"Integrated protein readout: {gene}"
            short_label=f"R: {gene}"
        else:
            label=f"{gene} phospho-{feature['site']}"
            short_label=f"P: {gene} {feature['site']}"
        node_metadata[site_node]={"node_id":site_node,"label":label,
          "short_label":short_label,"node_type":node_type,"gene":gene,
          "uniprot":feature.get("uniprot",""),"site":feature.get("site",""),"observed":True,"latent":False,
          "activity_node_id":activity_nodes[gene],"feature_id":feature["feature_id"],
          "measurement_semantics":feature.get("measurement_semantics","")}
    representative_sites={gene:str(row["causal_node"]) for gene,row in proxy.items()
      if gene in nodes and str(row["causal_node"]) in node_metadata}
    matrix={site:values for site,values in matrix.items() if site in node_metadata}
    matrix[DDAVP_NODE]=np.asarray([float(row["treatment_code"]) for row in samples])
    matrix[TIME_NODE]=np.asarray([float(row["log2_time_minutes"]) for row in samples])
    matrix[INTERACTION_NODE]=np.asarray([float(row["treatment_time_interaction"]) for row in samples])
    site_link_mode=str(config.get("site_link_mode","activity_to_phosphosite"))
    block_count=len({str(row["block_id"]) for row in samples})
    normalization["independent_cluster_count"]=block_count
    if pooled_mode:
        regimes=[]
        for layer in sorted({str(row.get("measurement_layer","")) for row in samples}):
            layer_rows=[row for row in samples if str(row.get("measurement_layer",""))==layer]
            is_kirby="Kirby-Leo" in layer
            regimes.append({"regime_id":layer,"kind":"randomized_treatment" if is_kirby else "intact",
              "n_observations":len(layer_rows),
              "independent_clusters":len({str(row["block_id"]) for row in layer_rows}),
              "conditions":sorted({str(row.get("condition","")) for row in layer_rows}),
              "status":"replicate_level_available",
              "time_treatment":("control and dDAVP retained separately; treatment, log2(time), and their interaction are explicit observed graph variables"
                if is_kirby else "control-only measurement regime; no treatment code is assigned"),
              "biological_entry_constraint":(f"dDAVP enters the protein-activity network only at {treatment_target_gene}" if is_kirby else "not applicable")})
    else:
        regimes=[{"regime_id":"joint_control_dDAVP","kind":"randomized_treatment","n_observations":len(samples),
                  "independent_matched_blocks":block_count,"status":"replicate_level_available",
                  "time_treatment":"control and dDAVP retained separately; treatment, log2(time), and their interaction are explicit observed graph variables",
                  "biological_entry_constraint":f"dDAVP enters the protein-activity network only at {treatment_target_gene}"}]
    for ko in config.get("ko_workbooks",[]): regimes.append({"regime_id":Path(str(ko)).stem,"kind":"hard_KO_candidate",**audit_ko_workbook(Path(str(ko)))})
    candidates=make_candidates(nodes,edges,[float(x) for x in config.get("edge_thresholds",[.999,.9999])],sites_by_gene,site_link_mode,design_nodes,design_edges)
    return InputBundle(nodes,edges,features,matrix,proxy,samples,regimes,candidates,run,node_metadata,
      activity_nodes,representative_sites,sites_by_gene,site_link_mode,design_nodes,design_edges,block_count,normalization)


def write_registries(bundle: InputBundle, output: Path) -> None:
    output.mkdir(parents=True,exist_ok=True)
    with gzip.open(output/"full_node_edge_registry.tsv.gz","wt",newline="",encoding="utf-8") as f:
        cols=["record_type","id","node_a","node_b","edge_probability","classes","node_type","modeled_state","observed_feature_mapping","latent_status","intervention_eligibility","direction_status"]
        w=csv.DictWriter(f,fieldnames=cols,delimiter="\t"); w.writeheader()
        for symbol,node in sorted(bundle.nodes.items()):
            mapped=bundle.proxy.get(symbol)
            w.writerow({"record_type":"node","id":bundle.activity_nodes[symbol],"classes":node.get("classes",""),"node_type":"latent_activity",
              "modeled_state":"unobserved protein activity state","observed_feature_mapping":mapped.get("causal_node","") if mapped else "",
              "latent_status":"latent; observed molecular readout available" if mapped else "latent; no observed molecular readout",
              "intervention_eligibility":"eligible activity-level KO target; feasibility requires biological review"})
        for node_id,meta in sorted(bundle.node_metadata.items()):
            if meta.get("node_type") in {"observed_phosphosite","observed_protein_signal"}:
                observed_type=str(meta.get("node_type"))
                modeled=(f"site-specific phosphorylation ({meta.get('site','')})" if observed_type=="observed_phosphosite"
                  else "standardized gene-level protein readout across measurement regimes")
                w.writerow({"record_type":"node","id":node_id,"node_type":observed_type,
                  "modeled_state":modeled,
                  "observed_feature_mapping":meta.get("feature_id",""),"latent_status":"observed",
                  "intervention_eligibility":"not treated as a protein knockout target"})
            elif node_id in bundle.design_nodes:
                w.writerow({"record_type":"node","id":node_id,"node_type":meta.get("node_type","observed_design"),
                  "modeled_state":meta.get("label",node_id),"observed_feature_mapping":"experimental design matrix",
                  "latent_status":"observed or deterministically derived","intervention_eligibility":"dDAVP is randomized; time is a design variable"})
        for i,(a,b,p) in enumerate(bundle.edges,1): w.writerow({"record_type":"edge","id":f"E{i}",
          "node_a":bundle.activity_nodes[a],"node_b":bundle.activity_nodes[b],"edge_probability":p,
          "direction_status":"undirected_V1_activity-support; candidate-specific orientation"})
        offset=len(bundle.edges)
        for i,(gene,sites) in enumerate(sorted(bundle.sites_by_gene.items()),offset+1):
            for site in sites:
                w.writerow({"record_type":"edge","id":f"M{i}_{site}","node_a":bundle.activity_nodes[gene],"node_b":site,
                  "node_type":"measurement_link","direction_status":"activity_to_observed_readout measurement-model assumption"})
        for i,(source,target) in enumerate(bundle.design_edges,offset+len(bundle.sites_by_gene)+1):
            w.writerow({"record_type":"edge","id":f"D{i}","node_a":source,"node_b":target,
              "node_type":"experimental_design_link","direction_status":"preregistered joint-model design constraint"})
    pd.DataFrame(bundle.features).to_csv(output/"observed_feature_registry.tsv",sep="\t",index=False)
    pd.DataFrame([{"protein_gene":k,**v} for k,v in bundle.proxy.items()]).to_csv(output/"node_feature_proxy_map.tsv",sep="\t",index=False)
    readout_links=[{"activity_node":bundle.activity_nodes[gene],"observed_readout_node":site,
      "observed_node_type":(bundle.node_metadata.get(site) or {}).get("node_type",""),
      "relation":"activity_to_observed_readout","status":"measurement_model_assumption",
      "interpretation":("phosphorylation is modeled as a downstream readout of latent activity; this does not assert that the site activates the protein"
        if (bundle.node_metadata.get(site) or {}).get("node_type")=="observed_phosphosite" else
        "the standardized multiregime protein signal is modeled as a downstream readout of latent activity; assay layer is retained in the sample manifest")}
      for gene,sites in sorted(bundle.sites_by_gene.items()) for site in sites]
    pd.DataFrame(readout_links).to_csv(output/"activity_observed_readout_links.tsv",sep="\t",index=False)
    pd.DataFrame(readout_links).to_csv(output/"activity_phosphosite_links.tsv",sep="\t",index=False)
    pd.DataFrame(bundle.samples).to_csv(output/"samples.tsv",sep="\t",index=False)
    (output/"normalization_audit.json").write_text(json.dumps(bundle.normalization,indent=2),encoding="utf-8")
    (output/"candidate_graphs.json").write_text(json.dumps([{"graph_id":g.graph_id,"signature":g.signature,"node_count":len(g.nodes),
      "edge_count":len(g.edges),"latent_nodes":sorted(g.latent),"cycle":g.cycle(),"metadata":g.metadata,
      "edges":[{"source":a,"target":b} for a,b in sorted(g.edges)]} for g in bundle.candidates],indent=2),encoding="utf-8")
    (output/"regime_audit.json").write_text(json.dumps(bundle.regimes,indent=2,default=str),encoding="utf-8")

