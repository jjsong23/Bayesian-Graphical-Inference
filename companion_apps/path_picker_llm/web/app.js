const $ = id => document.getElementById(id);
const fields = {
  workflow_mode:'workflowMode', rag_model:'ragModel', rag_reasoning:'ragReasoning',
  rag_kb_path:'ragKbPath', rag_seed_run:'ragSeedRun', rag_max_rounds:'ragMaxRounds',
  rag_claim_limit:'ragClaimLimit', rag_context_chars:'ragContextChars',
  rag_max_new_claims:'ragMaxNewClaims', rag_max_investigations:'ragMaxInvestigations',
  api_base_url:'endpoint', screening_model:'screeningModel', research_model:'researchModel',
  verification_model:'verificationModel', reasoning_model:'reasoningModel', synthesis_model:'synthesisModel',
  max_deep_path_reviews:'maxReviews', shortlist_minimum:'minCards', shortlist_maximum:'maxCards',
  path_round_size:'pathRoundSize', max_research_items:'maxResearchItems', max_total_api_calls:'maxTotalApiCalls',
  max_output_tokens:'maxOutputTokens', max_tool_calls:'maxToolCalls',
  research_reasoning:'researchReasoning', edge_batch_size:'edgeBatchSize', node_batch_size:'nodeBatchSize',
  exclusion_max_paths:'exclusionMaxPaths', exclusion_max_fraction:'exclusionMaxFraction'
};
let active = null, poll = null, defaults = {}, lastJob = null, compatible = false;
let pricingBase = {};
const rateKeys = ['input_per_million','cached_input_per_million','cache_write_per_million','output_per_million'];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const number = value => Number(value || 0).toLocaleString();
async function api(url, options = {}) {
  const response = await fetch(url, options), data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}
function fill(config) {
  for (const [key,id] of Object.entries(fields)) $(id).value = config[key] ?? defaults[key] ?? '';
  fillPricing(config.pricing);
  renderWorkflowControls();
}
function renderWorkflowControls() {
  const rag = $('workflowMode').value === 'rag_path_first';
  $('ragSettings').hidden = !rag;
  $('recoveryControls').hidden = rag;
  $('workflowMode').disabled = Boolean(active) || !compatible;
  $('ragSettings').disabled = ['queued','running'].includes(lastJob?.status);
  $('workflowNotice').textContent = active
    ? 'This saved run keeps its original workflow. Select New inventory to create a separate RAG run; old checkpoints are preserved.'
    : 'RAG uses a fresh inventory and shared accepted evidence. Legacy modes remain available for new runs; existing runs are never migrated.';
}
function addPricingRow(model = '', rates = {}) {
  const row = document.createElement('tr');
  row.innerHTML = `<td><input class="pricing-name" aria-label="Exact deployment or model name" value="${esc(model)}" placeholder="Exact deployment name"></td>` + rateKeys.map(key => `<td><input data-rate="${key}" aria-label="${esc(key.replaceAll('_',' '))} for ${esc(model || 'new deployment')}" type="number" min="0" step="any" placeholder="Unknown" value="${esc(rates[key] ?? '')}"></td>`).join('');
  $('pricingModels').appendChild(row);
}
function fillPricing(pricing) {
  const base = defaults.pricing || {}, supplied = pricing || {};
  pricingBase = JSON.parse(JSON.stringify({...base, ...supplied, search:{...base.search,...supplied.search}, long_context:{...base.long_context,...supplied.long_context}}));
  const models = {...base.models,...supplied.models};
  $('pricingModels').textContent = '';
  for (const [model,rates] of Object.entries(models)) addPricingRow(model, {...base.models?.[model],...rates});
  const search = pricingBase.search, context = pricingBase.long_context;
  $('azureSearchRate').value = search.azure_per_thousand ?? '';
  $('openaiSearchRate').value = search.openai_per_thousand ?? '';
  $('longContextThreshold').value = context.threshold_input_tokens ?? '';
  $('longInputMultiplier').value = context.input_multiplier ?? '';
  $('longOutputMultiplier').value = context.output_multiplier ?? '';
}
function readRate(input) {
  if (input.value.trim() === '') return null;
  const value = Number(input.value);
  if (!Number.isFinite(value) || value < 0) throw new Error('Cost rates must be nonnegative finite numbers, or blank for unknown.');
  return value;
}
function pricingConfiguration() {
  const result = JSON.parse(JSON.stringify(pricingBase));
  result.models = {};
  for (const row of $('pricingModels').querySelectorAll('tr')) {
    const model = row.querySelector('.pricing-name').value.trim();
    if (!model) throw new Error('Each pricing row needs an exact deployment/model name.');
    if (Object.hasOwn(result.models,model)) throw new Error(`Duplicate pricing model: ${model}`);
    const rates = {...pricingBase.models?.[model]};
    for (const input of row.querySelectorAll('[data-rate]')) rates[input.dataset.rate] = readRate(input);
    Object.defineProperty(result.models,model,{value:rates,enumerable:true,writable:true,configurable:true});
  }
  result.search = {...result.search, azure_per_thousand:readRate($('azureSearchRate')), openai_per_thousand:readRate($('openaiSearchRate'))};
  // The form does not verify an account's billing agreement.
  result.search.azure_rate_verified = false; result.search.openai_rate_verified = false;
  result.long_context = {...result.long_context, threshold_input_tokens:readRate($('longContextThreshold')), input_multiplier:readRate($('longInputMultiplier')), output_multiplier:readRate($('longOutputMultiplier'))};
  return result;
}
function useRecommendedDefaults() {
  if (!compatible || ['queued','running'].includes(lastJob?.status)) return;
  fill({...defaults, workflow_mode:active ? (lastJob?.configuration?.workflow_mode || 'path_first') : defaults.workflow_mode});
  renderOutputLimitWarning();
  $('presetNotice').textContent = 'Recommended settings applied. New inventories use Sol RAG path-first; a selected saved run keeps its workflow mode. Your input, API key, and saved work are unchanged. A higher output ceiling may increase spend. No paid work has started.';
}
function applyOutputLimitRecovery() {
  if (!compatible || !lastJob || ['queued','running'].includes(lastJob.status)) return;
  $('maxOutputTokens').value = 32000;
  $('researchReasoning').value = 'medium';
  $('edgeBatchSize').value = 1;
  $('nodeBatchSize').value = 2;
  renderOutputLimitWarning();
  $('recoveryNotice').textContent = 'Recovery settings applied to this form only: 32,000 output-token ceiling including reasoning, medium research reasoning, 1 edge / 2 nodes per batch. This is a ceiling, not a target; a higher ceiling may increase spend. Endpoint, API key, selected run, deployments, pricing, and other settings are unchanged. No paid calls start until you choose Launch/resume paid curation; completed checkpoints will be reused.';
}
function researchSettings() {
  return {max_output_tokens:Number($('maxOutputTokens').value), research_reasoning:$('researchReasoning').value,
    edge_batch_size:Number($('edgeBatchSize').value), node_batch_size:Number($('nodeBatchSize').value),
    research_model:$('researchModel').value.trim()};
}
function unchangedOutputLimitFailure(config, job = lastJob) {
  if (job?.status !== 'needs_attention') return false;
  const latest = job.progress?.events?.[0];
  // After a server restart only the saved latest event may carry the failure.
  // Do not combine stale events with a newer, differently classified error.
  const detail = job.error ? `${job.message || ''} ${job.error}`
    : (job.message?.startsWith('Response incomplete:') ? job.message : (latest?.stage === 'error' ? latest.message || '' : ''));
  if (!detail.includes('reason=max_output_tokens') || !/stage=path_picker_(edge|node)_research/.test(detail)) return false;
  const batchKey = detail.includes('stage=path_picker_edge_research') ? 'edge_batch_size' : 'node_batch_size';
  return ['max_output_tokens','research_reasoning',batchKey,'research_model']
    .every(key => String(config[key]) === String(job.configuration?.[key]));
}
function renderOutputLimitWarning() {
  const settings = researchSettings(), unchanged = unchangedOutputLimitFailure(settings);
  $('outputLimitWarning').hidden = !unchanged;
  $('outputLimitWarning').textContent = unchanged
    ? `This run is still set to the research settings that hit the output limit: ${number(settings.max_output_tokens)} tokens, ${settings.research_reasoning} reasoning, ${settings.edge_batch_size} edge(s) / ${settings.node_batch_size} node(s) per batch. Click Apply output-limit recovery settings, then Launch/resume paid curation. Reloading or reselecting a run restores its saved settings; form edits are saved only when you launch/resume.`
    : '';
}
function configuration() {
  const result = {};
  for (const [key,id] of Object.entries(fields)) result[key] = $(id).type === 'number' ? Number($(id).value) : $(id).value.trim();
  result.pricing = pricingConfiguration();
  return result;
}
async function listRuns() {
  const runs = await api('/api/runs');
  $('savedRun').innerHTML = '<option value="">New inventory</option>' + runs.map(j => `<option value="${esc(j.run_id)}">${esc(j.run_id)} · ${esc(j.status.replaceAll('_',' '))}</option>`).join('');
  $('savedRun').value = active || '';
  return runs;
}
async function load() {
  const response = await api('/api/defaults'); defaults = response.configuration; fill(defaults);
  compatible = defaults.workflow_mode === 'rag_path_first';
  renderWorkflowControls();
  if (!compatible) {
    $('compatibilityNotice').hidden = false;
    $('compatibilityNotice').textContent = 'This running server predates RAG support. Monitoring and safe cancellation remain available. New launches require a server restart after its current job has stopped; the active job has not been interrupted.';
    $('run').disabled = true; $('inventory').disabled = true; $('recommendedDefaults').disabled = true; $('outputLimitRecovery').disabled = true;
  }
  $('inputPath').value = response.input_path;
  const runs = await listRuns(), running = runs.find(j => ['queued','running'].includes(j.status));
  const requested = new URL(window.location.href).searchParams.get('run');
  const selected = runs.find(job => job.run_id === requested) || running;
  if (selected) await selectRun(selected.run_id);
}
async function selectRun(id) {
  clearInterval(poll); active = id || null; $('savedRun').value = id || '';
  $('recoveryNotice').textContent = '';
  const address = new URL(window.location.href);
  if (active) address.searchParams.set('run',active); else address.searchParams.delete('run');
  window.history.replaceState(null,'',address);
  if (!active) {
    lastJob = null; fill(defaults); $('inputPath').value = ''; $('status').textContent = 'New inventory';
    for (const id of ['counts','stages','coverageNote','runMessage','files','events','exclusions','error','ragProgress','ragSummary','ragStage','ragLibrary','knowledgeSummary']) $(id).textContent = '';
    $('ragStatus').hidden = true;
    $('estimate').hidden = true; $('cancel').disabled = true; $('inventory').disabled = !compatible; $('run').disabled = !compatible; $('recommendedDefaults').disabled = !compatible; $('pricingControls').disabled = false;
    $('outputLimitRecovery').disabled = true;
    renderOutputLimitWarning();
    renderUsage(null);
    $('presetNotice').textContent = 'New inventory uses Sol RAG path-first, shared evidence, medium reasoning, and at most two path rounds. Creating the inventory imports only verified saved evidence into the local library, not old path assessments. No paid calls start until explicit launch/resume.';
    return;
  }
  const job = await api(`/api/runs/${encodeURIComponent(active)}`);
  $('inputPath').value = job.input_path || ''; fill({...job.configuration, workflow_mode:job.configuration?.workflow_mode || 'path_first'}); render(job); begin();
  $('presetNotice').textContent = 'Loaded this run\u2019s saved settings without migrating them. Its workflow mode is preserved. To use RAG for legacy input, select New inventory and create a separate run; the shared library can seed accepted claims, not old path assessments.';
}
async function start(mode) {
  $('error').textContent = '';
  try {
    if (!compatible) throw new Error('The existing server must finish/stop and restart before using the new RAG workflow.');
    const config = configuration();
    if (mode === 'production') {
      if (!active || !lastJob?.inventory?.paths) throw new Error('Create or select a completed inventory first, then check its input scope.');
      const warning = unchangedOutputLimitFailure(config)
        ? 'WARNING: These are the same research settings that hit the output-token limit. This may fail again and incur charges. Choose Cancel and apply output-limit recovery settings unless you intentionally want to retry unchanged.\n\n' : '';
      const work = config.workflow_mode === 'rag_path_first'
        ? `RAG: ${config.rag_model}; ${config.rag_reasoning} reasoning; at most ${config.rag_max_rounds} path rounds, ${config.rag_max_new_claims} new claims and ${config.rag_max_investigations} investigations per round. Shared accepted evidence is reused; path-level review remains new. Verification and finalization add calls.`
        : `Research: ${config.research_model}; ${number(config.max_output_tokens)} output-token ceiling (including reasoning); ${config.research_reasoning} reasoning; ${config.edge_batch_size} edge(s) / ${config.node_batch_size} node(s) per batch.`;
      if (!confirm(`${warning}Start paid curation of ${number(lastJob.inventory.paths)} imported paths?\n\n${work}\n\nCompleted checkpoints will be reused within this run. The API-attempt cap is ${number(config.max_total_api_calls)}, not a dollar cap.`)) return;
    }
    const url = mode === 'production' ? `/api/runs/${encodeURIComponent(active)}/resume` : '/api/start';
    const job = await api(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mode,input_path:$('inputPath').value,api_key:$('apiKey').value,configuration:config})});
    active = job.run_id;
    const address = new URL(window.location.href); address.searchParams.set('run',active); window.history.replaceState(null,'',address);
    await listRuns(); render(job); begin();
  } catch (error) { $('error').textContent = error.message; }
}
function begin() { clearInterval(poll); poll = setInterval(refresh, 2500); }
async function refresh() {
  if (!active) return;
  try {
    const job = await api(`/api/runs/${encodeURIComponent(active)}`); render(job);
    if (!['queued','running'].includes(job.status)) clearInterval(poll);
  } catch (error) { $('error').textContent = error.message; }
}
function render(job) {
  lastJob = job;
  renderWorkflowControls();
  const savedOption = Array.from($('savedRun').options).find(option => option.value === job.run_id);
  if (savedOption) savedOption.textContent = `${job.run_id} · ${job.status.replaceAll('_',' ')}`;
  const running = ['queued','running'].includes(job.status), progress = job.progress || {}, inventory = job.inventory || {}, p = job.adaptive || {};
  $('status').textContent = job.status.replaceAll('_',' '); $('runMessage').textContent = job.message || '';
  $('cancel').disabled = !running; $('inventory').disabled = running || !compatible; $('run').disabled = running || !compatible;
  $('recommendedDefaults').disabled = running || !compatible;
  $('outputLimitRecovery').disabled = running || !compatible;
  renderOutputLimitWarning();
  $('pricingControls').disabled = running;
  renderUsage(job.usage);
  renderRag(job);
  $('error').textContent = job.error ? job.message : '';
  const stats = p.total_paths ? {
    'Imported paths':p.total_paths, 'Directly reviewed':p.directly_reviewed, 'Uncertain':p.uncertain,
    'Rule-excluded':p.excluded_by_rule, 'Pending':p.pending,
    [job.configuration?.workflow_mode === 'rag_path_first' ? 'Scoped investigations' : 'Researched items']:
      job.configuration?.workflow_mode === 'rag_path_first' ? job.rag?.progress?.completed_scoped_investigations : p.research_items_completed
  } : (progress.counts || {});
  $('counts').innerHTML = Object.entries(stats).map(([key,value]) => `<div class="count"><b>${recorded(value)}</b><span>${esc(key)}</span></div>`).join('');
  $('coverageNote').textContent = 'These counters overlap: uncertain is a subset of directly reviewed, and a reviewed path may later be rule-excluded. Rule exclusion is not proof of direct review. Pending paths remain unresolved. ' + (job.report_stale ? 'The previous report is stale after an exclusion change; resume to regenerate it.' : '');
  $('stages').innerHTML = Object.entries(progress.stages || {}).map(([name,states]) => {
    const total = Object.values(states).reduce((a,b)=>a+Number(b),0), done = states.complete || 0;
    return `<div class="stage"><div class="stage-head"><b>${esc(name.replaceAll('_',' '))}</b><span>${number(done)} / ${number(total)} scheduled tasks</span></div><div class="bar"><i style="width:${total ? Math.min(100,100*done/total) : 0}%"></i></div></div>`;
  }).join('');
  $('estimate').hidden = !Object.keys(inventory).length;
  const scopeNodes = value => esc(Array.isArray(value) ? value.join(', ') : value || 'Not recorded');
  $('estimate').innerHTML = `<b>Imported scope:</b> ${number(inventory.nodes)} unique nodes · ${number(inventory.edges)} unique edges · ${number(inventory.paths)} paths. <br><b>Queries:</b> ${recorded(inventory.query_count)} · ${scopeNodes(inventory.source_nodes)} → ${scopeNodes(inventory.target_nodes)}.<br>All imported paths are accounted for. Per-stream BGI records are retained where supplied. Review <a href="/runs/${encodeURIComponent(job.run_id)}/inventory.json" target="_blank">inventory.json</a> for provenance and input limitations. Deep-review and research limits do not guarantee a complete review or fixed cost.`;
  $('events').innerHTML = (progress.events || []).map(event => `<li><b>${esc(event.stage)}</b> — ${esc(event.message)}</li>`).join('') || '<li>No events yet.</li>';
  const blocked = job.report_stale ? new Set(['report.html','shortlist.json','llm_handoff.json']) : new Set();
  $('files').innerHTML = (job.files || []).filter(name=>!blocked.has(name)).map(name => `<a href="/runs/${encodeURIComponent(job.run_id)}/${encodeURIComponent(name)}" target="_blank" rel="noreferrer">${esc(name)}</a>`).join('');
  $('exclusions').innerHTML = (job.exclusions || []).map(rule => {
    const id = rule.rule_id, enabled = rule.status === 'active';
    return `<article class="rule"><div class="row"><b>${esc(id)} · ${esc(rule.status)}</b>${enabled ? `<button class="secondary" data-revoke="${esc(id)}" ${running?'disabled':''}>Undo rule</button>` : ''}</div><p>${esc(rule.rationale)}</p><details><summary>Scope and supporting evidence</summary><pre>${esc(JSON.stringify(rule,null,2))}</pre></details></article>`;
  }).join('') || '<p class="muted">No exclusion rules recorded.</p>';
}
function renderRag(job) {
  const enabled = job.configuration?.workflow_mode === 'rag_path_first';
  $('ragStatus').hidden = !enabled;
  if (!enabled) return;
  const rag = job.rag || {}, progress = rag.progress || {}, knowledge = rag.knowledge || rag.knowledge_summary || {};
  const items = [
    [knowledge.accepted_claims, 'Accepted shared facts'],
    [knowledge.audit_records, 'Rejected audit records'],
    [progress.completed_review_rounds, 'Completed path rounds'],
    [progress.completed_scoped_investigations, 'Scoped investigations']
  ];
  $('ragSummary').innerHTML = items.map(([value,label]) => `<div class="usage-stat"><b>${recorded(value)}</b><span>${esc(label)}</span></div>`).join('');
  const stage = typeof rag.stage === 'string' ? rag.stage.replaceAll('_',' ') : 'Awaiting local inventory';
  $('ragStage').textContent = `Stage: ${stage}. ${recorded(progress.pending)} paths pending; ${recorded(progress.uncertain)} reviewed paths remain uncertain.`;
  $('ragLibrary').textContent = `Library: ${knowledge.path || job.configuration?.rag_kb_path || 'Not recorded'}` +
    (knowledge.error || knowledge.warning ? ` · ${knowledge.error || knowledge.warning}` : '');
  $('ragProgress').textContent = JSON.stringify(rag, null, 2);
}
function money(value) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return 'Unknown';
  return new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',minimumFractionDigits:4,maximumFractionDigits:4}).format(value);
}
function recorded(value) {
  return value === null || value === undefined ? 'Unknown' : number(value);
}
function componentMoney(value, complete) {
  if (complete === false) return Number(value) > 0 ? `${money(value)} + unknown` : 'Unknown';
  return money(value);
}
function renderUsage(usage) {
  if (!usage) {
    $('usageState').textContent = active ? 'Usage unavailable' : 'No run selected';
    $('usageSummary').innerHTML = '<p class="muted">' + (active ? 'This server has not supplied usage totals. Missing usage is not zero cost.' : 'Select an inventory or run to see recorded usage.') + '</p>';
    $('usageWarnings').hidden = true; $('usageWarnings').textContent = ''; $('usageDetails').textContent = ''; $('billingCaveats').textContent = '';
    return;
  }
  const cost = usage.cost || {}, tokens = usage.tokens || {}, search = usage.search || {};
  const hasAttempts = (usage.attempts || 0) > 0 || (usage.response_count || 0) > 0 || (usage.malformed_lines || 0) > 0 || cost.complete === false;
  const hasResponses = (usage.response_count || 0) > 0;
  $('usageState').textContent = !hasAttempts ? 'No paid calls recorded' : cost.complete ? 'Recorded estimate' : 'Partial · unknown charges';
  const label = cost.complete ? 'Recorded estimated USD' : 'Known estimated subtotal · USD';
  const items = [
    [hasResponses ? componentMoney(cost.known_estimated_usd,cost.complete) : hasAttempts ? 'Unknown' : '—',label],
    [hasResponses ? componentMoney(cost.token_estimated_usd,cost.token_complete ?? cost.complete) : hasAttempts ? 'Unknown' : '—','Recorded token estimate'],
    [hasResponses ? componentMoney(cost.search_estimated_usd,cost.search_complete ?? cost.complete) : hasAttempts ? 'Unknown' : '—','Reported search estimate'],
    [number(usage.attempts),'Recorded API attempts']
  ];
  $('usageSummary').innerHTML = items.map(([value,title])=>`<div class="usage-stat"><b>${esc(value)}</b><span>${esc(title)}</span></div>`).join('');
  const warnings = [...(usage.warnings || [])];
  if (hasAttempts && !cost.complete) warnings.unshift('Only known components are added above. Missing usage, unfinished requests, or unpriced deployments may add charges; they are not zero.');
  $('usageWarnings').hidden = !warnings.length;
  $('usageWarnings').innerHTML = warnings.map(warning=>`<li>${esc(warning)}</li>`).join('');
  const counters = {
    'Responses':usage.response_count, 'Failed attempts':usage.failed_attempts, 'Unfinished attempts':usage.unfinished_attempts,
    'Reported input tokens':tokens.input, 'Cached input tokens':tokens.cached_input, 'Cache-write tokens':tokens.cache_write,
    'Uncached input tokens':tokens.uncached_input, 'Reported output tokens':tokens.output,
    'Reasoning tokens (already in output)':tokens.reasoning_included_in_output,
    'Reported search requests':search.reported_requests, 'Observed search actions (not billing units)':search.observed_actions,
    'Responses with unknown search usage':search.unknown_response_count
  };
  $('usageDetails').innerHTML = `<dl class="usage-counters">${Object.entries(counters).map(([title,value])=>`<div><dt>${esc(title)}</dt><dd>${recorded(value)}</dd></div>`).join('')}</dl>
    <p class="muted small">Token counts are the reported portion only. Reasoning is already included in output tokens and is never charged twice here. Search content tokens, where reported as model input, are included in input cost.</p>
    <div class="table-scroll"><table class="usage-models"><thead><tr><th scope="col">Recorded model/deployment</th><th scope="col">Responses</th><th scope="col">Token USD</th><th scope="col">Search USD</th><th scope="col">Known subtotal USD</th><th scope="col">Coverage</th></tr></thead><tbody>${(usage.models || []).map(model=>`<tr><th scope="row">${esc(model.model)}<br><small class="muted">Rate: ${esc(model.pricing_model || model.model)} · ${esc(model.service_tier || 'default')}</small></th><td>${number(model.response_count)}</td><td>${componentMoney(model.cost?.token_estimated_usd,model.cost?.token_complete ?? model.cost?.complete)}</td><td>${componentMoney(model.cost?.search_estimated_usd,model.cost?.search_complete ?? model.cost?.complete)}</td><td>${componentMoney(model.cost?.known_estimated_usd,model.cost?.complete)}</td><td>${model.cost?.complete ? 'Recorded estimate' : 'Partial / unknown'}</td></tr>`).join('') || '<tr><td colspan="6">No model responses recorded.</td></tr>'}</tbody></table></div>
    <p class="muted small">Last logged event: ${esc(usage.last_event_at || 'None')}. Duplicate responses ignored: ${number(usage.duplicate_responses)}. Unreadable log lines: ${number(usage.malformed_lines)}.</p>`;
  $('billingCaveats').innerHTML = (usage.billing_caveats || []).map(caveat=>`<li>${esc(caveat)}</li>`).join('');
}
$('savedRun').onchange = () => selectRun($('savedRun').value).catch(error=>$('error').textContent=error.message);
$('addPricingModel').onclick = () => addPricingRow();
$('recommendedDefaults').onclick = useRecommendedDefaults;
$('workflowMode').onchange = renderWorkflowControls;
$('inspectKnowledge').onclick = async () => {
  try { $('knowledgeSummary').textContent = JSON.stringify(await api('/api/knowledge'), null, 2); }
  catch (error) { $('knowledgeSummary').textContent = error.message; }
};
$('outputLimitRecovery').onclick = applyOutputLimitRecovery;
for (const id of ['maxOutputTokens','researchReasoning','edgeBatchSize','nodeBatchSize','researchModel']) {
  $(id).oninput = renderOutputLimitWarning;
  $(id).onchange = renderOutputLimitWarning;
}
$('inventory').onclick = () => start('inventory'); $('run').onclick = () => start('production');
$('cancel').onclick = async () => {
  try { if (active) { await api(`/api/runs/${encodeURIComponent(active)}/cancel`,{method:'POST'}); await refresh(); } }
  catch (error) { $('error').textContent = error.message; }
};
$('exclusions').onclick = async event => {
  const button = event.target.closest('[data-revoke]');
  if (!button || !active || !confirm('Restore affected paths? A new review/report may require additional paid calls.')) return;
  try { render(await api(`/api/runs/${encodeURIComponent(active)}/exclusions/${encodeURIComponent(button.dataset.revoke)}/revoke`,{method:'POST'})); }
  catch (error) { $('error').textContent = error.message; }
};
load().catch(error=>$('error').textContent=error.message);

