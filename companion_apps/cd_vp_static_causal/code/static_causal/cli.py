from __future__ import annotations
import argparse,json
from pathlib import Path
from .pipeline import run

def main():
    parser=argparse.ArgumentParser(description="Standalone static causal graph discrimination")
    parser.add_argument("--config",required=True); parser.add_argument("--output",required=True)
    args=parser.parse_args(); config=json.loads(Path(args.config).read_text(encoding="utf-8"))
    manifest=run(config,args.output,lambda text,pct:print(f"[{pct:3d}%] {text}",flush=True)); print(json.dumps(manifest,indent=2))

if __name__=="__main__": main()

