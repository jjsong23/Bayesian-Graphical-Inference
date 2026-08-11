const state = { registry: null, defaults: null, jobId: null, pollTimer: null };

const $ = (id) => document.getElementById(id);
const formatInt = (value) => new Intl.NumberFormat("en-US").format(Number(value || 0));
const formatScore = (value) => Number(value).toPrecision(8);

function streamCard(stream, group, current) {
  const card = document.createElement("div");
  card.className = `stream-card${current.enabled ? " enabled" : ""}`;
  card.dataset.stream = stream.id;
  card.dataset.group = group;
  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = current.enabled;
  toggle.className = "stream-toggle";
  toggle.id = `${group}-${stream.id}`;
  toggle.setAttribute("aria-label", `Enable ${stream.label}`);
  const copy = document.createElement("label");
  copy.className = "stream-copy";
  copy.htmlFor = toggle.id;
  const dependence = stream.dependence_group
    ? `<small>Shared-source group: ${stream.dependence_group.replaceAll("_", " ")}</small>`
    : "";
  copy.innerHTML = `<strong>${stream.label}</strong><span>${stream.description}</span><small>${stream.normalization.reference}</small>${dependence}`;
  const weight = document.createElement("label");
  weight.className = "weight-field";
  weight.innerHTML = `<span>Weight</span><input class="stream-weight stream-setting" type="number" min="0" max="10" step="0.1" value="${current.weight}" ${current.enabled ? "" : "disabled"} aria-label="${stream.label} weight" />`;
  card.append(toggle, copy, weight);
  if (stream.normalization.user_control !== false) {
    const normalization = document.createElement("label");
    normalization.className = "normalization-field";
    normalization.title = stream.normalization.help;
    normalization.innerHTML = `<span>${stream.normalization.control_label}</span><input class="stream-tq stream-setting" type="number" min="0.05" max="20" step="0.05" value="${current.tq_multiplier}" ${current.enabled ? "" : "disabled"} aria-label="${stream.label} ${stream.normalization.control_label}" />`;
    card.appendChild(normalization);
  } else {
    card.classList.add("without-normalization");
  }
  (stream.parameters || []).forEach((parameter) => {
    card.classList.add("with-parameters");
    const field = document.createElement("label");
    field.className = "stream-parameter-field";
    field.title = parameter.help;
    field.innerHTML = `<span>${parameter.label}</span><input class="stream-parameter stream-setting" data-parameter="${parameter.id}" type="number" min="${parameter.minimum}" max="${parameter.maximum}" step="${parameter.step}" value="${current.parameters[parameter.id]}" ${current.enabled ? "" : "disabled"} aria-label="${stream.label} ${parameter.label}" />`;
    card.appendChild(field);
  });
  toggle.addEventListener("change", () => {
    if (toggle.checked && stream.exclusive_group) {
      document.querySelectorAll(`[data-group="${group}"]`).forEach((other) => {
        const otherDef = state.registry[`${group}_streams`].find((item) => item.id === other.dataset.stream);
        if (other !== card && otherDef?.exclusive_group === stream.exclusive_group) {
          other.querySelector(".stream-toggle").checked = false;
          other.querySelectorAll(".stream-setting").forEach((input) => { input.disabled = true; });
          other.classList.remove("enabled");
        }
      });
    }
    card.classList.toggle("enabled", toggle.checked);
    card.querySelectorAll(".stream-setting").forEach((input) => { input.disabled = !toggle.checked; });
  });
  return card;
}

function renderStreams(group) {
  const container = $(`${group}-streams`);
  container.innerHTML = "";
  state.registry[`${group}_streams`].forEach((stream) => {
    container.appendChild(streamCard(stream, group, state.defaults[`${group}_streams`][stream.id]));
  });
}

function renderPathOntologyClasses(path) {
  const container = $("path-ontology-classes");
  container.innerHTML = "";
  const selected = new Set(path.allowed_intermediate_classes || []);
  state.registry.path_ontology_classes.forEach((item) => {
    const option = document.createElement("label");
    option.className = "ontology-option";
    option.title = item.description;
    const toggle = document.createElement("input");
    toggle.type = "checkbox";
    toggle.className = "path-ontology-class";
    toggle.value = item.id;
    toggle.checked = selected.has(item.id);
    const copy = document.createElement("span");
    const provenance = item.roots.length ? item.roots.join(" · ") : "Curated class";
    copy.innerHTML = `<strong>${item.label}</strong><small>${provenance}</small>`;
    option.append(toggle, copy);
    container.appendChild(option);
  });
}

function populateControls() {
  renderStreams("node");
  renderStreams("edge");
  const node = state.defaults.node_integration;
  $("include-messengers").checked = node.include_second_messengers;
  $("node-prior").value = node.prior_probability;
  $("node-cutoff").value = node.output_probability_cutoff;
  const edge = state.defaults.edge_integration;
  $("edge-prior").value = edge.prior_probability;
  $("edge-cutoff").value = edge.output_probability_cutoff;
  const path = state.defaults.path;
  $("path-enabled").checked = path.enabled;
  $("path-start").value = path.start;
  $("path-target").value = path.target;
  $("path-top-k").value = path.top_k;
  $("path-max-hops").value = path.max_hops;
  $("path-cutoff").value = path.minimum_edge_probability;
  $("signal-only").checked = path.signaling_intermediates_only;
  $("exclude-multirole-scaffolds").checked = path.exclude_multirole_scaffolds;
  renderPathOntologyClasses(path);
  setPathControls(path.enabled);
}

function collectStreams(group) {
  const result = {};
  document.querySelectorAll(`[data-group="${group}"]`).forEach((card) => {
    const parameters = {};
    card.querySelectorAll(".stream-parameter").forEach((input) => {
      parameters[input.dataset.parameter] = Number(input.value);
    });
    result[card.dataset.stream] = {
      enabled: card.querySelector(".stream-toggle").checked,
      weight: Number(card.querySelector(".stream-weight").value),
      tq_multiplier: Number(card.querySelector(".stream-tq")?.value ?? 1),
      parameters,
    };
  });
  return result;
}

function collectConfiguration() {
  return {
    node_streams: collectStreams("node"),
    node_integration: {
      include_second_messengers: $("include-messengers").checked,
      prior_probability: Number($("node-prior").value),
      output_probability_cutoff: Number($("node-cutoff").value),
    },
    edge_streams: collectStreams("edge"),
    edge_integration: {
      prior_probability: Number($("edge-prior").value),
      output_probability_cutoff: Number($("edge-cutoff").value),
    },
    path: {
      enabled: $("path-enabled").checked,
      start: $("path-start").value.trim(),
      target: $("path-target").value.trim(),
      top_k: Number($("path-top-k").value),
      max_hops: Number($("path-max-hops").value),
      minimum_edge_probability: Number($("path-cutoff").value),
      signaling_intermediates_only: $("signal-only").checked,
      exclude_multirole_scaffolds: $("exclude-multirole-scaffolds").checked,
      allowed_intermediate_classes: Array.from(
        document.querySelectorAll(".path-ontology-class:checked")
      ).map((input) => input.value),
    },
  };
}

function setPathControls(enabled) {
  $("path-controls").querySelectorAll("input, button").forEach((control) => { control.disabled = !enabled; });
  $("path-controls").style.opacity = enabled ? "1" : ".45";
  setOntologyControls(enabled);
}

function setOntologyControls(pathEnabled) {
  const enabled = pathEnabled && $("signal-only").checked;
  $("ontology-selector").querySelectorAll("input, button").forEach((control) => {
    control.disabled = !enabled;
  });
  $("ontology-selector").classList.toggle("inactive", !enabled);
}

function showPanel(name) {
  ["empty-state", "job-state", "result-state", "error-state"].forEach((id) => $(id).classList.add("hidden"));
  $(name).classList.remove("hidden");
}

function updateJob(job) {
  $("job-message").textContent = job.message || "Working";
  const percent = Math.round((job.progress || 0) * 100);
  $("job-percent").textContent = `${percent}%`;
  $("progress-bar").style.width = `${percent}%`;
  const detail = {
    queued: "Your analysis is waiting for the local analysis worker.",
    cancelling: "Stopping at the next safe checkpoint. Completed cache writes will be preserved.",
    cancelled: "This analysis was cancelled. You can adjust the configuration and run again.",
  };
  $("job-detail").textContent = detail[job.status] || "Results are written to a new audited run folder as each stage completes.";
  const terminal = ["complete", "failed", "cancelled"].includes(job.status);
  $("cancel-job-button").disabled = terminal || job.status === "cancelling" || !state.jobId;
  $("cancel-job-button").textContent = job.status === "cancelling" ? "Cancelling…" : "Cancel analysis";
}

function renderResult(job) {
  const preview = job.preview || { metrics: {}, top_paths: [], files: [], warnings: [] };
  $("metric-nodes").textContent = formatInt(preview.metrics.selected_nodes);
  $("metric-edges").textContent = formatInt(preview.metrics.supported_edges);
  $("metric-paths").textContent = formatInt(preview.metrics.ranked_paths);
  const body = $("paths-body");
  body.innerHTML = "";
  if (!preview.top_paths.length) {
    body.innerHTML = `<tr><td colspan="4" class="muted">Path finding was disabled or no supported route was found.</td></tr>`;
  } else {
    preview.top_paths.forEach((path) => {
      const row = document.createElement("tr");
      row.innerHTML = `<td>${path.rank}</td><td class="route">${path.path_symbols}</td><td>${path.hop_count}</td><td class="score">${formatScore(path.path_probability_product)}</td>`;
      body.appendChild(row);
    });
  }
  const warnings = preview.warnings || [];
  $("warning-box").classList.toggle("hidden", !warnings.length);
  $("warning-box").textContent = warnings.join(" ");
  const downloads = $("download-links");
  downloads.innerHTML = "";
  (preview.files || []).forEach((file) => {
    const link = document.createElement("a");
    link.href = `/api/jobs/${job.job_id}/files/${encodeURIComponent(file)}`;
    link.textContent = file;
    link.setAttribute("download", file);
    downloads.appendChild(link);
  });
  showPanel("result-state");
}

async function pollJob() {
  try {
    const response = await fetch(`/api/jobs/${state.jobId}`, { cache: "no-store" });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "Unable to read job status");
    updateJob(job);
    if (job.status === "complete") {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      $("run-button").disabled = false;
      $("cancel-job-button").disabled = true;
      renderResult(job);
    } else if (job.status === "failed") {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      $("run-button").disabled = false;
      $("cancel-job-button").disabled = true;
      $("error-message").textContent = job.error || "The analysis could not be completed.";
      showPanel("error-state");
    } else if (job.status === "cancelled") {
      clearInterval(state.pollTimer);
      state.pollTimer = null;
      $("run-button").disabled = false;
      $("cancel-job-button").disabled = true;
      updateJob(job);
    }
  } catch (error) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
    $("run-button").disabled = false;
    $("error-message").textContent = error.message;
    showPanel("error-state");
  }
}

async function cancelRun() {
  if (!state.jobId) return;
  const button = $("cancel-job-button");
  button.disabled = true;
  button.textContent = "Cancelling…";
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/cancel`, { method: "POST" });
    const job = await response.json();
    if (!response.ok) throw new Error(job.error || "Unable to cancel analysis");
    updateJob(job);
  } catch (error) {
    button.disabled = false;
    button.textContent = "Cancel analysis";
    $("error-message").textContent = error.message;
    showPanel("error-state");
  }
}

async function startRun(event) {
  event.preventDefault();
  $("run-button").disabled = true;
  state.jobId = null;
  $("cancel-job-button").disabled = true;
  $("cancel-job-button").textContent = "Cancel analysis";
  showPanel("job-state");
  updateJob({ message: "Submitting configuration", progress: 0, status: "queued" });
  try {
    const response = await fetch("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ configuration: collectConfiguration() }),
    });
    const job = await response.json();
    if (response.status === 409 && job.active_job) {
      state.jobId = job.active_job.job_id;
      updateJob(job.active_job);
      state.pollTimer = setInterval(pollJob, 700);
      pollJob();
      return;
    }
    if (!response.ok) throw new Error(job.error || "Unable to start analysis");
    state.jobId = job.job_id;
    $("cancel-job-button").disabled = false;
    updateJob(job);
    state.pollTimer = setInterval(pollJob, 700);
    pollJob();
  } catch (error) {
    try {
      const recoveryResponse = await fetch("/api/jobs/active", { cache: "no-store" });
      const recovery = await recoveryResponse.json();
      if (recoveryResponse.ok && recovery.active_job) {
        state.jobId = recovery.active_job.job_id;
        updateJob(recovery.active_job);
        state.pollTimer = setInterval(pollJob, 700);
        pollJob();
        return;
      }
    } catch (_) {
      // Preserve the original submission error when recovery is unavailable.
    }
    $("run-button").disabled = false;
    $("error-message").textContent = error.message;
    showPanel("error-state");
  }
}

async function initialize() {
  try {
    const response = await fetch("/api/config", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to load configuration");
    state.registry = payload.registry;
    state.defaults = payload.defaults;
    $("catalog-nodes").textContent = formatInt(payload.project.seed_catalog_nodes);
    $("catalog-pairs").textContent = formatInt(payload.project.cached_pair_hypotheses);
    populateControls();
    if (payload.active_job) {
      state.jobId = payload.active_job.job_id;
      showPanel("job-state");
      updateJob(payload.active_job);
      state.pollTimer = setInterval(pollJob, 700);
      pollJob();
    }
  } catch (error) {
    $("error-message").textContent = error.message;
    showPanel("error-state");
  }
}

$("workflow-form").addEventListener("submit", startRun);
$("cancel-job-button").addEventListener("click", cancelRun);
$("reset-button").addEventListener("click", () => { populateControls(); showPanel("empty-state"); });
$("path-enabled").addEventListener("change", (event) => setPathControls(event.target.checked));
$("signal-only").addEventListener("change", () => setOntologyControls($("path-enabled").checked));
$("ontology-all").addEventListener("click", () => {
  document.querySelectorAll(".path-ontology-class").forEach((input) => { input.checked = true; });
});
$("ontology-none").addEventListener("click", () => {
  document.querySelectorAll(".path-ontology-class").forEach((input) => { input.checked = false; });
});
$("dismiss-error").addEventListener("click", () => showPanel("empty-state"));
initialize();
