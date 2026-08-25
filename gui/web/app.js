const state = { registry: null, defaults: null, jobId: null, pollTimer: null };

const $ = (id) => document.getElementById(id);
const formatInt = (value) => new Intl.NumberFormat("en-US").format(Number(value || 0));
const formatScore = (value) => Number(value).toPrecision(8);
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

function svgElement(name, attributes = {}, text = "") {
  const element = document.createElementNS(SVG_NAMESPACE, name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, value));
  if (text) element.textContent = text;
  return element;
}

function formatProbability(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return number.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}

function renderProbabilityDistribution(kind, distribution) {
  const svg = $(`${kind}-distribution-chart`);
  const stats = $(`${kind}-distribution-stats`);
  svg.innerHTML = "";
  stats.innerHTML = "";
  $(`${kind}-distribution-count`).textContent = distribution
    ? `${formatInt(distribution.hypothesis_count)} hypotheses`
    : "No data";
  if (!distribution || !distribution.bin_counts?.length) {
    svg.appendChild(svgElement("text", { x: 220, y: 109, class: "chart-empty", "text-anchor": "middle" }, "Distribution unavailable"));
    return;
  }

  const width = 440;
  const height = 218;
  const margin = { top: 15, right: 15, bottom: 36, left: 42 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const counts = distribution.bin_counts.map(Number);
  const transformed = counts.map((count) => Math.log10(count + 1));
  const maximum = Math.max(...transformed, 1);
  const binWidth = plotWidth / counts.length;

  [0, 0.5, 1].forEach((fraction) => {
    const y = margin.top + plotHeight * (1 - fraction);
    svg.appendChild(svgElement("line", { x1: margin.left, y1: y, x2: width - margin.right, y2: y, class: "chart-grid" }));
  });

  counts.forEach((count, index) => {
    const barHeight = (transformed[index] / maximum) * plotHeight;
    const lower = distribution.bin_edges[index];
    const upper = distribution.bin_edges[index + 1];
    const bar = svgElement("rect", {
      x: margin.left + index * binWidth + 0.45,
      y: margin.top + plotHeight - barHeight,
      width: Math.max(binWidth - 0.9, 0.6),
      height: Math.max(barHeight, count ? 1 : 0),
      class: "distribution-bar",
      rx: 0.7,
    });
    const interval = index === counts.length - 1 ? "]" : ")";
    bar.appendChild(svgElement("title", {}, `[${lower.toFixed(2)}, ${upper.toFixed(2)}${interval}: ${formatInt(count)}`));
    svg.appendChild(bar);
  });

  const cutoffX = margin.left + Number(distribution.output_probability_cutoff_exclusive) * plotWidth;
  svg.appendChild(svgElement("line", { x1: cutoffX, y1: margin.top, x2: cutoffX, y2: margin.top + plotHeight, class: "chart-cutoff" }));
  svg.appendChild(svgElement("text", { x: Math.min(cutoffX + 4, width - 58), y: margin.top + 11, class: "chart-cutoff-label" }, "cutoff"));
  svg.appendChild(svgElement("line", { x1: margin.left, y1: margin.top + plotHeight, x2: width - margin.right, y2: margin.top + plotHeight, class: "chart-axis" }));

  [0, 0.25, 0.5, 0.75, 1].forEach((value) => {
    const x = margin.left + value * plotWidth;
    svg.appendChild(svgElement("line", { x1: x, y1: margin.top + plotHeight, x2: x, y2: margin.top + plotHeight + 4, class: "chart-axis" }));
    svg.appendChild(svgElement("text", { x, y: height - 15, class: "chart-label", "text-anchor": "middle" }, value.toFixed(2)));
  });
  svg.appendChild(svgElement("text", { x: margin.left - 9, y: margin.top + 4, class: "chart-label", "text-anchor": "end" }, formatInt(Math.max(...counts))));
  svg.appendChild(svgElement("text", { x: margin.left - 9, y: margin.top + plotHeight + 4, class: "chart-label", "text-anchor": "end" }, "0"));

  const statItems = [
    ["Below prior", distribution.below_prior_count, true],
    ["At prior", distribution.at_exact_prior_count, true],
    ["Above cutoff", distribution.above_output_cutoff_count, true],
    ["Mean", formatProbability(distribution.mean), false],
    ["Range", `${formatProbability(distribution.minimum)}–${formatProbability(distribution.maximum)}`, false],
  ];
  statItems.forEach(([label, value, isCount]) => {
    const wrapper = document.createElement("div");
    const term = document.createElement("dt");
    const detail = document.createElement("dd");
    term.textContent = label;
    detail.textContent = isCount ? formatInt(value) : value;
    wrapper.append(term, detail);
    stats.appendChild(wrapper);
  });
}

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
    card.classList.add("with-preferred-tq");
    const normalization = document.createElement("label");
    normalization.className = "normalization-field";
    normalization.title = stream.normalization.help;
    normalization.innerHTML = `<span>${stream.normalization.control_label}</span><input class="stream-tq stream-setting" type="number" min="0.05" max="20" step="0.05" value="${current.tq_multiplier}" ${current.enabled ? "" : "disabled"} aria-label="${stream.label} ${stream.normalization.control_label}" />`;
    card.appendChild(normalization);
    const preferred = document.createElement("label");
    const bounds = stream.normalization.calibration_bounds || [0.25, 4];
    const preferredLabel = stream.normalization.control_label.startsWith("Ref")
      ? "Preferred Ref ×"
      : "Preferred Tq ×";
    preferred.className = "preferred-tq-field";
    preferred.title = "Regularization anchor used when positive-control calibration is enabled; it does not directly rescore an ordinary uncalibrated run.";
    preferred.innerHTML = `<span>${preferredLabel}</span><input class="stream-preferred-tq stream-calibration-setting" type="number" min="${bounds[0]}" max="${bounds[1]}" step="any" value="${current.preferred_tq_multiplier}" ${(current.enabled && state.defaults.calibration.enabled) ? "" : "disabled"} aria-label="${stream.label} ${preferredLabel}" />`;
    card.appendChild(preferred);
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
  if (group === "node" || !stream.derived) {
    const negative = document.createElement("label");
    negative.className = "continuous-negative-field";
    negative.title = "Remove the positive-only floor for this source. Eligible nondetections are x=0; weak observations may produce BF below 1.";
    negative.innerHTML = `<input class="stream-continuous-negative stream-setting" type="checkbox" ${current.continuous_negative_evidence ? "checked" : ""} ${current.enabled ? "" : "disabled"} /><span>Allow continuous negative evidence (weak values + x=0 nondetections)</span>`;
    card.appendChild(negative);
  }
  toggle.addEventListener("change", () => {
    if (toggle.checked && stream.exclusive_group) {
      document.querySelectorAll(`[data-group="${group}"]`).forEach((other) => {
        const otherDef = state.registry[`${group}_streams`].find((item) => item.id === other.dataset.stream);
        if (other !== card && otherDef?.exclusive_group === stream.exclusive_group) {
          other.querySelector(".stream-toggle").checked = false;
          other.querySelectorAll(".stream-setting, .stream-calibration-setting").forEach((input) => { input.disabled = true; });
          other.classList.remove("enabled");
        }
      });
    }
    card.classList.toggle("enabled", toggle.checked);
    card.querySelectorAll(".stream-setting").forEach((input) => { input.disabled = !toggle.checked; });
    card.querySelectorAll(".stream-calibration-setting").forEach((input) => {
      input.disabled = !toggle.checked || !$('calibration-enabled').checked;
    });
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
  const calibration = state.defaults.calibration;
  $("calibration-enabled").checked = calibration.enabled;
  $("calibration-known-nodes").value = (calibration.known_nodes || []).join("\n");
  $("calibration-known-edges").value = (calibration.known_edges || []).map((pair) => pair.join(",")).join("\n");
  $("calibration-lambda").value = calibration.regularization_strength;
  const node = state.defaults.node_integration;
  $("include-messengers").checked = node.include_second_messengers;
  $("penalize-unobserved").checked = node.penalize_unobserved;
  $("unobserved-bf").value = node.unobserved_bayes_factor;
  $("unobserved-bf").disabled = !node.penalize_unobserved;
  $("continuous-node-bf-floor").value = node.continuous_bayes_factor_floor;
  $("node-prior").value = node.prior_probability;
  $("node-cutoff").value = node.output_probability_cutoff;
  const edge = state.defaults.edge_integration;
  $("penalize-unsupported-edges").checked = edge.penalize_unsupported;
  $("unsupported-edge-bf").value = edge.unsupported_bayes_factor;
  $("unsupported-edge-bf").disabled = !edge.penalize_unsupported;
  $("continuous-edge-bf-floor").value = edge.continuous_bayes_factor_floor;
  $("edge-prior").value = edge.prior_probability;
  $("edge-cutoff").value = edge.output_probability_cutoff;
  const path = state.defaults.path;
  $("path-enabled").checked = path.enabled;
  $("path-start").value = path.start;
  $("path-target").value = path.target;
  $("path-top-k").value = path.top_k;
  $("path-max-hops").value = path.max_hops;
  $("path-cutoff").value = path.minimum_edge_probability;
  $("ontology-directionality").checked = path.ontology_directionality_enabled;
  $("signal-only").checked = path.signaling_intermediates_only;
  $("exclude-multirole-scaffolds").checked = path.exclude_multirole_scaffolds;
  renderPathOntologyClasses(path);
  const temporal = state.defaults.temporal_validation;
  $("temporal-enabled").checked = temporal.enabled;
  $("temporal-prior-df").value = temporal.prior_df;
  $("temporal-alpha").value = temporal.alpha;
  $("temporal-draws").value = temporal.monte_carlo_draws;
  $("temporal-seed").value = temporal.random_seed;
  $("temporal-p-adjust").value = temporal.p_adjust_method;
  $("temporal-min-scored").value = temporal.minimum_scored_nodes;
  setCalibrationControls(calibration.enabled);
  setPathControls(path.enabled);
}

function collectStreams(group) {
  const result = {};
  document.querySelectorAll(`[data-group="${group}"]`).forEach((card) => {
    const parameters = {};
    card.querySelectorAll(".stream-parameter").forEach((input) => {
      parameters[input.dataset.parameter] = Number(input.value);
    });
    const streamState = {
      enabled: card.querySelector(".stream-toggle").checked,
      weight: Number(card.querySelector(".stream-weight").value),
      tq_multiplier: Number(card.querySelector(".stream-tq")?.value ?? 1),
      parameters,
    };
    const continuousNegative = card.querySelector(".stream-continuous-negative");
    if (continuousNegative) streamState.continuous_negative_evidence = continuousNegative.checked;
    const preferred = card.querySelector(".stream-preferred-tq");
    if (preferred) streamState.preferred_tq_multiplier = Number(preferred.value);
    result[card.dataset.stream] = streamState;
  });
  return result;
}

function collectConfiguration() {
  return {
    calibration: {
      enabled: $("calibration-enabled").checked,
      known_nodes: $("calibration-known-nodes").value.split(/[\s,;]+/).map((value) => value.trim()).filter(Boolean),
      known_edges: $("calibration-known-edges").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
      regularization_strength: Number($("calibration-lambda").value),
      multistart_count: 2,
    },
    node_streams: collectStreams("node"),
    node_integration: {
      include_second_messengers: $("include-messengers").checked,
      penalize_unobserved: $("penalize-unobserved").checked,
      unobserved_bayes_factor: Number($("unobserved-bf").value),
      continuous_negative_evidence: false,
      continuous_bayes_factor_floor: Number($("continuous-node-bf-floor").value),
      prior_probability: Number($("node-prior").value),
      output_probability_cutoff: Number($("node-cutoff").value),
    },
    edge_streams: collectStreams("edge"),
    edge_integration: {
      penalize_unsupported: $("penalize-unsupported-edges").checked,
      unsupported_bayes_factor: Number($("unsupported-edge-bf").value),
      continuous_negative_evidence: false,
      continuous_bayes_factor_floor: Number($("continuous-edge-bf-floor").value),
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
      ontology_directionality_enabled: $("ontology-directionality").checked,
      signaling_intermediates_only: $("signal-only").checked,
      exclude_multirole_scaffolds: $("exclude-multirole-scaffolds").checked,
      allowed_intermediate_classes: Array.from(
        document.querySelectorAll(".path-ontology-class:checked")
      ).map((input) => input.value),
    },
    temporal_validation: {
      enabled: $("temporal-enabled").checked,
      prior_df: Number($("temporal-prior-df").value),
      alpha: Number($("temporal-alpha").value),
      monte_carlo_draws: Number($("temporal-draws").value),
      random_seed: Number($("temporal-seed").value),
      p_adjust_method: $("temporal-p-adjust").value,
      minimum_scored_nodes: Number($("temporal-min-scored").value),
    },
  };
}

function setCalibrationControls(enabled) {
  $("calibration-controls").querySelectorAll("input, textarea").forEach((control) => {
    control.disabled = !enabled;
  });
  $("calibration-controls").classList.toggle("inactive", !enabled);
  document.querySelectorAll(".stream-calibration-setting").forEach((control) => {
    const card = control.closest(".stream-card");
    control.disabled = !enabled || !card.querySelector(".stream-toggle").checked;
  });
}

function setPathControls(enabled) {
  $("path-controls").querySelectorAll("input, button").forEach((control) => { control.disabled = !enabled; });
  $("path-controls").style.opacity = enabled ? "1" : ".45";
  setOntologyControls(enabled);
  setTemporalControls(enabled);
}

function setOntologyControls(pathEnabled) {
  const enabled = pathEnabled && $("signal-only").checked;
  $("ontology-selector").querySelectorAll("input, button").forEach((control) => {
    control.disabled = !enabled;
  });
  $("ontology-selector").classList.toggle("inactive", !enabled);
}

function setTemporalControls(pathEnabled) {
  const toggle = $("temporal-enabled");
  toggle.disabled = !pathEnabled;
  if (!pathEnabled) toggle.checked = false;
  const enabled = pathEnabled && toggle.checked;
  $("temporal-controls").querySelectorAll("input, select").forEach((control) => {
    control.disabled = !enabled;
  });
  $("temporal-controls").classList.toggle("inactive", !enabled);
}

function showPanel(name) {
  ["empty-state", "job-state", "result-state", "error-state"].forEach((id) => $(id).classList.add("hidden"));
  $(name).classList.remove("hidden");
}

function showWorkflowError(error, fallbackMessage = "The analysis could not be completed.") {
  const rawMessage = String(error?.message || error || fallbackMessage);
  const disconnected = error instanceof TypeError
    || /failed to fetch|networkerror|load failed|network request failed/i.test(rawMessage);
  $("error-eyebrow").textContent = disconnected ? "Local connection lost" : "Run stopped";
  $("error-title").textContent = disconnected
    ? "Local analysis server is not running"
    : "Configuration needs attention";
  $("error-message").textContent = disconnected
    ? "The browser interface is still open, but its Python backend is unavailable. Start the workbench server, then reconnect. Your saved project data are unaffected."
    : rawMessage;
  $("reconnect-server").classList.toggle("hidden", !disconnected);
  showPanel("error-state");
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
  renderProbabilityDistribution("node", preview.probability_distributions?.nodes);
  renderProbabilityDistribution("edge", preview.probability_distributions?.edges);
  const calibration = preview.calibration;
  $("calibration-result").classList.toggle("hidden", !calibration?.enabled);
  if (calibration?.enabled) {
    const fits = [calibration.node, calibration.edge].filter(Boolean);
    const targetCount = fits.reduce((total, fit) => total + Number(fit.target_count || 0), 0);
    const initialMean = fits.length
      ? fits.reduce((total, fit) => total + Number(fit.initial_mean_target_probability || 0), 0) / fits.length
      : 0;
    const finalMean = fits.length
      ? fits.reduce((total, fit) => total + Number(fit.final_mean_target_probability || 0), 0) / fits.length
      : 0;
    $("calibration-result-score").textContent = `${formatProbability(initialMean)} → ${formatProbability(finalMean)}`;
    $("calibration-result-detail").textContent = `${formatInt(targetCount)} positive controls fitted in ${fits.length} independent stage${fits.length === 1 ? "" : "s"} using bounded SciPy Powell. Unknown hypotheses were not treated as negatives; derived scaffold closure was fixed.`;
  }
  const directionality = preview.directionality?.edge_output_graph;
  $("directionality-result").classList.toggle("hidden", !directionality);
  if (directionality) {
    const percent = 100 * Number(directionality.proportion_uniquely_oriented || 0);
    $("oriented-edge-count").textContent = formatInt(directionality.uniquely_oriented_edge_count);
    $("oriented-edge-percent").textContent = `${percent.toFixed(1)}%`;
    $("directionality-result-detail").textContent = `${formatInt(directionality.retained_unique_edge_count)} edges above the output cutoff; ${formatInt(directionality.unresolved_no_matching_rule_count)} had no matching rule and ${formatInt(directionality.unresolved_conflicting_rules_count)} had conflicting multi-role rules.`;
  }
  const temporal = preview.temporal_validation;
  const temporalExecuted = Boolean(temporal?.executed);
  $("temporal-result").classList.toggle("hidden", !temporalExecuted);
  document.querySelectorAll(".temporal-column").forEach((column) => {
    column.classList.toggle("hidden", !temporalExecuted);
  });
  if (temporalExecuted) {
    $("temporal-informative-count").textContent = formatInt(temporal.temporally_informative_path_count);
    $("temporal-passing-genes").textContent = `${formatInt(temporal.genes_passing_gate)} genes pass`;
    $("temporal-result-detail").textContent = `${formatInt(temporal.measured_gene_count)} genes measured; ${formatInt(temporal.paths_with_at_least_two_scored_nodes)} paths had at least two scored nodes. Peak p adjustment: ${String(temporal.p_adjust_method).replaceAll("_", " ")}. Primary Bayesian ranks were preserved.`;
  }
  const body = $("paths-body");
  body.innerHTML = "";
  if (!preview.top_paths.length) {
    body.innerHTML = `<tr><td colspan="${temporalExecuted ? 6 : 4}" class="muted">Path finding was disabled or no supported route was found.</td></tr>`;
  } else {
    preview.top_paths.forEach((path) => {
      const row = document.createElement("tr");
      const temporalCells = temporalExecuted
        ? `<td>${formatInt(path.temporal_n_scored)}</td><td class="score">${formatProbability(path.temporal_kendall_tau_mean)} [${formatProbability(path.temporal_kendall_tau_low)}, ${formatProbability(path.temporal_kendall_tau_high)}]</td>`
        : "";
      row.innerHTML = `<td>${path.rank}</td><td class="route">${path.path_symbols}</td><td>${path.hop_count}</td><td class="score">${formatScore(path.path_probability_product)}</td>${temporalCells}`;
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
      showWorkflowError(job.error || "The analysis could not be completed.");
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
    showWorkflowError(error);
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
    showWorkflowError(error);
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
    showWorkflowError(error);
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
    } else {
      $("run-button").disabled = false;
    }
  } catch (error) {
    showWorkflowError(error);
  }
}

$("workflow-form").addEventListener("submit", startRun);
$("cancel-job-button").addEventListener("click", cancelRun);
$("reset-button").addEventListener("click", () => { populateControls(); showPanel("empty-state"); });
$("calibration-enabled").addEventListener("change", (event) => setCalibrationControls(event.target.checked));
$("path-enabled").addEventListener("change", (event) => setPathControls(event.target.checked));
$("temporal-enabled").addEventListener("change", () => setTemporalControls($("path-enabled").checked));
$("signal-only").addEventListener("change", () => setOntologyControls($("path-enabled").checked));
$("penalize-unobserved").addEventListener("change", (event) => {
  $("unobserved-bf").disabled = !event.target.checked;
});
$("penalize-unsupported-edges").addEventListener("change", (event) => {
  $("unsupported-edge-bf").disabled = !event.target.checked;
});
$("ontology-all").addEventListener("click", () => {
  document.querySelectorAll(".path-ontology-class").forEach((input) => { input.checked = true; });
});
$("ontology-none").addEventListener("click", () => {
  document.querySelectorAll(".path-ontology-class").forEach((input) => { input.checked = false; });
});
$("dismiss-error").addEventListener("click", () => showPanel("empty-state"));
$("reconnect-server").addEventListener("click", () => window.location.reload());
initialize();
