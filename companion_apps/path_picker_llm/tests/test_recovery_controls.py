"""Offline recovery controls: no server startup, API requests, or paid work."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest

import server


class RecoveryControlTests(unittest.TestCase):
    def test_new_defaults_and_existing_configuration_are_distinct(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, defaults = server.normalize({"input_path": temporary})
            expected = {"max_output_tokens": 32000, "research_reasoning": "medium",
                        "edge_batch_size": 1, "node_batch_size": 2}
            for key, value in expected.items():
                self.assertEqual(defaults[key], value)
            self.assertEqual(defaults["screening_reasoning"], "medium")
            for key in ("verification_reasoning", "path_reasoning", "synthesis_reasoning"):
                self.assertEqual(defaults[key], "high")
            legacy = {"max_output_tokens": 8000, "research_reasoning": "high",
                      "edge_batch_size": 3, "node_batch_size": 6,
                      "api_base_url": "https://example.invalid/v1", "research_model": "custom-research"}
            _, loaded = server.normalize({"input_path": temporary, "configuration": legacy})
            for key, value in legacy.items():
                self.assertEqual(loaded[key], value)

    def test_reasoning_and_batch_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            for effort in ("low", "medium", "high"):
                _, config = server.normalize({"input_path": temporary, "configuration": {"research_reasoning": effort}})
                self.assertEqual(config["research_reasoning"], effort)
            for effort in ("none", "xhigh", "", None, True, ["medium"]):
                with self.subTest(effort=effort), self.assertRaisesRegex(ValueError, "research_reasoning"):
                    server.normalize({"input_path": temporary, "configuration": {"research_reasoning": effort}})
            for field in ("node_batch_size", "edge_batch_size"):
                for valid in (1, 50, "2"):
                    _, config = server.normalize({"input_path": temporary, "configuration": {field: valid}})
                    self.assertEqual(config[field], int(valid))
                for invalid in (0, 51, -1, 1.5, True, None, "1.5", float("inf")):
                    with self.subTest(field=field, value=invalid), self.assertRaisesRegex(ValueError, field):
                        server.normalize({"input_path": temporary, "configuration": {field: invalid}})

    def test_html_exposes_recovery_settings_and_cost_warning(self):
        html = (server.WEB / "index.html").read_text(encoding="utf-8")
        for identifier in ("safetySettings", "researchReasoning", "edgeBatchSize", "nodeBatchSize", "outputLimitRecovery", "recoveryNotice"):
            self.assertIn(f'id="{identifier}"', html)
        self.assertIn("Output-token ceiling per request · includes reasoning", html)
        self.assertIn("A higher ceiling may increase spend", html)
        self.assertIn("No paid calls start until launch/resume", html)
        self.assertIn("reuses completed checkpoints", html)
        self.assertIn('id="usageSummary"', html)

    @unittest.skipUnless(shutil.which("node"), "Node is required for offline browser logic checks")
    def test_browser_recovery_is_explicit_scoped_and_nonlaunching(self):
        script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const elements = new Map();
const html = fs.readFileSync(path.join(path.dirname(process.argv[2]),'index.html'),'utf8');
const numericIds = new Set([...html.matchAll(/<input id="([^"]+)" type="number"/g)].map(match=>match[1]));
function element() {
  let value = '';
  return {get value(){return value;},set value(next){value=String(next);},type:'text',textContent:'',innerHTML:'',hidden:false,disabled:false,options:[],children:[],
    appendChild(child){this.children.push(child);},querySelectorAll(){return this.children;}};
}
const document = {getElementById(id){if(!elements.has(id)){const el=element();if(numericIds.has(id)) el.type='number';elements.set(id,el);} return elements.get(id);},createElement:element};
const window = {location:{href:'http://localhost/?run=stopped'},history:{replaceState(a,b,address){window.location.href=String(address);}}};
let requests = 0;
const context = vm.createContext({document,window,URL,Intl,console,setInterval(){return 1;},clearInterval(){},
  fetch(){requests++; throw new Error('Recovery must not make requests');}});
const source = fs.readFileSync(process.argv[2],'utf8').split("load().catch(")[0];
vm.runInContext(source,context);
function run(code){return vm.runInContext(code,context);}
context.testDefaults = JSON.parse(process.argv[3]);
run(`defaults=testDefaults; compatible=true; active='stopped'; lastJob={run_id:'stopped',status:'needs_attention'};
  fill({...defaults,max_output_tokens:8000,research_reasoning:'high',edge_batch_size:3,node_batch_size:6,
    api_base_url:'https://custom.invalid/v1',screening_model:'custom-screen',research_model:'custom-research',
    verification_model:'custom-verify',reasoning_model:'custom-review',synthesis_model:'custom-write'});
  $('inputPath').value='chosen/input'; $('apiKey').value='memory-only-key'; $('savedRun').value='stopped';
  $('azureSearchRate').value='99'; $('maxTotalApiCalls').value='77';`);
assert.equal(elements.get('maxOutputTokens').value,8000);
assert.equal(elements.get('researchReasoning').value,'high');
assert.equal(elements.get('edgeBatchSize').value,3);
assert.equal(elements.get('nodeBatchSize').value,6);
const before = new Map([...elements].map(([id,el])=>[id,el.value]));
const pricingBefore = run('JSON.stringify(pricingBase)');
const rowsBefore = elements.get('pricingModels').children;
elements.get('outputLimitRecovery').onclick();
assert.equal(elements.get('maxOutputTokens').value,32000);
assert.equal(elements.get('researchReasoning').value,'medium');
assert.equal(elements.get('edgeBatchSize').value,1);
assert.equal(elements.get('nodeBatchSize').value,2);
const changed = [...before].filter(([id,value])=>elements.get(id).value!==value).map(([id])=>id).sort();
assert.deepEqual(changed,['edgeBatchSize','maxOutputTokens','nodeBatchSize','researchReasoning']);
assert.equal(run('active'),'stopped');
assert.equal(new URL(window.location.href).searchParams.get('run'),'stopped');
assert.equal(run('JSON.stringify(pricingBase)'),pricingBefore);
assert.strictEqual(elements.get('pricingModels').children,rowsBefore);
assert.equal(requests,0);
assert.match(elements.get('recoveryNotice').textContent,/may increase spend/);
assert.match(elements.get('recoveryNotice').textContent,/No paid calls/);
assert.match(elements.get('recoveryNotice').textContent,/completed checkpoints will be reused/);
run(`pricingConfiguration=()=>({sentinel:'unchanged'});`);
const payload = run('configuration()');
assert.equal(payload.max_output_tokens,32000);
assert.equal(payload.research_reasoning,'medium');
assert.equal(payload.edge_batch_size,1);
assert.equal(payload.node_batch_size,2);
for (const state of ['queued','running']) {
  run(`render({run_id:'stopped',status:'${state}',usage:null}); $('maxOutputTokens').value=7000; applyOutputLimitRecovery();`);
  assert.equal(elements.get('outputLimitRecovery').disabled,true);
  assert.equal(elements.get('maxOutputTokens').value,7000);
}
run(`render({run_id:'stopped',status:'needs_attention',usage:null});`);
assert.equal(elements.get('outputLimitRecovery').disabled,false);
run(`compatible=false; $('maxOutputTokens').value=6000; applyOutputLimitRecovery();`);
assert.equal(elements.get('maxOutputTokens').value,6000);
run(`compatible=true; lastJob=null; applyOutputLimitRecovery();`);
assert.equal(elements.get('maxOutputTokens').value,6000);
assert.equal(requests,0);
run(`api=async function(){return {run_id:'legacy',status:'needs_attention',configuration:{...defaults,
  max_output_tokens:8000,research_reasoning:'high',edge_batch_size:3,node_batch_size:6}};};`);
(async()=>{
  await run("selectRun('legacy')");
  assert.equal(elements.get('maxOutputTokens').value,8000);
  assert.equal(elements.get('researchReasoning').value,'high');
  assert.equal(elements.get('edgeBatchSize').value,3);
  assert.equal(elements.get('nodeBatchSize').value,6);
  assert.equal(elements.get('recoveryNotice').textContent,'');
  assert.equal(elements.get('apiKey').value,'memory-only-key');
  assert.match(elements.get('presetNotice').textContent,/without migrating/);
  await run("selectRun('')");
  assert.equal(elements.get('maxOutputTokens').value,32000);
  assert.equal(elements.get('researchReasoning').value,'medium');
  assert.equal(elements.get('edgeBatchSize').value,1);
  assert.equal(elements.get('nodeBatchSize').value,2);
  assert.equal(elements.get('outputLimitRecovery').disabled,true);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which("node"), "-", str(server.WEB / "app.js"), json.dumps(server.DEFAULT_CONFIG)],
                                input=script, text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
