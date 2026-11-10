from __future__ import annotations
import argparse,subprocess,sys,time,webbrowser
from pathlib import Path

ROOT=Path(__file__).resolve().parent
def main():
    p=argparse.ArgumentParser(); p.add_argument('--host',default='127.0.0.1'); p.add_argument('--port',type=int,default=8771); p.add_argument('--no-browser',action='store_true'); p.add_argument('--foreground',action='store_true'); a=p.parse_args()
    cmd=[sys.executable,str(ROOT/'gui'/'server.py'),'--host',a.host,'--port',str(a.port)]; url=f'http://{a.host}:{a.port}/'
    if a.foreground:
        print(f'Static causal workbench: {url}',flush=True)
        if not a.no_browser: webbrowser.open(url)
        return subprocess.call(cmd,cwd=ROOT)
    proc=subprocess.Popen(cmd,cwd=ROOT,creationflags=getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)); time.sleep(.7)
    if proc.poll() is not None: return proc.returncode
    print(f'Static causal workbench: {url}',flush=True)
    if not a.no_browser: webbrowser.open(url)
    return 0
if __name__=='__main__': raise SystemExit(main())

