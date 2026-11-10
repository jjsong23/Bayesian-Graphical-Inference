#!/usr/bin/env python3
"""Launch one bundled application, validate evidence, or relocate run checkpoints."""
from __future__ import annotations
import argparse
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APPS = {"bgi": "graphical_bayesian_inference", "path-picker": "path_picker_llm", "ground-truth": "cd_vp_ground_truth", "static-causal": "cd_vp_static_causal"}

def main() -> int:
    environment_python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if environment_python.is_file() and Path(sys.executable).resolve() != environment_python.resolve():
        return subprocess.call([str(environment_python), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", nargs="?", choices=APPS, default="path-picker")
    parser.add_argument("--check", action="store_true", help="Read-only local checks; does not contact an API")
    parser.add_argument("--relocate", action="store_true", help="Audit and remap copied Path Picker checkpoints to this extracted folder")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int)
    args = parser.parse_args()
    if args.relocate:
        sys.path.insert(0, str(ROOT / "path_picker_llm"))
        from path_picker.portable import remap_runs
        origin = json.loads((ROOT / "PORTABLE_ORIGIN.json").read_text(encoding="utf-8"))
        print(json.dumps(remap_runs(ROOT / "path_picker_llm", Path(origin["source_workspace"]), ROOT), indent=2))
    if args.check:
        missing = [name for name in ("numpy", "pandas", "scipy", "openpyxl", "PIL", "networkx", "obonet", "pyarrow") if importlib.util.find_spec(name) is None]
        reports = {"python": sys.version.split()[0], "root": str(ROOT), "missing_dependencies": missing, "apps": {name: (ROOT / directory / "launch.py").is_file() for name, directory in APPS.items()}}
        library = ROOT / "path_picker_llm/runtime/shared_knowledge/evidence.sqlite3"
        if library.is_file():
            with sqlite3.connect(library.as_uri() + "?mode=ro", uri=True) as db:
                reports["shared_library"] = {"quick_check": db.execute("PRAGMA quick_check").fetchone()[0], "claims": db.execute("SELECT COUNT(*) FROM knowledge_claims WHERE quarantined=0").fetchone()[0], "rejected_audit_rows": db.execute("SELECT COUNT(*) FROM knowledge_rejections").fetchone()[0]}
        bgi_root = ROOT / "graphical_bayesian_inference"
        registry_path = bgi_root / "gui/evidence_registry.json"
        if registry_path.is_file():
            registry = json.loads(registry_path.read_text(encoding="utf-8"))
            required = {"data/node_selection/node_universe_combined_nonzero.tsv", "results/combined_kinase_phosphoprotein_evidence/combined_all_nodes.tsv"}
            for group in ("node_streams", "edge_streams"):
                for item in registry.get(group, []):
                    if item.get("factor_file"):
                        required.add(item["factor_file"])
            reports["bgi_missing_inputs"] = sorted(path for path in required if not (bgi_root / path).is_file())
        else:
            reports["bgi_missing_inputs"] = ["gui/evidence_registry.json"]
        inputs = []
        for database in sorted((ROOT / "path_picker_llm/runs").glob("*/path_picker.sqlite3")):
            with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
                meta = dict(db.execute("SELECT key,value FROM metadata"))
                source = meta.get("input_path", "")
                inputs.append({"run": database.parent.name, "status": meta.get("status"), "input_available": bool(source) and Path(source).exists()})
        reports["path_picker_run_inputs"] = inputs
        static_seed = bgi_root / "results/gui_runs/20261103_163859_8fe27c63"
        reports["static_causal_seed_graph"] = all((static_seed / name).is_file() for name in ("supported_edges.tsv.gz", "full_graph_node_statistics.tsv.gz", "node_posteriors.tsv.gz"))
        reports["note"] = "All four saved inventories include their original inputs. Relocate before checking on a different computer."
        print(json.dumps(reports, indent=2))
        if missing or not all(reports["apps"].values()):
            return 2
        return 2 if reports["bgi_missing_inputs"] or not reports["static_causal_seed_graph"] or not all(item["input_available"] for item in inputs) else 0
    command = [sys.executable, str(ROOT / APPS[args.app] / "launch.py"), "--foreground"]
    if args.no_browser:
        command.append("--no-browser")
    if args.port:
        command += ["--port", str(args.port)]
    print("Local workbench only; no paid run starts until explicitly requested in the application.", flush=True)
    return subprocess.call(command, cwd=ROOT / APPS[args.app])

if __name__ == "__main__":
    raise SystemExit(main())
