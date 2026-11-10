"""Local-only live usage and pricing contracts. These tests never launch API work."""
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
from path_picker.workflow import PathPickerWorkflow


class LiveUsageTests(unittest.TestCase):
    def test_defaults_expose_explicit_rates_and_no_store(self):
        handler = server.Handler.__new__(server.Handler)
        handler.path = "/api/defaults"
        handler.wfile = io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_GET()
        result = json.loads(handler.wfile.getvalue())
        self.assertEqual(result["input_path"], "")
        self.assertEqual(set(result["configuration"]["pricing"]["models"]), {"gpt-6-luna", "gpt-6-sol"})
        self.assertEqual(result["configuration"]["pricing"]["search"]["azure_per_thousand"], 14)
        handler.send_header.assert_any_call("Cache-Control", "no-store")

    def test_manual_rates_validate_and_defaults_are_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, first = server.normalize({"input_path": temporary})
            first["pricing"]["models"]["gpt-6-luna"]["input_per_million"] = 99
            _, second = server.normalize({"input_path": temporary})
            self.assertNotEqual(second["pricing"]["models"]["gpt-6-luna"]["input_per_million"], 99)
            for bad in (-1, float("inf"), float("nan"), True, "1.2"):
                with self.subTest(value=bad), self.assertRaises(ValueError):
                    server.normalize({"input_path": temporary, "configuration": {"pricing": {"models": {"custom": {"input_per_million": bad}}}}})
            _, config = server.normalize({"input_path": temporary, "configuration": {"pricing": {"models": {"custom": {"input_per_million": None}}}}})
            self.assertIsNone(config["pricing"]["models"]["custom"]["input_per_million"])

    def test_public_job_rereads_usage_and_uses_saved_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            database = RunDatabase(directory / "path_picker.sqlite3")
            config = {"pricing": {"models": {"custom": {"input_per_million": 3}}}}
            database.set_meta("configuration", config)
            job = server.Job("test", directory, "inventory", "inventory_complete")
            with patch.object(server, "summarize_usage", side_effect=[{"attempts": 1}, {"attempts": 2}]) as summary:
                self.assertEqual(server.public_job(job)["usage"]["attempts"], 1)
                self.assertEqual(server.public_job(job)["usage"]["attempts"], 2)
            self.assertEqual(summary.call_count, 2)
            summary.assert_called_with(directory / "api_usage.jsonl", config)

    def test_pricing_is_persisted_without_starting_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            _, config = server.normalize({"input_path": temporary})
            config["pricing"]["models"]["my-deployment"] = {"input_per_million": 1.25, "output_per_million": None}
            workflow = PathPickerWorkflow(run_directory=directory, input_path=directory, configuration=config, api_key=None)
            saved = json.loads((directory / "configuration.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["pricing"], config["pricing"])
            self.assertEqual(workflow.database.get_meta("configuration", decode=True)["pricing"], config["pricing"])
            self.assertIsNone(workflow.client)
            self.assertFalse((directory / "api_usage.jsonl").exists())

    @unittest.skipUnless(shutil.which("node"), "Node is required for offline browser logic checks")
    def test_browser_rendering_defaults_and_saved_run_links(self):
        # Small deterministic DOM stand-in; no browser, HTTP requests, or external libraries.
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const elements = new Map();
function element() {
  return {value:'',type:'text',textContent:'',innerHTML:'',hidden:false,disabled:false,options:[],children:[],
    appendChild(child){this.children.push(child);},querySelectorAll(){return this.children;}};
}
const document = {getElementById(id){if(!elements.has(id)) elements.set(id,element()); return elements.get(id);},createElement:element};
let timers = 0;
const window = {location:{href:'http://localhost/?run=selected'},history:{replaceState(a,b,address){window.location.href=String(address);}}};
const context = vm.createContext({document,window,URL,Intl,setInterval(){timers++;return timers;},clearInterval(){},console});
const source = fs.readFileSync(process.argv[2],'utf8').split("load().catch(")[0];
vm.runInContext(source,context);
function run(code){return vm.runInContext(code,context);}
run(`active='selected'; renderUsage({attempts:2,response_count:1,failed_attempts:0,unfinished_attempts:1,tokens:{input:100,output:30,reasoning_included_in_output:20},search:{unknown_response_count:1},cost:{known_estimated_usd:0.1,token_estimated_usd:0.1,search_estimated_usd:null,complete:false},warnings:['<script>bad</script>'],models:[],billing_caveats:[]});`);
assert.match(elements.get('usageState').textContent,/Partial/);
assert.match(elements.get('usageSummary').innerHTML,/\$0\.1000/);
assert.match(elements.get('usageSummary').innerHTML,/Unknown/);
assert.match(elements.get('usageWarnings').innerHTML,/not zero/);
assert(!elements.get('usageWarnings').innerHTML.includes('<script>'));
assert.match(elements.get('usageDetails').innerHTML,/already in output/);
run(`renderUsage({attempts:0,response_count:0,cost:{complete:true},models:[],warnings:[]});`);
assert.equal(elements.get('usageState').textContent,'No paid calls recorded');
assert(!elements.get('usageSummary').innerHTML.includes('$0.0000'));
run(`compatible=true; lastJob=null; defaults={pricing:{models:{'gpt-6-luna':{input_per_million:0.1},'gpt-6-sol':{input_per_million:2}},search:{azure_per_thousand:14},long_context:{}},screening_model:'gpt-6-luna'}; $('inputPath').value='chosen/path'; $('apiKey').value='memory-key'; useRecommendedDefaults();`);
assert.equal(elements.get('inputPath').value,'chosen/path');
assert.equal(elements.get('apiKey').value,'memory-key');
assert.equal(elements.get('screeningModel').value,'gpt-6-luna');
assert.equal(elements.get('azureSearchRate').value,14);
elements.get('savedRun').options=[{value:'selected',textContent:'selected · running'}];
run(`render({run_id:'selected',status:'needs_attention',usage:null});`);
assert.equal(elements.get('savedRun').options[0].textContent,'selected · needs attention');
run(`render({run_id:'selected',status:'running',usage:null});`);
assert.equal(elements.get('pricingControls').disabled,true);
const row={querySelector(){return {value:'exact-alias'};},querySelectorAll(){return [{value:'',dataset:{rate:'input_per_million'}},{value:'2.5',dataset:{rate:'output_per_million'}}];}};
elements.get('pricingModels').children=[row];
elements.get('azureSearchRate').value='14'; elements.get('openaiSearchRate').value='';
for(const id of ['longContextThreshold','longInputMultiplier','longOutputMultiplier']) elements.get(id).value='';
const pricing=run('pricingConfiguration()');
assert.equal(pricing.models['exact-alias'].input_per_million,null);
assert.equal(pricing.models['exact-alias'].output_per_million,2.5);
assert.equal(pricing.search.openai_per_thousand,null);
run(`api=async function(url){if(url==='/api/defaults') return {configuration:defaults,input_path:''}; if(url==='/api/runs') return [{run_id:'selected',status:'inventory_complete'},{run_id:'running',status:'running'}]; return {run_id:url.split('/').pop(),status:'inventory_complete',configuration:defaults};};`);
(async()=>{
  await run('load()');
  assert.equal(run('active'),'selected');
  window.location.href='http://localhost/?run=invalid';
  await run('load()');
  assert.equal(run('active'),'running');
  assert.equal(new URL(window.location.href).searchParams.get('run'),'running');
  await run("selectRun('')");
  assert.equal(new URL(window.location.href).searchParams.get('run'),null);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which("node"), "-", str(server.WEB / "app.js")], input=script, text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
