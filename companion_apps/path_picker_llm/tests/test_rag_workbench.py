"""RAG server/UI contracts; all starts, requests, and libraries are local doubles."""
from __future__ import annotations

import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import server
from path_picker.database import RunDatabase


class RagWorkbenchTests(unittest.TestCase):
    def test_rag_defaults_and_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, config = server.normalize({"input_path": temporary})
            self.assertEqual(config["workflow_mode"], "rag_path_first")
            self.assertEqual(config["rag_model"], "gpt-6-sol")
            self.assertEqual(config["rag_reasoning"], "medium")
            self.assertEqual(config["rag_max_rounds"], 2)
            self.assertEqual(config["rag_claim_limit"], 40)
            self.assertEqual(config["rag_context_chars"], 24000)
            self.assertEqual(config["rag_max_new_claims"], 12)
            self.assertEqual(config["rag_max_investigations"], 8)
            self.assertEqual(config["rag_kb_path"], str(server.DEFAULT_KNOWLEDGE))
            for key, value in (("rag_max_rounds", 3), ("rag_max_rounds", True),
                               ("rag_max_new_claims", 1.5), ("rag_claim_limit", 0),
                               ("rag_context_chars", 999), ("rag_max_investigations", 101),
                               ("rag_reasoning", "invalid"), ("rag_model", ""),
                               ("rag_kb_path", "relative.sqlite3"), ("rag_seed_run", temporary)):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    server.normalize({"input_path": temporary, "configuration": {key: value}})
            with self.assertRaisesRegex(ValueError, "overwrite"):
                server.normalize({"input_path": temporary, "configuration": {
                    "rag_kb_path": str(Path(__file__).resolve()), "rag_seed_run": str(Path(__file__).resolve())}})

    def test_resume_preserves_legacy_mode_even_if_omitted(self):
        for mode in (None, "path_first", "evidence_first", "rag_path_first"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                directory = root / "old"
                database = RunDatabase(directory / "path_picker.sqlite3")
                database.set_meta("input_path", str(root))
                saved = {} if mode is None else {"workflow_mode": mode}
                database.set_meta("configuration", saved)
                with patch.object(server, "RUNS", root), patch.dict(server.JOBS, {}, clear=True), patch.object(server.threading, "Thread") as thread:
                    server.start({"mode": "production", "api_key": "mock-only", "configuration": {}}, resume_id="old")
                    launched_config = thread.call_args.kwargs["args"][2]
                    self.assertEqual(launched_config["workflow_mode"], mode or "path_first")
                    thread.return_value.start.assert_called_once()
                self.assertEqual(database.get_meta("configuration", decode=True), saved)

    def test_resume_rejects_mode_change_without_thread_or_config_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = RunDatabase(root / "old" / "path_picker.sqlite3")
            database.set_meta("input_path", str(root))
            database.set_meta("configuration", {"workflow_mode": "path_first"})
            with patch.object(server, "RUNS", root), patch.object(server.threading, "Thread") as thread:
                with self.assertRaisesRegex(ValueError, "fresh inventory"):
                    server.start({"mode": "production", "api_key": "mock-only", "configuration": {
                        "workflow_mode": "rag_path_first"}}, resume_id="old")
                thread.assert_not_called()
            self.assertEqual(database.get_meta("configuration", decode=True), {"workflow_mode": "path_first"})

    def test_knowledge_endpoint_inspects_only_default_path(self):
        handler = server.Handler.__new__(server.Handler)
        handler.path = "/api/knowledge?path=do-not-open.sqlite3"
        handler.wfile = io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        with patch.object(server, "knowledge_snapshot", return_value={"exists": False, "claims": 0}) as summary:
            handler.do_GET()
        summary.assert_called_once_with(server.DEFAULT_KNOWLEDGE)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"exists": False, "claims": 0})
        handler.send_header.assert_any_call("Cache-Control", "no-store")

    def test_public_job_has_rag_metadata_and_downloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database = RunDatabase(directory / "path_picker.sqlite3")
            database.set_meta("configuration", {"workflow_mode": "rag_path_first", "rag_kb_path": "example.sqlite3"})
            database.set_meta("rag_stage", "reviewing_paths")
            database.set_meta("rag_progress", {"reviewed": 4})
            database.set_meta("rag_seed_summary", {"accepted": 3})
            for name in ("rag_audit.json", "rag_reviews.jsonl"):
                (directory / name).write_text("{}\n", encoding="utf-8")
            with patch.object(server, "knowledge_snapshot", return_value={"exists": False}) as summary:
                result = server.public_job(server.Job("test", directory, "inventory", "inventory_complete"))
            self.assertEqual(result["rag"]["stage"], "reviewing_paths")
            self.assertEqual(result["rag"]["progress"], {"reviewed": 4})
            self.assertEqual(result["rag"]["seed_summary"], {"accepted": 3})
            summary.assert_called_once_with("example.sqlite3")
            self.assertIn("rag_audit.json", result["files"])
            self.assertIn("rag_reviews.jsonl", result["files"])

    @unittest.skipUnless(shutil.which("node"), "Node required for offline UI checks")
    def test_ui_preserves_modes_and_inspection_cannot_launch(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements=new Map(),requests=[];
function el(){let value='';return {get value(){return value;},set value(next){value=String(next);},type:'text',textContent:'',innerHTML:'',hidden:false,disabled:false,options:[],children:[],appendChild(c){this.children.push(c);},querySelectorAll(){return this.children;}};}
const document={getElementById(id){if(!elements.has(id))elements.set(id,el());return elements.get(id);},createElement:el};
const window={location:{href:'http://localhost/'},history:{replaceState(a,b,u){window.location.href=String(u);}}};
const context=vm.createContext({document,window,URL,Intl,console,setInterval(){return 1;},clearInterval(){}});
vm.runInContext(fs.readFileSync(process.argv[2],'utf8').split('load().catch(')[0],context);
const run=code=>vm.runInContext(code,context);
context.testDefaults=JSON.parse(process.argv[3]);
run(`defaults=testDefaults; compatible=true; fill(defaults); pricingConfiguration=()=>({});`);
assert.equal(elements.get('workflowMode').value,'rag_path_first');
assert.equal(elements.get('ragSettings').hidden,false);
context.mockApi=async(url)=>{requests.push(url);if(url==='/api/knowledge')return {exists:false};return {run_id:'legacy',status:'needs_attention',configuration:{workflow_mode:'evidence_first'}};};
run('api=mockApi;');
(async()=>{
  await run("selectRun('legacy')");
  assert.equal(elements.get('workflowMode').value,'evidence_first');
  assert.equal(elements.get('workflowMode').disabled,true);
  assert.equal(elements.get('ragSettings').hidden,true);
  run('useRecommendedDefaults();');
  assert.equal(elements.get('workflowMode').value,'evidence_first');
  assert.equal(run('configuration().workflow_mode'),'evidence_first');
  await run("selectRun('')");
  assert.equal(elements.get('workflowMode').value,'rag_path_first');
  assert.equal(elements.get('workflowMode').disabled,false);
  requests.length=0;
  await elements.get('inspectKnowledge').onclick();
  assert.deepEqual(requests,['/api/knowledge']);
  assert.match(elements.get('knowledgeSummary').textContent,/false/);
  run(`render({run_id:'rag',status:'running',configuration:{workflow_mode:'rag_path_first',rag_kb_path:'safe/library.sqlite3'},
    adaptive:{total_paths:20,directly_reviewed:4,uncertain:4,pending:16,excluded_by_rule:0,research_items_completed:0},
    rag:{stage:'reviewing_paths',knowledge:{accepted_claims:39,audit_records:12},
      progress:{completed_review_rounds:6,completed_scoped_investigations:7,pending:16,uncertain:4}}});`);
  assert.equal(elements.get('ragStatus').hidden,false);
  assert.match(elements.get('ragSummary').innerHTML,/>39<\/b><span>Accepted shared facts/);
  assert.match(elements.get('ragSummary').innerHTML,/>12<\/b><span>Rejected audit records/);
  assert.match(elements.get('counts').innerHTML,/>7<\/b><span>Scoped investigations/);
  assert.doesNotMatch(elements.get('counts').innerHTML,/Researched items/);
  assert.match(elements.get('ragStage').textContent,/16 paths pending; 4 reviewed paths remain uncertain/);
  assert.match(elements.get('ragLibrary').textContent,/safe\/library.sqlite3/);
  assert.match(elements.get('ragProgress').textContent,/completed_review_rounds/);
  run(`render({run_id:'legacy',status:'needs_attention',configuration:{workflow_mode:'path_first'}});`);
  assert.equal(elements.get('ragStatus').hidden,true);
})().catch(e=>{console.error(e);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which("node"), "-", str(server.WEB / "app.js"), json.dumps(server.DEFAULT_CONFIG)],
                                input=script, text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
