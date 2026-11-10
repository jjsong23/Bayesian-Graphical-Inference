"""Offline browser regressions for output-limit warnings and explicit paid resume.

The Node VM never boots the server or reaches a network; API responses and the
confirmation dialog are in-memory doubles. No saved runs or API keys are read.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest

import server


HARNESS = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert'), path = require('path');
const elements = new Map(), requests = [], confirmations = [];
const html = fs.readFileSync(path.join(path.dirname(process.argv[2]),'index.html'),'utf8');
const numericIds = new Set([...html.matchAll(/<input id="([^"]+)" type="number"/g)].map(match=>match[1]));
function element() {
  let value = '';
  return {get value(){return value;},set value(next){value=String(next);},type:'text',textContent:'',innerHTML:'',hidden:false,disabled:false,options:[],children:[],
    appendChild(child){this.children.push(child);},querySelectorAll(){return this.children;}};
}
const document = {getElementById(id){if(!elements.has(id)){const el=element();if(numericIds.has(id)) el.type='number';elements.set(id,el);} return elements.get(id);},createElement:element};
const window = {location:{href:'http://localhost/?run=saved-output-limit'},history:{replaceState(a,b,address){window.location.href=String(address);}}};
let confirmAnswer = false, networkAttempts = 0;
const context = vm.createContext({document,window,URL,Intl,console,setInterval(){return 1;},clearInterval(){},
  confirm(message){confirmations.push(message);return confirmAnswer;},
  fetch(){networkAttempts++;throw new Error('Network access is forbidden in this test');}});
const source = fs.readFileSync(process.argv[2],'utf8').split("load().catch(")[0];
vm.runInContext(source,context);
function run(code){return vm.runInContext(code,context);}
function plain(value){return JSON.parse(JSON.stringify(value));}
context.testDefaults = JSON.parse(process.argv[3]);
context.savedJob = {
  run_id:'saved-output-limit',status:'needs_attention',input_path:'fixture-only/input',
  configuration:{...context.testDefaults,workflow_mode:'path_first',max_output_tokens:8000,research_reasoning:'high',edge_batch_size:3,node_batch_size:6,research_model:'gpt-6-luna'},
  message:'Response incomplete: reason=max_output_tokens; stage=path_picker_edge_research; model=gpt-6-luna',
  inventory:{paths:125,nodes:40,edges:60},
  progress:{events:[],stages:{path_picker_edge_research:{complete:7,failed:1}}},
  files:['path_picker.sqlite3'],usage:null
};
context.mockApi = async (url,options={}) => {
  requests.push({url,options:plain(options)});
  if(url==='/api/runs/saved-output-limit' && !options.method) return plain(context.savedJob);
  throw new Error(`Unexpected mocked API request: ${options.method || 'GET'} ${url}`);
};
run(`defaults=testDefaults; compatible=true; api=mockApi;
  pricingConfiguration=()=>({...pricingBase,search:{...pricingBase.search,azure_per_thousand:Number($('azureSearchRate').value)}});`);
async function loadSaved(){
  await run("selectRun('saved-output-limit')");
  assert.equal(run('active'),'saved-output-limit');
  assert.equal(elements.get('maxOutputTokens').value,'8000');
  requests.length=0;
}
function assertWarning(visible){
  assert.equal(elements.get('outputLimitWarning').hidden,!visible);
  assert.equal(Boolean(elements.get('outputLimitWarning').textContent),visible);
}
function assertSettings(expected){
  for(const [id,value] of Object.entries(expected)) assert.equal(elements.get(id).value,String(value),id);
}
'''


@unittest.skipUnless(shutil.which("node"), "Node is required for offline browser logic checks")
class RetryWarningTests(unittest.TestCase):
    def browser(self, checks: str) -> None:
        script = HARNESS + "\n(async()=>{\n" + checks + r'''
assert.equal(networkAttempts,0,'All browser requests must remain mocked');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(
            [shutil.which("node"), "-", str(server.WEB / "app.js"), json.dumps(server.DEFAULT_CONFIG)],
            input=script, text=True, encoding="utf-8", capture_output=True, timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_saved_failure_warns_and_recovery_changes_only_four_fields(self):
        self.browser(r'''
await loadSaved();
assertWarning(true);
assert.match(elements.get('outputLimitWarning').textContent,/8,000 tokens, high reasoning, 3 edge\(s\) \/ 6 node\(s\)/);
assert.match(elements.get('outputLimitWarning').textContent,/saved settings/);
assert.match(elements.get('outputLimitWarning').textContent,/saved only when you launch\/resume/);
run("$('apiKey').value='fixture-only-placeholder'; $('maxTotalApiCalls').value=77; $('azureSearchRate').value=99;");
const before = new Map([...elements].map(([id,el])=>[id,el.value]));
const savedBefore = JSON.stringify(context.savedJob);
const pricingBefore = run('JSON.stringify(pricingBase)');
elements.get('outputLimitRecovery').onclick();
assertSettings({maxOutputTokens:32000,researchReasoning:'medium',edgeBatchSize:1,nodeBatchSize:2});
assertWarning(false);
assert.deepEqual([...before].filter(([id,value])=>elements.get(id).value!==value).map(([id])=>id).sort(),
  ['edgeBatchSize','maxOutputTokens','nodeBatchSize','researchReasoning']);
assert.equal(run('JSON.stringify(pricingBase)'),pricingBefore);
assert.equal(JSON.stringify(context.savedJob),savedBefore);
assert.equal(run('active'),'saved-output-limit');
assert.equal(new URL(window.location.href).searchParams.get('run'),'saved-output-limit');
assert.equal(requests.length,0,'Applying a form preset must not call the API');
''')

    def test_unchanged_paid_confirmation_warns_and_cancel_sends_nothing(self):
        self.browser(r'''
await loadSaved();
await run("start('production')");
assert.equal(confirmations.length,1);
const message=confirmations[0];
assert.match(message,/WARNING: These are the same research settings that hit the output-token limit/);
assert.match(message,/may fail again and incur charges/);
assert.match(message,/Research: gpt-6-luna; 8,000 output-token ceiling \(including reasoning\); high reasoning; 3 edge\(s\) \/ 6 node\(s\) per batch/);
assert.match(message,/Completed checkpoints will be reused/);
assert.equal(requests.length,0,'Cancelled confirmation must prevent every API request');
assert.equal(run('active'),'saved-output-limit');
assertWarning(true);
''')

    def test_confirmed_recovery_submits_displayed_settings_to_same_resume_only(self):
        self.browser(r'''
await loadSaved();
const checkpointsBefore = JSON.stringify(context.savedJob.progress.stages);
elements.get('outputLimitRecovery').onclick();
confirmAnswer=true;
context.mockApi=async (url,options={})=>{
  requests.push({url,options:plain(options)});
  if(url==='/api/runs/saved-output-limit/resume' && options.method==='POST') {
    const body=JSON.parse(options.body);
    return {...plain(context.savedJob),status:'queued',configuration:body.configuration};
  }
  if(url==='/api/runs' && !options.method) return [{run_id:'saved-output-limit',status:'queued'}];
  throw new Error(`Unexpected mocked API request: ${options.method || 'GET'} ${url}`);
};
run('api=mockApi');
await run("start('production')");
assert.equal(confirmations.length,1);
assert.doesNotMatch(confirmations[0],/WARNING:|same research settings/);
assert.match(confirmations[0],/Research: gpt-6-luna; 32,000 output-token ceiling \(including reasoning\); medium reasoning; 1 edge\(s\) \/ 2 node\(s\) per batch/);
assert.match(confirmations[0],/Completed checkpoints will be reused/);
assert.deepEqual(requests.map(({url})=>url),['/api/runs/saved-output-limit/resume','/api/runs']);
const submitted=JSON.parse(requests[0].options.body);
assert.equal(submitted.mode,'production');
assert.equal(submitted.input_path,'fixture-only/input');
assert.equal(submitted.configuration.max_output_tokens,32000);
assert.equal(submitted.configuration.research_reasoning,'medium');
assert.equal(submitted.configuration.edge_batch_size,1);
assert.equal(submitted.configuration.node_batch_size,2);
assert.equal(submitted.configuration.research_model,'gpt-6-luna');
assert.deepEqual(Object.keys(submitted).sort(),['api_key','configuration','input_path','mode']);
assert.equal(Object.keys(submitted.configuration).some(key=>/reset|clear|restart/i.test(key)),false);
assert.equal(JSON.stringify(context.savedJob.progress.stages),checkpointsBefore);
assert.equal(run('JSON.stringify(lastJob.progress.stages)'),checkpointsBefore);
assert.equal(run('active'),'saved-output-limit');
assert.equal(elements.get('error').textContent,'');
assertWarning(false);
''')

    def test_polling_does_not_restore_saved_settings_over_form_edits(self):
        self.browser(r'''
await loadSaved();
elements.get('outputLimitRecovery').onclick();
run("$('maxOutputTokens').value=24000; $('researchModel').value='edited-deployment'; $('nodeBatchSize').value=4;");
const before=new Map([...elements].map(([id,el])=>[id,el.value]));
await run('refresh()');
assert.deepEqual(requests.map(({url})=>url),['/api/runs/saved-output-limit']);
assert.deepEqual([...before].filter(([id,value])=>elements.get(id).value!==value),[]);
assertSettings({maxOutputTokens:24000,researchReasoning:'medium',edgeBatchSize:1,nodeBatchSize:4,researchModel:'edited-deployment'});
assertWarning(false);
assert.equal(run('lastJob.configuration.max_output_tokens'),8000);
''')

    def test_irrelevant_edits_do_not_hide_warning_but_relevant_edits_do(self):
        self.browser(r'''
await loadSaved();
run("$('azureSearchRate').value=999; $('maxTotalApiCalls').value=123; $('nodeBatchSize').value=2; renderOutputLimitWarning();");
assertWarning(true);
assert.equal(run('unchangedOutputLimitFailure(configuration())'),true);
for(const [id,value] of [['maxOutputTokens',32000],['researchReasoning','medium'],['edgeBatchSize',1],['researchModel','changed-model']]) {
  const previous=elements.get(id).value;
  elements.get(id).value=value;
  elements.get(id).oninput();
  assertWarning(false);
  elements.get(id).value=previous;
  elements.get(id).onchange();
  assertWarning(true);
}
context.savedJob.message='Response incomplete: reason=max_output_tokens; stage=path_picker_node_research; model=gpt-6-luna';
run('fill(savedJob.configuration); render(savedJob);');
assertWarning(true);
run("$('edgeBatchSize').value=1; renderOutputLimitWarning();");
assertWarning(true);
run("$('nodeBatchSize').value=2; renderOutputLimitWarning();");
assertWarning(false);
''')

    def test_non_token_errors_and_non_research_stages_do_not_warn(self):
        self.browser(r'''
await loadSaved();
for(const message of [
  'Response incomplete: stage=path_picker_edge_research reason=content_filter',
  'Response incomplete: stage=path_picker_node_research reason=content_filter',
  'stage=path_picker_edge_research reason=timeout',
  'stage=path_picker_edge_research Request failed: HTTP 429',
  'Response incomplete: stage=path_picker_screening reason=max_output_tokens',
  'Response incomplete: stage=path_picker_verification reason=max_output_tokens',
  'Unrelated failure'
]) {
  context.savedJob.message=message;
  run('render(savedJob)');
  assertWarning(false);
  assert.equal(run('unchangedOutputLimitFailure(configuration())'),false,message);
  await run("start('production')");
  assert.doesNotMatch(confirmations.at(-1),/WARNING:|same research settings/);
}
assert.equal(requests.length,0);
// Each supported failure detail source is recognized without scanning old history.
for(const source of ['message','error','latest']) {
  context.savedJob.message='Needs attention';
  context.savedJob.error=null;
  context.savedJob.progress.events=[];
  const detail='Response incomplete: stage=path_picker_edge_research reason=max_output_tokens';
  if(source==='latest') context.savedJob.progress.events=[{stage:'error',message:detail}];
  else context.savedJob[source]=detail;
  run('render(savedJob)');
  assertWarning(true);
}
context.savedJob.progress.events=[{stage:'error',message:'reason=content_filter'},
  {stage:'error',message:'stage=path_picker_edge_research reason=max_output_tokens'}];
run('render(savedJob)');
assertWarning(false);
// A new in-memory failure must take precedence over the persisted old event.
context.savedJob.message='Response incomplete: stage=path_picker_edge_research reason=content_filter';
context.savedJob.progress.events=[{stage:'error',message:'stage=path_picker_edge_research reason=max_output_tokens'}];
for(const currentError of [null,'Traceback: Response incomplete: reason=content_filter']) {
  context.savedJob.error=currentError;
  run('render(savedJob)');
  assertWarning(false);
}
context.savedJob.error=null;
context.savedJob.message='Response incomplete: stage=path_picker_edge_research reason=max_output_tokens';
for(const status of ['queued','running','complete','cancelled','inventory_complete']) {
  context.savedJob.status=status;
  run('render(savedJob)');
  assertWarning(false);
}
''')


if __name__ == "__main__":
    unittest.main()
