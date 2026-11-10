const gtState = { defaults: null, jobId: null, pollTimer: null };
const gt = (id) => document.getElementById(id);
const terminalStatuses = new Set(["complete", "failed", "cancelled"]);

function escapeHtml(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function showOnly(id) {
  ["gt-empty", "gt-job", "gt-complete", "gt-error"].forEach((name) => gt(name).classList.toggle("hidden", name !== id));
}

function collectConfiguration() {
  const defaults = structuredClone(gtState.defaults);
  defaults.api_base_url = gt("gt-endpoint").value.trim();
  defaults.deployments = { generator: gt("gt-generator").value.trim(), validator: gt("gt-validator").value.trim() };
  defaults.pair_search_calls_per_round = Number(gt("gt-pair-calls").value);
  defaults.min_pair_search_rounds = Number(gt("gt-min-pair-rounds").value);
  defaults.max_pair_search_rounds = Number(gt("gt-max-pair-rounds").value);
  defaults.pair_search_saturation_rounds = Number(gt("gt-saturation-rounds").value);
  defaults.pair_search_min_new_pairs = Number(gt("gt-min-new-pairs").value);
  defaults.max_pair_candidates = Number(gt("gt-max-pairs").value);
  defaults.minimum_candidates_per_profile_polarity = Number(gt("gt-min-profile-polarity").value);
  defaults.decoy_fraction = Number(gt("gt-decoy-fraction").value);
  defaults.seed = Number(gt("gt-seed").value);
  defaults.max_co_mention_decoy_queries = Number(gt("gt-comention").value);
  defaults.timeout_seconds = Number(gt("gt-timeout").value);
  defaults.generator_web_search = true;
  defaults.generator_web_search_required = true;
  defaults.generator_web_search_context_size = gt("gt-search-context").value;
  defaults.generator_concurrency = Number(gt("gt-generator-concurrency").value);
  defaults.validator_concurrency = Number(gt("gt-validator-concurrency").value);
  return defaults;
}

function updateCostPreview() {
  const perRound = Number(gt("gt-pair-calls").value || 0);
  const minRounds = Number(gt("gt-min-pair-rounds").value || 0);
  const maxRounds = Number(gt("gt-max-pair-rounds").value || 0);
  gt("gt-cost-preview").innerHTML = `
    <div><strong>${perRound.toLocaleString()}</strong><span>profile-specific search calls per round</span></div>
    <div><strong>${(perRound * minRounds).toLocaleString()}</strong><span>minimum pair-search calls</span></div>
    <div><strong>${(perRound * maxRounds).toLocaleString()}</strong><span>hard maximum pair-search calls</span></div>
    <div><strong>${Number(gt("gt-max-pairs").value || 0).toLocaleString()}</strong><span>maximum pair/profile hypotheses sent to validation</span></div>
    <div><strong>${Number(gt("gt-min-profile-polarity").value || 0).toLocaleString()}</strong><span>reserved candidates per profile and polarity</span></div>
    <div><strong>${Number(gt("gt-generator-concurrency").value || 0)}</strong><span>parallel Luna calls</span></div>
    <div><strong>${Number(gt("gt-validator-concurrency").value || 0)}</strong><span>parallel Sol validators</span></div>
    <div><strong>Required</strong><span>one or more hosted web searches per generator call</span></div>`;
}

function populate(defaults) {
  gtState.defaults = defaults;
  gt("gt-endpoint").value = defaults.api_base_url;
  gt("gt-generator").value = defaults.deployments.generator;
  gt("gt-validator").value = defaults.deployments.validator;
  gt("gt-pair-calls").value = defaults.pair_search_calls_per_round;
  gt("gt-min-pair-rounds").value = defaults.min_pair_search_rounds;
  gt("gt-max-pair-rounds").value = defaults.max_pair_search_rounds;
  gt("gt-saturation-rounds").value = defaults.pair_search_saturation_rounds;
  gt("gt-min-new-pairs").value = defaults.pair_search_min_new_pairs;
  gt("gt-max-pairs").value = defaults.max_pair_candidates;
  gt("gt-min-profile-polarity").value = defaults.minimum_candidates_per_profile_polarity || 0;
  gt("gt-decoy-fraction").value = defaults.decoy_fraction;
  gt("gt-seed").value = defaults.seed;
  gt("gt-comention").value = defaults.max_co_mention_decoy_queries;
  gt("gt-timeout").value = defaults.timeout_seconds;
  gt("gt-search-context").value = defaults.generator_web_search_context_size || "low";
  gt("gt-generator-concurrency").value = defaults.generator_concurrency || 4;
  gt("gt-validator-concurrency").value = defaults.validator_concurrency || 4;
  updateCostPreview();
}

function metric(value, label) {
  return `<div class="gt-metric"><strong>${escapeHtml(value ?? "—")}</strong><span>${escapeHtml(label)}</span></div>`;
}

function renderComplete(job) {
  showOnly("gt-complete");
  const result = job.result || {};
  gt("gt-complete-title").textContent = job.mode === "dry_run" ? "Call-volume preview ready" : "Ground-truth dataset ready";
  if (job.mode === "dry_run") {
    gt("gt-result-metrics").innerHTML = [
      metric(result.pair_search_calls_per_round, "pair-search calls per round"),
      metric(result.minimum_pair_generation_calls, "minimum pair-search calls"),
      metric(result.maximum_pair_generation_calls, "maximum pair-search calls"),
      metric(result.maximum_pair_candidates, "pair/profile validation cap"),
      metric(result.profile_count, "evidence profiles"),
      metric(result.generator_web_search ? "Required" : "Off", "Luna web search"),
      metric(result.generator_concurrency, "parallel Luna calls"),
      metric(result.validator_concurrency, "parallel Sol validators"),
      metric("0", "paid calls made"),
    ].join("");
    gt("gt-result-warning").textContent = result.warning || "No API calls were made.";
    gt("gt-result-warning").classList.remove("hidden");
  } else {
    const counts = result.counts || {};
    gt("gt-result-metrics").innerHTML = [
      metric(counts.node_candidates, "candidate nodes"), metric(counts.edge_profile_candidates, "pair/profile candidates"),
      metric(counts.h1_positive_references, "positive H1 references"), metric(counts.h0_explicit_negative_references, "explicit-negative H0 references"),
      metric(counts.conflicts, "positive/negative conflicts"), metric(counts.decoys, "decoys"),
      metric(counts.audit_rows, "audit rows"), metric((result.observed_model_versions || []).join(", ") || "—", "observed models"),
    ].join("");
    gt("gt-result-warning").classList.add("hidden");
  }
  gt("gt-files").innerHTML = (job.files || []).map((file) => `<a href="/api/ground-truth/jobs/${encodeURIComponent(job.job_id)}/files/${encodeURIComponent(file)}">${escapeHtml(file)}</a>`).join("");
}

function renderJob(job) {
  gtState.jobId = job.job_id;
  if (job.status === "complete") { stopPolling(); renderComplete(job); return; }
  if (job.status === "failed" || job.status === "cancelled") {
    stopPolling(); showOnly("gt-error");
    gt("gt-error-message").textContent = job.error || (job.status === "cancelled" ? "The run was cancelled. Existing checkpoints remain on disk and a future run can resume them when launched against the same directory." : "The run failed.");
    gt("gt-retry-area").classList.toggle("hidden", !job.resumable);
    gt("gt-retry-api-key").value = "";
    return;
  }
  showOnly("gt-job");
  const percent = Math.round(100 * Number(job.progress || 0));
  gt("gt-job-mode").textContent = job.mode === "dry_run" ? "Free preview" : "Paid production curation";
  gt("gt-job-title").textContent = job.status === "cancelling" ? "Stopping safely" : "Curating evidence";
  gt("gt-job-percent").textContent = `${percent}%`;
  gt("gt-progress-bar").style.width = `${percent}%`;
  gt("gt-job-message").textContent = job.message || "Working";
  gt("gt-job-id").textContent = job.job_id;
  gt("gt-cancel").disabled = job.status === "cancelling";
  if (!gtState.pollTimer) gtState.pollTimer = window.setInterval(pollJob, 1500);
}

async function pollJob() {
  if (!gtState.jobId) return;
  try {
    const response = await fetch(`/api/ground-truth/jobs/${encodeURIComponent(gtState.jobId)}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to read run status");
    renderJob(payload);
  } catch (error) {
    stopPolling(); showOnly("gt-error"); gt("gt-error-message").textContent = String(error.message || error);
  }
}

function stopPolling() { if (gtState.pollTimer) window.clearInterval(gtState.pollTimer); gtState.pollTimer = null; }

async function startJob(mode) {
  const form = gt("ground-truth-form");
  if (!form.reportValidity()) return;
  const key = gt("gt-api-key").value.trim();
  if (mode === "production" && !key) {
    gt("gt-api-key").setCustomValidity("Enter the Azure API key for this launch."); gt("gt-api-key").reportValidity(); gt("gt-api-key").setCustomValidity(""); return;
  }
  if (mode === "production" && !window.confirm("This v0.6 run starts from scratch. Luna will search separately for positive and explicit-negative PKA-responsive pairs in each evidence class. Sol will then validate each retained pair under that class's experimental rules. Launch it now?")) return;
  ["gt-dry-run", "gt-run"].forEach((id) => { gt(id).disabled = true; });
  try {
    const response = await fetch("/api/ground-truth/jobs", {
      method: "POST", headers: { "Content-Type": "application/json", ...(key ? { "X-Azure-OpenAI-API-Key": key } : {}) },
      body: JSON.stringify({ mode, configuration: collectConfiguration() }),
    });
    gt("gt-api-key").value = "";
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to start the run");
    renderJob(payload);
  } catch (error) {
    showOnly("gt-error"); gt("gt-error-message").textContent = String(error.message || error);
  } finally {
    gt("gt-api-key").value = "";
    ["gt-dry-run", "gt-run"].forEach((id) => { gt(id).disabled = false; });
  }
}

async function cancelJob() {
  if (!gtState.jobId) return;
  const response = await fetch(`/api/ground-truth/jobs/${encodeURIComponent(gtState.jobId)}/cancel`, { method: "POST" });
  const payload = await response.json();
  if (!response.ok) { showOnly("gt-error"); gt("gt-error-message").textContent = payload.error || "Unable to cancel"; return; }
  renderJob(payload);
}

async function retryJob() {
  if (!gtState.jobId) return;
  const input = gt("gt-retry-api-key");
  const key = input.value.trim();
  if (!key) {
    input.setCustomValidity("Enter the Azure API key to resume this saved run.");
    input.reportValidity();
    input.setCustomValidity("");
    return;
  }
  gt("gt-retry").disabled = true;
  try {
    const response = await fetch(`/api/ground-truth/jobs/${encodeURIComponent(gtState.jobId)}/retry`, {
      method: "POST",
      headers: { "X-Azure-OpenAI-API-Key": key },
    });
    const payload = await response.json();
    input.value = "";
    if (!response.ok) throw new Error(payload.error || "Unable to resume the saved run");
    renderJob(payload);
  } catch (error) {
    showOnly("gt-error");
    gt("gt-error-message").textContent = String(error.message || error);
  } finally {
    input.value = "";
    gt("gt-retry").disabled = false;
  }
}

async function initialize() {
  try {
    const response = await fetch("/api/ground-truth/config", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to load ground-truth configuration");
    populate(payload.defaults);
    if (payload.active_job) renderJob(payload.active_job);
  } catch (error) {
    showOnly("gt-error"); gt("gt-error-message").textContent = String(error.message || error);
  }
}

gt("ground-truth-form").addEventListener("submit", (event) => { event.preventDefault(); startJob("production"); });
gt("gt-dry-run").addEventListener("click", () => startJob("dry_run"));
gt("gt-cancel").addEventListener("click", cancelJob);
gt("gt-retry").addEventListener("click", retryJob);
gt("gt-new-run").addEventListener("click", () => { gtState.jobId = null; showOnly("gt-empty"); });
gt("gt-dismiss-error").addEventListener("click", () => showOnly("gt-empty"));
["gt-pair-calls", "gt-min-pair-rounds", "gt-max-pair-rounds", "gt-max-pairs", "gt-min-profile-polarity", "gt-generator-concurrency", "gt-validator-concurrency"].forEach((id) => gt(id).addEventListener("input", updateCostPreview));
initialize();
