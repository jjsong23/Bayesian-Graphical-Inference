from __future__ import annotations

import argparse
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from urllib.parse import urlparse

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"code"))
from static_causal.pipeline import run
from static_causal.data import latest_v1_run
from static_causal.identification import engine_status

STATE={"status":"idle","message":"Ready","progress":0,"run_id":None,"manifest":None,"error":None}
LOCK=threading.Lock()


def defaults():
    parent=ROOT.parent; v1=parent/"graphical_bayesian_inference"
    run_path=""
    try: run_path=str(latest_v1_run(v1))
    except Exception: pass
    return {"v1_root":str(v1),"v1_network_run":run_path,"phospho_workbook":str(v1/"data"/"phospho_data_original.xlsx"),
      "ko_workbooks":[str(v1/"data"/"pka_ko"/"PKA-KO_database_raw.xlsx"),str(v1/"data"/"node_selection"/"pka_subunit_ko"/"raw"/"Phosphopeptides-PKAsKO.xlsx")],
      "edge_thresholds":[0.999,0.9999],"site_link_mode":"activity_to_phosphosite","treatment_target_gene":"PRKACA","query_budget":80,"max_conditioning_size":3,"equivalence_margin":0.25,
      "complexity_penalty":0.02,"maximum_batch_size":3,"ko_query_budget":16,"do_query_budget":24,"maximum_feasible_kos":None,"random_seed":17,
      "identification_engine":engine_status()}


def restore_latest_completed_run():
    reports=list((ROOT/"runs").glob("*/report.html")) if (ROOT/"runs").exists() else []
    if not reports: return
    report=max(reports,key=lambda path:path.stat().st_mtime)
    manifest_path=report.parent/"run_manifest.json"; manifest=None
    if manifest_path.exists():
        try: manifest=json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception: manifest=None
    STATE.update(status="complete",message="Latest completed analysis",progress=100,run_id=report.parent.name,
      manifest=manifest,error=None,report_url=f"/runs/{report.parent.name}/report.html")


def execute(config):
    run_id=time.strftime("%Y%m%d_%H%M%S")
    output=ROOT/"runs"/run_id
    def update(message,progress):
        with LOCK: STATE.update(status="running",message=message,progress=progress)
    with LOCK: STATE.update(status="running",message="Starting",progress=1,run_id=run_id,manifest=None,error=None)
    try:
        manifest=run(config,output,update)
        with LOCK: STATE.update(status="complete",message="Analysis complete",progress=100,manifest=manifest,report_url=f"/runs/{run_id}/report.html")
    except Exception as exc:
        with LOCK: STATE.update(status="failed",message=str(exc),error=traceback.format_exc(),progress=100)


class Handler(SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self,format,*args): sys.stderr.write("[static-causal] "+format%args+"\n")
    def json(self,payload,status=200):
        data=json.dumps(payload).encode(); self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/api/defaults": return self.json(defaults())
        if path=="/api/engine": return self.json(engine_status())
        if path=="/api/status":
            with LOCK: return self.json(dict(STATE))
        if path=="/": self.path="/gui/web/index.html"
        return super().do_GET()
    def do_POST(self):
        if urlparse(self.path).path!="/api/run": return self.json({"error":"not found"},404)
        length=int(self.headers.get("Content-Length","0")); config=json.loads(self.rfile.read(length) or b"{}")
        with LOCK:
            if STATE["status"]=="running": return self.json({"error":"a run is already active"},409)
        threading.Thread(target=execute,args=(config,),daemon=True).start(); return self.json({"accepted":True},202)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--host",default="127.0.0.1"); parser.add_argument("--port",type=int,default=8771)
    args=parser.parse_args(); server=ThreadingHTTPServer((args.host,args.port),Handler)
    print(f"Static causal workbench: http://{args.host}:{args.port}/",flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass

restore_latest_completed_run()

if __name__=="__main__": main()

