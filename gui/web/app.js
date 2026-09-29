const state = {
  registry: null,
  defaults: null,
  jobId: null,
  pollTimer: null,
  pathNetwork: null,
  networkRankLimit: 50,
  networkLayoutSeed: 0,
  networkResizeTimer: null,
  selectedPathRank: null,
  inspectionHistory: [],
  currentEvidenceInspection: null,
  literatureHistory: [],
  networkLiteratureHistory: [],
  networkLiteratureResearchId: null,
  networkLiteraturePollTimer: null,
  networkLiteratureActive: false,
  literatureInterpreter: null,
};

const $ = (id) => document.getElementById(id);
const formatInt = (value) => new Intl.NumberFormat("en-US").format(Number(value || 0));
const formatScore = (value) => Number(value).toPrecision(8);
const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

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

function formatEvidenceNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  if (number === 0) return "0";
  if (number > 0.9999 && number < 1) {
    return number.toFixed(12).replace(/0+$/, "").replace(/\.$/, "");
  }
  if (Math.abs(number) >= 1000 || Math.abs(number) < 0.001) return number.toExponential(3);
  return number.toPrecision(5).replace(/0+$/, "").replace(/\.$/, "");
}

function evidenceScopeText(stream, kind) {
  if (!stream.enabled) return "Not used";
  if (kind === "node") {
    if (stream.observed) return "Observed";
    if (stream.fixed_absence_penalty_applied) return "Not observed · penalty applied";
    if (stream.negative_evidence_eligible === false) return "Not observed · absence not scorable";
    return "Not observed · eligible";
  }
  if (stream.source_record_retained) {
    return stream.negative_evidence_eligible === false
      ? "Record retained · absence not scorable"
      : "Source record retained";
  }
  if (stream.fixed_absence_penalty_applied || stream.absence_penalty_applied) {
    return "No record · penalty applied";
  }
  if (stream.continuous_negative_evidence_applied) {
    return "No record · continuous penalty";
  }
  if (stream.negative_evidence_eligible === false) return "No record · no absence penalty";
  return stream.derived ? "Rule not triggered" : "No record · eligible";
}

function evidencePercentileText(position) {
  if (!position) return "—";
  const lower = Number(position.lower_percentile || 0);
  const upper = Number(position.upper_percentile || 0);
  const prefix = position.exact ? "" : "≈ ";
  if (Math.abs(upper - lower) > 0.11) {
    return `${prefix}${lower.toFixed(1)}–${upper.toFixed(1)} percentile`;
  }
  return `${prefix}${Number(position.midpoint_percentile || 0).toFixed(1)} percentile`;
}

function renderEvidenceFactorDistribution(stream) {
  const panel = $("evidence-stream-distribution");
  const svg = $("evidence-stream-distribution-chart");
  const summary = $("evidence-stream-distribution-summary");
  const distribution = stream.factor_distribution;
  const edges = distribution?.bin_edges_log2 || [];
  const counts = distribution?.bin_counts || [];
  if (!distribution || !counts.length || edges.length !== counts.length + 1) {
    panel.classList.remove("hidden");
    svg.classList.add("hidden");
    svg.innerHTML = "";
    summary.textContent = stream.enabled
      ? "This historical run does not contain a factor distribution for this stream. Re-run the workflow to generate it."
      : "This stream was disabled, so it contributed no Bayes-factor distribution to this run.";
    return;
  }

  panel.classList.remove("hidden");
  svg.classList.remove("hidden");
  svg.innerHTML = "";
  const width = 720;
  const height = 164;
  const margin = { left: 42, right: 16, top: 18, bottom: 34 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const domainMin = Number(edges[0]);
  const domainMax = Number(edges[edges.length - 1]);
  const x = (value) => margin.left + ((Number(value) - domainMin) / Math.max(domainMax - domainMin, 1e-12)) * plotWidth;
  const maxLogCount = Math.max(...counts.map((count) => Math.log10(Number(count) + 1)), 1);
  counts.forEach((count, index) => {
    const x0 = x(edges[index]);
    const x1 = x(edges[index + 1]);
    const barHeight = (Math.log10(Number(count) + 1) / maxLogCount) * plotHeight;
    const midpoint = (Number(edges[index]) + Number(edges[index + 1])) / 2;
    const evidenceClass = midpoint < -1e-12 ? "refuting" : midpoint > 1e-12 ? "supporting" : "neutral";
    const rect = svgElement("rect", {
      x: (x0 + 0.5).toFixed(2),
      y: (margin.top + plotHeight - barHeight).toFixed(2),
      width: Math.max(0.5, x1 - x0 - 1).toFixed(2),
      height: Math.max(0, barHeight).toFixed(2),
      class: `evidence-factor-bar ${evidenceClass}`,
    });
    rect.appendChild(svgElement("title", {}, `${formatInt(count)} hypotheses in BF ${formatEvidenceNumber(2 ** Number(edges[index]))}–${formatEvidenceNumber(2 ** Number(edges[index + 1]))}`));
    svg.appendChild(rect);
  });

  const axisY = margin.top + plotHeight;
  svg.appendChild(svgElement("line", { x1: margin.left, y1: axisY, x2: width - margin.right, y2: axisY, class: "evidence-factor-axis" }));
  const neutralX = x(0);
  svg.appendChild(svgElement("line", { x1: neutralX, y1: margin.top, x2: neutralX, y2: axisY, class: "evidence-factor-neutral" }));
  svg.appendChild(svgElement("text", { x: neutralX, y: height - 17, "text-anchor": "middle", class: "evidence-factor-axis-label" }, "BF 1 · neutral"));
  svg.appendChild(svgElement("text", { x: margin.left, y: height - 17, "text-anchor": "start", class: "evidence-factor-axis-label" }, "Refutes ←"));
  svg.appendChild(svgElement("text", { x: width - margin.right, y: height - 17, "text-anchor": "end", class: "evidence-factor-axis-label" }, "→ Supports"));

  const factor = Number(stream.applied_bayes_factor);
  if (Number.isFinite(factor) && factor > 0) {
    const markerX = x(Math.max(domainMin, Math.min(domainMax, Math.log2(factor))));
    svg.appendChild(svgElement("line", { x1: markerX, y1: margin.top - 2, x2: markerX, y2: axisY, class: "evidence-factor-selected" }));
    svg.appendChild(svgElement("circle", { cx: markerX, cy: margin.top - 3, r: 4, class: "evidence-factor-selected-dot" }));
  }

  const position = evidencePercentileText(stream.distribution_position);
  const scope = distribution.distribution_scope || "modeled hypotheses";
  const exactness = stream.distribution_position?.exact ? "empirical" : "histogram-estimated";
  summary.textContent = `Applied BF ${formatEvidenceNumber(stream.applied_bayes_factor)} is at ${position} (${exactness}) among ${formatInt(distribution.hypothesis_count)} ${scope}. Refuting: ${formatInt(distribution.refuting_count)}; neutral: ${formatInt(distribution.neutral_count)}; supporting: ${formatInt(distribution.supporting_count)}.`;
}

function readableTraceValue(value) {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return formatEvidenceNumber(value);
  if (Array.isArray(value)) return value.join("; ") || "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function traceRecordTitle(record, index) {
  const directed = record.source_symbol && record.target_symbol
    ? `${record.source_symbol} → ${record.target_symbol}`
    : null;
  const pair = record.source_symbol_a && record.source_symbol_b
    ? `${record.source_symbol_a} — ${record.source_symbol_b}`
    : null;
  const resource = Array.isArray(record.resources) ? record.resources.join(", ") : record.resources;
  return directed || pair || resource || `Source record ${index + 1}`;
}

function renderDatabaseTrace(trace) {
  const panel = $("database-trace");
  const derivation = $("database-trace-derivation");
  const factors = $("database-trace-factors");
  const records = $("database-trace-records");
  const links = $("database-trace-links");
  derivation.innerHTML = "";
  factors.innerHTML = "";
  records.innerHTML = "";
  links.innerHTML = "";
  if (!trace) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  $("database-trace-title").textContent = `${trace.database || "Database"} source traceback`;
  $("database-trace-status").textContent = String(trace.trace_status || "available").replaceAll("_", " ");
  $("database-trace-summary").textContent = trace.summary || "No traceback summary was provided.";

  const derivationValues = trace.score_derivation || {};
  [
    ["Database score", derivationValues.database_score],
    ["Raw Bayes factor", derivationValues.raw_bayes_factor],
    ["Model weight", derivationValues.model_weight],
    ["Effective BF", derivationValues.weighted_effective_bayes_factor],
    ["Formula", derivationValues.formula],
    ["Important limit", derivationValues.important_limit],
  ].forEach(([label, value]) => {
    if (value === null || value === undefined || value === "") return;
    const card = document.createElement("div");
    const name = document.createElement("span");
    const strong = document.createElement("strong");
    name.textContent = label;
    strong.textContent = readableTraceValue(value);
    card.append(name, strong);
    derivation.appendChild(card);
  });

  (trace.factors || []).forEach((factor) => {
    const card = document.createElement("div");
    card.className = "trace-factor";
    const title = document.createElement("strong");
    const value = document.createElement("span");
    const note = document.createElement("small");
    title.textContent = factor.factor || "Factor";
    value.textContent = readableTraceValue(factor.value);
    note.textContent = factor.description || factor.role || "";
    card.append(title, value, note);
    factors.appendChild(card);
  });

  (trace.records || []).slice(0, 30).forEach((record, index) => {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    const pre = document.createElement("pre");
    summary.textContent = traceRecordTitle(record, index);
    pre.textContent = JSON.stringify(record, null, 2);
    details.append(summary, pre);
    records.appendChild(details);
  });

  (trace.links || []).forEach((item) => {
    if (!String(item.url || "").startsWith("http")) return;
    const link = document.createElement("a");
    link.href = item.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = item.label || item.url;
    links.appendChild(link);
  });
}

function networkLiteratureScope() {
  const network = state.pathNetwork;
  if (!network) return { paths: 0, nodes: [], edges: [] };
  const paths = Math.min(state.networkRankLimit, Number(network.visualized_path_count || 0));
  const visible = visiblePathNetwork(network, paths);
  return { paths, nodes: visible.nodes, edges: visible.edges };
}

const LITERATURE_CONNECTION_STORAGE_KEY = "gbi-literature-connection-v1";

function literatureEndpointConfiguration() {
  const raw = $("literature-api-base-url").value.trim();
  const model = $("literature-model").value.trim();
  let url;
  try {
    url = new URL(raw);
  } catch (_error) {
    return { valid: false, message: "Enter a complete HTTPS API base URL." };
  }
  const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
  const isPublicOpenAI = hostname === "api.openai.com";
  const isAzure = hostname.endsWith(".openai.azure.com") || hostname.endsWith(".services.ai.azure.com");
  const path = url.pathname.replace(/\/+$/, "");
  if (url.protocol !== "https:" || (!isPublicOpenAI && !isAzure)) {
    return { valid: false, message: "Use api.openai.com or an official Azure OpenAI HTTPS hostname." };
  }
  if (isAzure && !path.endsWith("/openai/v1")) {
    return { valid: false, message: "Azure API base URLs must end in /openai/v1." };
  }
  if (isPublicOpenAI && !path.endsWith("/v1")) {
    return { valid: false, message: "The public OpenAI API base URL must end in /v1." };
  }
  if (!model) return { valid: false, message: "Enter a model ID or Azure deployment name." };
  const baseUrl = `${url.origin}${path}`;
  return {
    valid: true,
    provider: isAzure ? "Azure OpenAI" : "OpenAI",
    baseUrl,
    responsesUrl: `${baseUrl}/responses`,
    message: isAzure
      ? `Azure OpenAI · POST ${baseUrl}/responses · deployment: ${model}`
      : `OpenAI · POST ${baseUrl}/responses · model: ${model}`,
  };
}

function saveLiteratureConnectionPreferences() {
  try {
    localStorage.setItem(LITERATURE_CONNECTION_STORAGE_KEY, JSON.stringify({
      apiBaseUrl: $("literature-api-base-url").value.trim(),
      model: $("literature-model").value.trim(),
    }));
  } catch (_error) {
    // Browser storage can be unavailable in locked-down environments. The form still works.
  }
}

function restoreLiteratureConnectionPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(LITERATURE_CONNECTION_STORAGE_KEY) || "null");
    if (saved?.apiBaseUrl) $("literature-api-base-url").value = String(saved.apiBaseUrl);
    if (saved?.model) $("literature-model").value = String(saved.model);
  } catch (_error) {
    // Ignore malformed or unavailable browser storage and keep server defaults.
  }
}

function updateLiteratureControls() {
  const scope = networkLiteratureScope();
  const browserKey = $("literature-api-key").value.trim();
  const serverKey = Boolean(state.literatureInterpreter?.available);
  const credentialReady = Boolean(browserKey) || serverKey;
  const endpoint = literatureEndpointConfiguration();
  $("literature-endpoint-preview").textContent = endpoint.message;
  $("literature-endpoint-preview").classList.toggle("warning", !endpoint.valid);
  $("literature-path-count").textContent = formatInt(scope.paths);
  $("literature-node-count").textContent = formatInt(scope.nodes.length);
  $("literature-edge-count").textContent = formatInt(scope.edges.length);
  $("literature-batch-count").textContent = formatInt(Math.ceil((scope.nodes.length + scope.edges.length) / 6));
  $("research-pathway-network").disabled = state.networkLiteratureActive || !state.jobId || !scope.paths || !credentialReady || !endpoint.valid;
  $("literature-interpreter-availability").textContent = serverKey
    ? `${state.literatureInterpreter.default_model || "LLM"} · server key ready`
    : browserKey
      ? "Session key entered"
      : "Session key required";
  if (state.networkLiteratureActive) return;
  if (!scope.paths) {
    $("literature-status").textContent = "Complete a path-finding run to define the network scope.";
  } else if (!endpoint.valid) {
    $("literature-status").textContent = endpoint.message;
  } else if (!credentialReady) {
    $("literature-status").textContent = "Enter an API key above. It will not be saved or included in the analysis configuration.";
  } else {
    $("literature-status").textContent = `Ready to research ${formatInt(scope.nodes.length)} nodes and ${formatInt(scope.edges.length)} edges from the top ${formatInt(scope.paths)} displayed paths.`;
  }
}

function addLiteratureTextSection(container, title, value) {
  if (!value || (Array.isArray(value) && !value.length)) return;
  const section = document.createElement("section");
  section.className = "literature-section";
  const heading = document.createElement("h5");
  heading.textContent = title;
  section.appendChild(heading);
  if (Array.isArray(value)) {
    const list = document.createElement("ul");
    value.forEach((item) => {
      const row = document.createElement("li");
      row.textContent = String(item);
      list.appendChild(row);
    });
    section.appendChild(list);
  } else {
    const text = document.createElement("p");
    text.textContent = String(value);
    section.appendChild(text);
  }
  container.appendChild(section);
}

function rememberLiteratureInterpretation(payload) {
  const key = literatureHypothesisKey(payload.hypothesis || {});
  const existing = state.literatureHistory.findIndex(
    (item) => literatureHypothesisKey(item.hypothesis || {}) === key,
  );
  if (existing >= 0) state.literatureHistory.splice(existing, 1);
  state.literatureHistory.push(JSON.parse(JSON.stringify(payload)));
  if (state.literatureHistory.length > 1000) state.literatureHistory.shift();
}

function literatureHypothesisKey(hypothesis) {
  if (hypothesis?.kind === "node") {
    return `node:${String(hypothesis.symbol || "").trim().toLowerCase()}`;
  }
  const pair = [hypothesis?.node_a, hypothesis?.node_b]
    .map((value) => String(value || "").trim().toLowerCase())
    .sort();
  return `edge:${pair.join("|")}`;
}

function evidenceHypothesisKey(payload) {
  return literatureHypothesisKey(payload.kind === "node"
    ? { kind: "node", symbol: payload.symbol }
    : { kind: "edge", node_a: payload.node_a, node_b: payload.node_b });
}

function selectedLiteratureRecord(payload) {
  const key = evidenceHypothesisKey(payload);
  for (let index = state.literatureHistory.length - 1; index >= 0; index -= 1) {
    const record = state.literatureHistory[index];
    if (literatureHypothesisKey(record.hypothesis || {}) === key) return record;
  }
  return null;
}

function appendSelectedLiteratureAssessment(label, value) {
  if (!value || (Array.isArray(value) && !value.length)) return;
  const row = document.createElement("tr");
  const heading = document.createElement("th");
  const body = document.createElement("td");
  heading.scope = "row";
  heading.textContent = label;
  if (Array.isArray(value)) {
    const list = document.createElement("ul");
    value.forEach((item) => {
      const entry = document.createElement("li");
      entry.textContent = String(item);
      list.appendChild(entry);
    });
    body.appendChild(list);
  } else {
    body.textContent = String(value);
  }
  row.append(heading, body);
  $("selected-literature-assessment-body").appendChild(row);
}

function renderSelectedLiterature(payload) {
  const panel = $("evidence-literature-interpretation");
  const record = selectedLiteratureRecord(payload);
  if (!record) {
    panel.classList.add("hidden");
    return;
  }
  const result = record.interpretation || {};
  const label = result.hypothesis_label || (payload.kind === "node"
    ? payload.symbol
    : `${payload.node_a} — ${payload.node_b}`);
  $("selected-literature-title").textContent = label;
  $("selected-literature-model").textContent = `${record.model || "LLM"} · ${record.reasoning_effort || ""}`;
  $("selected-literature-classification").textContent = String(result.classification || "uncertain").replaceAll("_", " ");
  $("selected-literature-confidence").textContent = `${Math.round(Number(result.confidence || 0) * 100)}%`;
  $("selected-literature-context").textContent = record.cell_type || "Not specified";
  $("selected-literature-takeaway").textContent = result.one_sentence_takeaway || "No concise takeaway was returned.";

  $("selected-literature-assessment-body").innerHTML = "";
  appendSelectedLiteratureAssessment("Signaling purpose", record.signaling_purpose);
  appendSelectedLiteratureAssessment("Novelty", result.novelty_interpretation);
  appendSelectedLiteratureAssessment("Mechanistic interpretation", result.mechanistic_interpretation);
  appendSelectedLiteratureAssessment("Bayesian evidence", result.bayesian_evidence_summary);
  appendSelectedLiteratureAssessment("Database tracebacks", result.database_trace_summary);
  appendSelectedLiteratureAssessment("Conflicting or missing evidence", result.conflicting_or_missing_evidence);
  appendSelectedLiteratureAssessment("Caveats", result.caveats);

  const claims = result.contextual_evidence || [];
  const claimsSection = $("selected-literature-claims");
  const claimsBody = $("selected-literature-claims-body");
  claimsBody.innerHTML = "";
  claimsSection.classList.toggle("hidden", !claims.length);
  claims.forEach((claim) => {
    const row = document.createElement("tr");
    const scope = document.createElement("td");
    const context = document.createElement("td");
    const support = document.createElement("td");
    const text = document.createElement("td");
    const sources = document.createElement("td");
    scope.textContent = String(claim.scope || "").replaceAll("_", " ");
    context.textContent = claim.biological_context || "Not specified";
    support.textContent = String(claim.support || "").replaceAll("_", " ");
    text.textContent = claim.claim || "";
    (claim.source_urls || []).forEach((url, index) => {
      if (!String(url).startsWith("http")) return;
      if (sources.childNodes.length) sources.append(" · ");
      const link = document.createElement("a");
      link.href = url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = `Source ${index + 1}`;
      sources.appendChild(link);
    });
    row.append(scope, context, support, text, sources);
    claimsBody.appendChild(row);
  });

  const sourceBox = $("selected-literature-sources");
  sourceBox.innerHTML = "";
  const seen = new Set();
  [...(result.sources || []), ...(record.web_sources || [])].forEach((source) => {
    const url = String(source.url || "");
    if (!url.startsWith("http") || seen.has(url)) return;
    seen.add(url);
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = source.title || url;
    link.title = source.relevance || "";
    sourceBox.appendChild(link);
  });
  panel.classList.remove("hidden");
}

function addLiteratureClaims(container, claims) {
  if (!(claims || []).length) return;
  const section = document.createElement("section");
  section.className = "literature-section";
  const heading = document.createElement("h5");
  heading.textContent = "Contextual literature evidence";
  section.appendChild(heading);
  claims.forEach((claim) => {
    const card = document.createElement("div");
    card.className = "literature-claim";
    const strong = document.createElement("strong");
    const scope = document.createElement("small");
    strong.textContent = claim.claim || "";
    scope.textContent = `${String(claim.scope || "").replaceAll("_", " ")} · ${claim.support || ""}`;
    card.append(strong, scope);
    const links = document.createElement("div");
    links.className = "trace-links";
    (claim.source_urls || []).forEach((url, index) => {
      if (!String(url).startsWith("http")) return;
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      anchor.textContent = `Source ${index + 1}`;
      links.appendChild(anchor);
    });
    card.appendChild(links);
    section.appendChild(card);
  });
  container.appendChild(section);
}

function renderNetworkLiterature(payload) {
  state.networkLiteratureHistory.push(JSON.parse(JSON.stringify(payload)));
  if (state.networkLiteratureHistory.length > 10) state.networkLiteratureHistory.shift();
  (payload.items || []).forEach(rememberLiteratureInterpretation);
  $("literature-result").classList.remove("hidden");
  const synthesis = payload.synthesis || {};
  $("literature-network-takeaway").textContent = synthesis.overall_summary || "The network audit completed.";
  $("literature-network-model").textContent = `${payload.model || "model"} · ${payload.reasoning_effort || ""}`;
  $("literature-item-count").textContent = `${formatInt(payload.hypothesis_count)} interpretations are ready. Click a node or edge in the graph to see its structured literature assessment inside the Interpretation panel.`;

  const counts = $("literature-classification-counts");
  counts.innerHTML = "";
  Object.entries(payload.classification_counts || {}).sort((a, b) => b[1] - a[1]).forEach(([label, count]) => {
    const card = document.createElement("div");
    const name = document.createElement("span");
    const value = document.createElement("strong");
    name.textContent = label.replaceAll("_", " ");
    value.textContent = formatInt(count);
    card.append(name, value);
    counts.appendChild(card);
  });

  if (state.currentEvidenceInspection) renderSelectedLiterature(state.currentEvidenceInspection);
}

function updateNetworkLiteratureProgress(payload) {
  state.networkLiteratureActive = !["complete", "failed", "cancelled"].includes(payload.status);
  $("literature-progress").classList.toggle("hidden", !state.networkLiteratureActive && payload.status !== "complete");
  $("literature-progress-bar").style.width = `${Math.max(0, Math.min(100, Number(payload.progress || 0) * 100))}%`;
  $("literature-progress-label").textContent = `${payload.message || payload.status} · ${Math.round(Number(payload.progress || 0) * 100)}%`;
  $("cancel-network-literature").classList.toggle("hidden", !state.networkLiteratureActive);
  updateLiteratureControls();
  $("literature-status").textContent = payload.status === "complete"
    ? `Completed the literature audit of ${formatInt(payload.hypothesis_count)} unique hypotheses.`
    : payload.error || payload.message || payload.status;
}

async function pollNetworkLiterature() {
  if (!state.jobId || !state.networkLiteratureResearchId) return;
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/network-literature-analysis/${state.networkLiteratureResearchId}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to read literature research progress");
    updateNetworkLiteratureProgress(payload);
    if (payload.status === "complete") {
      clearInterval(state.networkLiteraturePollTimer);
      state.networkLiteraturePollTimer = null;
      renderNetworkLiterature(payload.result);
    } else if (["failed", "cancelled"].includes(payload.status)) {
      clearInterval(state.networkLiteraturePollTimer);
      state.networkLiteraturePollTimer = null;
    }
  } catch (error) {
    clearInterval(state.networkLiteraturePollTimer);
    state.networkLiteraturePollTimer = null;
    state.networkLiteratureActive = false;
    $("literature-status").textContent = String(error?.message || error);
    updateLiteratureControls();
  }
}

async function researchPathwayNetwork() {
  if (!state.jobId) return;
  const scope = networkLiteratureScope();
  const button = $("research-pathway-network");
  const apiKey = $("literature-api-key").value.trim();
  const apiBaseUrl = $("literature-api-base-url").value.trim();
  button.disabled = true;
  state.networkLiteratureActive = true;
  $("literature-result").classList.add("hidden");
  $("literature-progress").classList.remove("hidden");
  $("literature-status").textContent = "Submitting the frozen path-network scope. The API key will not be written to disk.";
  try {
    const headers = { "Content-Type": "application/json" };
    if (apiKey) headers["X-OpenAI-API-Key"] = apiKey;
    const response = await fetch(`/api/jobs/${state.jobId}/network-literature-analysis`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        rank_limit: scope.paths,
        cell_type: $("literature-cell-type").value.trim(),
        signaling_purpose: $("literature-purpose").value.trim(),
        api_base_url: apiBaseUrl,
        model: $("literature-model").value.trim(),
        reasoning_effort: $("literature-reasoning").value,
        force_refresh: $("literature-force-refresh").checked,
      }),
    });
    const payload = await response.json();
    if (!response.ok && !(response.status === 409 && payload.active_research)) {
      throw new Error(payload.error || "Unable to start the network literature audit");
    }
    const research = payload.active_research || payload;
    state.networkLiteratureResearchId = research.research_id;
    if (apiKey) $("literature-api-key").value = "";
    updateNetworkLiteratureProgress(research);
    state.networkLiteraturePollTimer = setInterval(pollNetworkLiterature, 1500);
    pollNetworkLiterature();
  } catch (error) {
    state.networkLiteratureActive = false;
    $("literature-status").textContent = String(error?.message || error);
    updateLiteratureControls();
  }
}

async function cancelNetworkLiterature() {
  if (!state.jobId || !state.networkLiteratureResearchId) return;
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/network-literature-analysis/${state.networkLiteratureResearchId}/cancel`, { method: "POST" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to cancel literature research");
    updateNetworkLiteratureProgress(payload);
  } catch (error) {
    $("literature-status").textContent = String(error?.message || error);
  }
}

function renderEvidenceInspection(payload) {
  const result = $("evidence-inspector-result");
  const body = $("evidence-ledger-body");
  body.innerHTML = "";
  const hypothesis = payload.kind === "node"
    ? payload.symbol
    : `${payload.node_a} — ${payload.node_b}`;
  $("evidence-summary-hypothesis").textContent = hypothesis;
  state.currentEvidenceInspection = JSON.parse(JSON.stringify(payload));
  $("evidence-summary-prior").textContent = formatProbability(payload.prior_probability);
  // Preserve near-boundary probabilities here: 0.99969 must not look like
  // mathematical certainty merely because the compact chart formatter uses
  // three decimal places elsewhere in the interface.
  $("evidence-summary-posterior").textContent = formatEvidenceNumber(payload.stored_posterior_probability);
  const selected = payload.kind === "node"
    ? payload.selected_in_graph
    : payload.supported_above_output_cutoff;
  $("evidence-summary-decision").textContent = selected ? "Included" : "Below cutoff";
  const difference = Number(payload.reconciliation_absolute_difference || 0);
  const reconciled = difference <= 1e-7;
  $("evidence-reconciliation").classList.toggle("warning", !reconciled);
  $("evidence-reconciliation").textContent = reconciled
    ? `Arithmetic check passed: stored ${formatEvidenceNumber(payload.stored_posterior_probability)}; reconstructed ${formatEvidenceNumber(payload.reconstructed_posterior_probability)} from the displayed contributions.`
    : `Arithmetic warning: stored ${formatEvidenceNumber(payload.stored_posterior_probability)}; reconstructed ${formatEvidenceNumber(payload.reconstructed_posterior_probability)} (absolute difference ${formatEvidenceNumber(difference)}).`;

  let initialDetail = null;
  payload.streams.forEach((stream) => {
    const row = document.createElement("tr");
    row.className = `evidence-row evidence-${stream.status}`;
    row.tabIndex = 0;
    const values = [
      stream.label,
      stream.status,
      evidenceScopeText(stream, payload.kind),
      formatEvidenceNumber(stream.applied_bayes_factor),
      formatEvidenceNumber(stream.weight),
      formatEvidenceNumber(stream.weighted_log2_odds_contribution),
      evidencePercentileText(stream.distribution_position),
    ];
    values.forEach((value, index) => {
      const cell = document.createElement("td");
      if (index === 1) {
        const badge = document.createElement("span");
        badge.className = `evidence-status evidence-status-${stream.status}`;
        badge.textContent = value;
        cell.appendChild(badge);
      } else {
        cell.textContent = value;
      }
      row.appendChild(cell);
    });
    const showDetail = () => {
      const parts = [stream.note];
      if (stream.description) parts.push(stream.description);
      if (stream.raw_value !== null && stream.raw_value !== undefined) {
        parts.push(`${stream.raw_value_label || "Raw value"}: ${formatEvidenceNumber(stream.raw_value)}.`);
      }
      if (stream.source_factor !== null && stream.source_factor !== undefined) {
        parts.push(`Source factor: ${formatEvidenceNumber(stream.source_factor)}.`);
      }
      if (stream.normalization_reference) {
        const scale = stream.tq_multiplier === null || stream.tq_multiplier === undefined
          ? ""
          : `; ${stream.normalization_control || "scale"} ${formatEvidenceNumber(stream.tq_multiplier)}`;
        parts.push(`Reference: ${stream.normalization_reference}${scale}.`);
      }
      if (stream.dependence_group) parts.push(`Shared-source group: ${stream.dependence_group}.`);
      $("evidence-stream-detail").textContent = `${stream.label}: ${parts.filter(Boolean).join(" ")}`;
      renderEvidenceFactorDistribution(stream);
      renderDatabaseTrace(stream.provenance_trace);
      body.querySelectorAll("tr").forEach((candidate) => candidate.classList.toggle("selected", candidate === row));
    };
    if (!initialDetail && stream.enabled) initialDetail = showDetail;
    row.addEventListener("click", showDetail);
    row.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        showDetail();
      }
    });
    body.appendChild(row);
  });
  $("evidence-stream-detail").textContent = "Select a stream row for its normalization, model weight, and score-distribution position.";
  $("evidence-inspector-message").textContent = `${payload.streams.filter((stream) => stream.enabled).length} enabled streams evaluated for ${hypothesis}.`;
  result.classList.remove("hidden");
  renderSelectedLiterature(payload);
  if (initialDetail) initialDetail();
  else renderDatabaseTrace(null);
}

function rememberEvidenceInspection(payload) {
  const key = payload.kind === "node"
    ? `node:${String(payload.symbol || "").toLowerCase()}`
    : `edge:${[payload.node_a, payload.node_b].map((value) => String(value || "").toLowerCase()).sort().join("|")}`;
  const snapshot = JSON.parse(JSON.stringify(payload));
  const existing = state.inspectionHistory.findIndex((item) => item.key === key);
  if (existing >= 0) state.inspectionHistory.splice(existing, 1);
  state.inspectionHistory.push({ key, inspected_at: new Date().toISOString(), ...snapshot });
  if (state.inspectionHistory.length > 100) state.inspectionHistory.shift();
}

async function inspectEvidence(kind) {
  if (!state.jobId) return;
  const form = kind === "node" ? $("node-evidence-form") : $("edge-evidence-form");
  const button = form.querySelector("button[type='submit']");
  const params = new URLSearchParams();
  if (kind === "node") {
    params.set("symbol", $("node-evidence-symbol").value.trim());
  } else {
    params.set("node_a", $("edge-evidence-node-a").value.trim());
    params.set("node_b", $("edge-evidence-node-b").value.trim());
  }
  button.disabled = true;
  button.textContent = "Reading…";
  $("evidence-inspector-message").textContent = "Reconstructing this hypothesis from the completed run…";
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/evidence/${kind}?${params.toString()}`, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Unable to inspect evidence");
    rememberEvidenceInspection(payload);
    renderEvidenceInspection(payload);
  } catch (error) {
    $("evidence-inspector-result").classList.add("hidden");
    $("evidence-inspector-message").textContent = String(error?.message || error);
  } finally {
    button.disabled = false;
    button.textContent = "Inspect";
  }
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

function networkHash(text) {
  let hash = 2166136261;
  for (const character of String(text)) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function networkEvidenceClass(probability, available = true) {
  if (!available || probability === null || probability === undefined) return "unscored";
  const value = Number(probability);
  if (value > 0.500000000001) return "supporting";
  if (value < 0.499999999999) return "refuting";
  return "neutral";
}

function observedProbabilityScale(values) {
  const probabilities = values
    .map(Number)
    .filter((value) => Number.isFinite(value))
    .map((value) => Math.max(0, Math.min(1, value)));
  if (!probabilities.length) return null;
  const minimum = Math.min(...probabilities);
  const maximum = Math.max(...probabilities);
  return {
    minimum,
    maximum,
    span: maximum - minimum,
    count: probabilities.length,
  };
}

function networkEvidenceStrength(probability, available = true, observedScale = null) {
  if (!available || probability === null || probability === undefined) return 0.22;
  const value = Math.max(0, Math.min(1, Number(probability)));
  if (!observedScale) return value;
  const { minimum, maximum, span } = observedScale;
  if (span <= 1e-15) return 0.5;
  return Math.max(0, Math.min(1, (value - minimum) / (maximum - minimum)));
}

function networkEvidenceColor(probability, available = true, observedScale = null, palette = "edge") {
  if (!available || probability === null || probability === undefined) return "var(--orange)";
  const strength = networkEvidenceStrength(probability, true, observedScale);
  const darkPercent = Math.round(100 * strength);
  return `color-mix(in srgb, var(--network-${palette}-low) ${100 - darkPercent}%, var(--network-${palette}-high) ${darkPercent}%)`;
}

function networkScaleGradient(scale, palette) {
  if (!scale) return "var(--network-neutral)";
  if (scale.span <= 1e-15) return networkEvidenceColor(scale.minimum, true, scale, palette);
  return `linear-gradient(to top, var(--network-${palette}-low), var(--network-${palette}-high))`;
}

function updatePathNetworkColorScale(kind, scale) {
  const gradient = $(`path-network-${kind}-color-gradient`);
  const maximum = $(`path-network-${kind}-color-maximum`);
  const midpoint = $(`path-network-${kind}-color-midpoint`);
  const minimum = $(`path-network-${kind}-color-minimum`);
  if (!gradient || !maximum || !midpoint || !minimum) return;
  gradient.style.background = networkScaleGradient(scale, kind);
  if (!scale) {
    maximum.textContent = midpoint.textContent = minimum.textContent = "—";
    return;
  }
  maximum.textContent = formatEvidenceNumber(scale.maximum);
  midpoint.textContent = formatEvidenceNumber((scale.minimum + scale.maximum) / 2);
  minimum.textContent = formatEvidenceNumber(scale.minimum);
}

function pathRankText(ranks) {
  const values = (ranks || []).map(Number).sort((left, right) => left - right);
  if (!values.length) return "none";
  const shown = values.slice(0, 8).join(", ");
  return values.length > 8 ? `${shown}, +${values.length - 8} more` : shown;
}

function visiblePathNetwork(network, rankLimit) {
  const nodes = (network.nodes || [])
    .filter((node) => (node.path_ranks || []).some((rank) => Number(rank) <= rankLimit))
    .map((node) => {
      const positions = (node.path_positions || []).filter((item) => Number(item.rank) <= rankLimit);
      const pathPosition = positions.length
        ? positions.reduce((total, item) => total + Number(item.position), 0) / positions.length
        : Number(node.mean_path_position || 0.5);
      const pathRanks = (node.path_ranks || []).filter((rank) => Number(rank) <= rankLimit);
      return { ...node, pathPosition, visiblePathRanks: pathRanks };
    });
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = (network.edges || [])
    .filter((edge) => (edge.path_ranks || []).some((rank) => Number(rank) <= rankLimit))
    .filter((edge) => nodeIds.has(edge.node_a) && nodeIds.has(edge.node_b))
    .map((edge) => ({
      ...edge,
      visiblePathRanks: (edge.path_ranks || []).filter((rank) => Number(rank) <= rankLimit),
    }));
  return { nodes, edges };
}

function assignPathTierJitter(nodes, marginX, usableWidth, marginY, usableHeight, seed) {
  const tierGroups = new Map();
  const maximumJitter = Math.min(112, Math.max(48, usableWidth * 0.12));
  nodes.forEach((node) => {
    const pathPosition = Math.max(0, Math.min(1, Number(node.pathPosition)));
    node.tierAnchorX = marginX + pathPosition * usableWidth;
    node.targetX = node.tierAnchorX;
    node.tierJitterX = 0;
    // Path positions are normalized fractions. Rounding only suppresses
    // floating-point noise; it does not merge visibly distinct tiers.
    const tierKey = Math.round(pathPosition * 1000);
    if (!tierGroups.has(tierKey)) tierGroups.set(tierKey, []);
    tierGroups.get(tierKey).push(node);
  });
  tierGroups.forEach((tierNodes, tierKey) => {
    const ordered = [...tierNodes].sort((left, right) => {
      const leftHash = networkHash(`${left.id}:${tierKey}:${seed}:tier-jitter`);
      const rightHash = networkHash(`${right.id}:${tierKey}:${seed}:tier-jitter`);
      return leftHash - rightHash || String(left.id).localeCompare(String(right.id));
    });
    const spacing = Math.min(46, (2 * maximumJitter) / Math.max(1, ordered.length - 1));
    ordered.forEach((node, index) => {
      if (node.is_start || node.is_target) return;
      const offset = (index - (ordered.length - 1) / 2) * spacing;
      node.tierJitterX = offset;
      node.targetX = Math.max(
        marginX,
        Math.min(marginX + usableWidth, node.tierAnchorX + offset),
      );
      // Allocate deterministic vertical lanes within every tier.  The earlier
      // layout used unrelated random Y targets, which could still place
      // several same-tier nodes—and their edges—on top of one another.
      const laneCount = ordered.length;
      const lane = laneCount === 1 ? 0.5 : (index + 0.5) / laneCount;
      const microJitter = ((networkHash(`${node.id}:${tierKey}:${seed}:lane`) % 17) - 8) * 0.7;
      node.targetY = Math.max(
        marginY,
        Math.min(marginY + usableHeight, marginY + lane * usableHeight + microJitter),
      );
    });
  });
}

function layoutPathNetwork(nodes, edges, width, height, seed) {
  const marginX = Math.min(62, Math.max(46, width * 0.08));
  const marginY = 38;
  const usableWidth = Math.max(width - 2 * marginX, 120);
  const usableHeight = Math.max(height - 2 * marginY, 180);
  nodes.forEach((node) => {
    node.radius = node.is_start || node.is_target ? 11 : 10;
    const hash = networkHash(`${node.id}:${seed}`);
    node.targetY = marginY + (((hash >>> 10) % 1000) / 999) * usableHeight;
    node.fixed = Boolean(node.is_start || node.is_target);
  });
  assignPathTierJitter(nodes, marginX, usableWidth, marginY, usableHeight, seed);
  nodes.forEach((node) => {
    node.x = node.targetX;
    node.y = node.targetY;
    if (node.is_start) {
      node.x = marginX;
      node.targetX = node.x;
      node.y = height / 2;
      node.targetY = node.y;
    } else if (node.is_target) {
      node.x = width - marginX;
      node.targetX = node.x;
      node.y = height / 2;
      node.targetY = node.y;
    }
  });
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const activeEdges = edges
    .map((edge) => ({ edge, source: byId.get(edge.source), target: byId.get(edge.target) }))
    .filter((item) => item.source && item.target);

  const iterations = nodes.length > 120 ? 180 : 250;
  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const forces = new Map(nodes.map((node) => [node.id, { x: 0, y: 0 }]));
    nodes.forEach((node) => {
      const force = forces.get(node.id);
      force.x += (node.targetX - node.x) * 0.11;
      // Retain deterministic vertical lanes while the springs merge shared
      // hubs. This prevents dense top-path unions from collapsing into a
      // single horizontal knot.
      force.y += (node.targetY - node.y) * 0.065;
    });
    for (let leftIndex = 0; leftIndex < nodes.length; leftIndex += 1) {
      for (let rightIndex = leftIndex + 1; rightIndex < nodes.length; rightIndex += 1) {
        const left = nodes[leftIndex];
        const right = nodes[rightIndex];
        let dx = right.x - left.x;
        let dy = right.y - left.y;
        let distance = Math.hypot(dx, dy);
        if (distance < 0.01) {
          dx = ((networkHash(`${left.id}:${right.id}`) % 17) - 8) / 8;
          dy = 1;
          distance = Math.hypot(dx, dy);
        }
        const desired = left.radius + right.radius + 48;
        if (distance < desired) {
          const push = (desired - distance) * 0.055;
          const unitX = dx / distance;
          const unitY = dy / distance;
          forces.get(left.id).x -= unitX * push;
          forces.get(left.id).y -= unitY * push;
          forces.get(right.id).x += unitX * push;
          forces.get(right.id).y += unitY * push;
        }
      }
    }
    activeEdges.forEach(({ source, target }) => {
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distance = Math.max(Math.hypot(dx, dy), 0.01);
      const layerDistance = Math.abs(target.targetX - source.targetX);
      const desired = Math.max(48, Math.min(105, 45 + layerDistance * 0.38));
      const sameTier = Math.abs(Number(source.pathPosition) - Number(target.pathPosition)) < 0.001;
      const pull = (distance - desired) * (sameTier ? 0.002 : 0.013);
      const unitX = dx / distance;
      const unitY = dy / distance;
      forces.get(source.id).x += unitX * pull;
      forces.get(source.id).y += unitY * pull;
      forces.get(target.id).x -= unitX * pull;
      forces.get(target.id).y -= unitY * pull;
    });
    nodes.forEach((node) => {
      if (node.fixed) return;
      const force = forces.get(node.id);
      node.x += Math.max(-4, Math.min(4, force.x));
      node.y += Math.max(-4, Math.min(4, force.y));
      node.x = Math.max(marginX, Math.min(width - marginX, node.x));
      node.y = Math.max(marginY, Math.min(height - marginY, node.y));
    });
  }
  return byId;
}

function setPathNetworkSelection(detail, nodeIds, edgeIds = []) {
  const svg = $("path-network-svg");
  const selectedNodes = new Set(nodeIds);
  const selectedEdges = new Set(edgeIds);
  svg.querySelectorAll("[data-network-node]").forEach((element) => {
    element.classList.toggle("network-dimmed", !selectedNodes.has(element.dataset.networkNode));
    element.classList.toggle("network-highlighted", selectedNodes.has(element.dataset.networkNode));
  });
  svg.querySelectorAll("[data-network-edge]").forEach((element) => {
    element.classList.toggle("network-dimmed", !selectedEdges.has(element.dataset.networkEdge));
    element.classList.toggle("network-highlighted", selectedEdges.has(element.dataset.networkEdge));
  });
  $("path-network-detail").textContent = detail;
}

function clearRankedPathSelection() {
  state.selectedPathRank = null;
  $("paths-body").querySelectorAll("tr").forEach((row) => row.classList.remove("selected"));
}

function selectRankedPath(path, row) {
  const network = state.pathNetwork;
  const rank = Number(path.rank);
  if (!network || !Number.isFinite(rank)) return;
  state.selectedPathRank = rank;
  const limitSelect = $("path-network-limit");
  const availableLimits = Array.from(limitSelect.options).map((option) => Number(option.value));
  const selectedLimit = availableLimits.find((value) => value >= rank) || Math.max(...availableLimits, rank);
  if (selectedLimit !== state.networkRankLimit) {
    state.networkRankLimit = selectedLimit;
    limitSelect.value = String(selectedLimit);
    drawPathNetwork();
  }
  const nodes = (network.nodes || []).filter((node) => (node.path_ranks || []).map(Number).includes(rank));
  const edges = (network.edges || []).filter((edge) => (edge.path_ranks || []).map(Number).includes(rank));
  setPathNetworkSelection(
    `Path ${rank}: ${path.path_symbols} · primary score ${formatScore(path.primary_path_score ?? path.geometric_mean_edge_probability ?? path.path_probability_product)}. Its lowest-probability edge is loaded below; select any highlighted node or edge to inspect another component.`,
    nodes.map((node) => node.id),
    edges.map((edge) => edge.id),
  );
  $("paths-body").querySelectorAll("tr").forEach((candidate) => candidate.classList.toggle("selected", candidate === row));
  const bottleneck = edges.reduce(
    (lowest, edge) => (!lowest || Number(edge.edge_probability) < Number(lowest.edge_probability) ? edge : lowest),
    null,
  );
  if (bottleneck) {
    $("edge-evidence-node-a").value = bottleneck.node_a;
    $("edge-evidence-node-b").value = bottleneck.node_b;
    inspectEvidence("edge");
  }
}

function clearPathNetworkSelection(nodeCount, edgeCount, pathCount) {
  const svg = $("path-network-svg");
  svg.querySelectorAll(".network-dimmed, .network-highlighted").forEach((element) => {
    element.classList.remove("network-dimmed", "network-highlighted");
  });
  $("path-network-detail").textContent = `${formatInt(nodeCount)} nodes and ${formatInt(edgeCount)} merged relationships from the top ${formatInt(pathCount)} path${pathCount === 1 ? "" : "s"}. Select a mark for exact probabilities, roles, and contributing path ranks.`;
}

function drawPathNetwork() {
  const network = state.pathNetwork;
  const svg = $("path-network-svg");
  if (!network || !(network.nodes || []).length) return;
  const rankLimit = Math.min(state.networkRankLimit, Number(network.visualized_path_count || 0));
  const visible = visiblePathNetwork(network, rankLimit);
  const nodeScale = observedProbabilityScale(
    visible.nodes
      .filter((node) => node.posterior_available)
      .map((node) => node.posterior_probability),
  );
  const edgeScale = observedProbabilityScale(
    visible.edges.map((edge) => edge.edge_probability),
  );
  updatePathNetworkColorScale("node", nodeScale);
  updatePathNetworkColorScale("edge", edgeScale);
  const width = Math.max(320, Math.round(svg.parentElement.getBoundingClientRect().width || 640));
  const height = Math.max(520, Math.min(860, 390 + visible.nodes.length * 6.5));
  svg.innerHTML = "";
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("height", String(height));

  const defs = svgElement("defs");
  svg.appendChild(defs);

  const positions = layoutPathNetwork(
    visible.nodes,
    visible.edges,
    width,
    height,
    state.networkLayoutSeed,
  );
  const edgeLayer = svgElement("g", { class: "network-edge-layer" });
  const nodeLayer = svgElement("g", { class: "network-node-layer" });
  svg.append(edgeLayer, nodeLayer);

  visible.edges.forEach((edge) => {
    const source = positions.get(edge.source);
    const target = positions.get(edge.target);
    if (!source || !target) return;
    const dx = target.x - source.x;
    const dy = target.y - source.y;
    const distance = Math.max(Math.hypot(dx, dy), 0.01);
    const directed = edge.directionality === "uniquely_directed";
    const sourceOffset = source.radius + 1;
    const targetOffset = target.radius + (directed ? 6 : 1);
    const x1 = source.x + (dx / distance) * sourceOffset;
    const y1 = source.y + (dy / distance) * sourceOffset;
    const x2 = target.x - (dx / distance) * targetOffset;
    const y2 = target.y - (dy / distance) * targetOffset;
    const evidenceClass = networkEvidenceClass(edge.edge_probability);
    const evidenceColor = networkEvidenceColor(edge.edge_probability, true, edgeScale, "edge");
    const strokeWidth = 2.6;
    const opacity = 0.94;
    let markerId = null;
    if (directed) {
      markerId = `path-network-arrow-${networkHash(edge.id)}`;
      const marker = svgElement("marker", {
        id: markerId,
        viewBox: "0 0 8 8",
        refX: 7,
        refY: 4,
        markerWidth: 9,
        markerHeight: 9,
        markerUnits: "userSpaceOnUse",
        orient: "auto-start-reverse",
      });
      marker.appendChild(svgElement("path", { d: "M 0 0 L 8 4 L 0 8 z", style: `fill:${evidenceColor}` }));
      defs.appendChild(marker);
    }
    const edgeHash = networkHash(`${edge.id}:${state.networkLayoutSeed}:edge-route`);
    const sameTier = Math.abs(Number(source.pathPosition) - Number(target.pathPosition)) < 0.001;
    const bendDirection = edgeHash % 2 ? 1 : -1;
    const bendMagnitude = sameTier ? 42 + (edgeHash % 39) : 10 + (edgeHash % 17);
    const controlX = (x1 + x2) / 2 - (dy / distance) * bendMagnitude * bendDirection;
    const controlY = (y1 + y2) / 2 + (dx / distance) * bendMagnitude * bendDirection;
    const pathData = `M ${x1.toFixed(2)} ${y1.toFixed(2)} Q ${controlX.toFixed(2)} ${controlY.toFixed(2)} ${x2.toFixed(2)} ${y2.toFixed(2)}`;
    const visiblePath = svgElement("path", {
      d: pathData,
      class: `network-edge ${evidenceClass}`,
      "data-network-edge": edge.id,
      "data-node-a": edge.node_a,
      "data-node-b": edge.node_b,
      "stroke-width": strokeWidth.toFixed(2),
      style: `stroke:${evidenceColor}`,
      opacity: opacity.toFixed(3),
      tabindex: 0,
      role: "button",
      "aria-label": `${edge.node_a} to ${edge.node_b}, edge posterior ${formatEvidenceNumber(edge.edge_probability)}`,
      ...(directed ? { "marker-end": `url(#${markerId})` } : {}),
    });
    const directionText = directed
      ? `constrained ${edge.source} → ${edge.target}`
      : edge.directionality === "undirected"
        ? "directionality disabled"
        : "direction unresolved; both traversals allowed";
    const detail = `${edge.node_a} — ${edge.node_b} · edge posterior ${formatEvidenceNumber(edge.edge_probability)} · ${directionText} · shown in path ranks ${pathRankText(edge.visiblePathRanks)}.`;
    visiblePath.appendChild(svgElement("title", {}, detail));
    const selectEdge = (event) => {
      event.stopPropagation();
      clearRankedPathSelection();
      setPathNetworkSelection(detail, [edge.node_a, edge.node_b], [edge.id]);
      $("edge-evidence-node-a").value = edge.node_a;
      $("edge-evidence-node-b").value = edge.node_b;
      inspectEvidence("edge");
    };
    visiblePath.addEventListener("click", selectEdge);
    visiblePath.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectEdge(event);
      }
    });
    const hitPath = svgElement("path", {
      d: pathData,
      class: "network-edge-hit",
      "data-network-edge": edge.id,
      "stroke-width": Math.max(14, strokeWidth + 8),
    });
    hitPath.addEventListener("click", selectEdge);
    edgeLayer.append(visiblePath, hitPath);
  });

  visible.nodes.forEach((node) => {
    const group = svgElement("g", {
      class: `network-node-group${node.is_start ? " start" : ""}${node.is_target ? " target" : ""}`,
      transform: `translate(${node.x.toFixed(2)} ${node.y.toFixed(2)})`,
      "data-network-node": node.id,
      tabindex: 0,
      role: "button",
      "aria-label": `${node.label}, node posterior ${formatEvidenceNumber(node.posterior_probability)}`,
    });
    group.appendChild(svgElement("circle", { r: Math.max(22, node.radius + 7), class: "network-node-hit" }));
    const evidenceClass = networkEvidenceClass(node.posterior_probability, node.posterior_available);
    const evidenceColor = networkEvidenceColor(node.posterior_probability, node.posterior_available, nodeScale, "node");
    const circle = svgElement("circle", {
      r: node.radius.toFixed(2),
      class: `network-node ${evidenceClass}`,
      style: `fill:${evidenceColor}`,
      "fill-opacity": node.posterior_available ? "0.96" : "0.6",
    });
    group.appendChild(circle);
    if (node.is_start || node.is_target) {
      group.appendChild(svgElement("circle", { r: (node.radius + 4).toFixed(2), class: "network-node-ring" }));
    }
    const labelAttributes = node.is_start
      ? { x: node.radius + 6, y: 3, "text-anchor": "start" }
      : node.is_target
        ? { x: -node.radius - 6, y: 3, "text-anchor": "end" }
        : { x: 0, y: -node.radius - 5, "text-anchor": "middle" };
    group.appendChild(svgElement("text", { ...labelAttributes, class: "network-node-label" }, node.label));
    const posteriorText = node.posterior_available
      ? `node posterior ${formatEvidenceNumber(node.posterior_probability)}`
      : "no Bayesian node posterior (curated messenger or external endpoint)";
    const roles = String(node.classes || "unclassified").replaceAll(";", ", ");
    const detail = `${node.label}${node.name ? ` — ${node.name}` : ""} · ${posteriorText} · roles: ${roles} · shown in path ranks ${pathRankText(node.visiblePathRanks)}.`;
    group.appendChild(svgElement("title", {}, detail));
    const selectNode = (event) => {
      event.stopPropagation();
      clearRankedPathSelection();
      const incident = visible.edges.filter((edge) => edge.node_a === node.id || edge.node_b === node.id);
      const neighbors = new Set([node.id]);
      incident.forEach((edge) => {
        neighbors.add(edge.node_a);
        neighbors.add(edge.node_b);
      });
      setPathNetworkSelection(detail, [...neighbors], incident.map((edge) => edge.id));
      if (node.posterior_available) {
        $("node-evidence-symbol").value = node.id;
        inspectEvidence("node");
      } else {
        $("evidence-inspector-result").classList.add("hidden");
        $("evidence-inspector-message").textContent = `${node.label} is a curated messenger or external endpoint and has no Bayesian node-selection ledger.`;
      }
    };
    group.addEventListener("click", selectNode);
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(event);
      }
    });
    nodeLayer.appendChild(group);
  });
  svg.addEventListener("click", () => {
    clearRankedPathSelection();
    clearPathNetworkSelection(visible.nodes.length, visible.edges.length, rankLimit);
  });
  clearPathNetworkSelection(visible.nodes.length, visible.edges.length, rankLimit);
}

function renderPathNetwork(network) {
  state.pathNetwork = network;
  const section = $("path-network-section");
  const available = Number(network?.visualized_path_count || 0);
  section.classList.toggle("hidden", !available || !(network?.nodes || []).length);
  if (!available || !(network?.nodes || []).length) return;
  const limitSelect = $("path-network-limit");
  const choices = [...new Set([Math.min(5, available), Math.min(10, available), Math.min(25, available), Math.min(50, available), available])]
    .filter((value) => value > 0)
    .sort((left, right) => left - right);
  limitSelect.innerHTML = "";
  choices.forEach((value) => {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = `Top ${value}`;
    limitSelect.appendChild(option);
  });
  state.networkRankLimit = Math.min(50, available);
  limitSelect.value = String(state.networkRankLimit);
  state.networkLayoutSeed = 0;
  drawPathNetwork();
  updateLiteratureControls();
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
  weight.title = "Any finite value from 0 through 10 is allowed; decimals are not restricted to tenths.";
  weight.innerHTML = `<span>Weight</span><input class="stream-weight stream-setting" type="number" min="0" max="10" step="any" value="${current.weight}" ${current.enabled ? "" : "disabled"} aria-label="${stream.label} weight" />`;
  card.append(toggle, copy, weight);
  if (stream.normalization.user_control !== false) {
    card.classList.add("with-preferred-tq");
    const normalization = document.createElement("label");
    normalization.className = "normalization-field";
    normalization.title = stream.normalization.help;
    normalization.innerHTML = `<span>${stream.normalization.control_label}</span><input class="stream-tq stream-setting" type="number" min="0.05" max="20" step="any" value="${current.tq_multiplier}" ${current.enabled ? "" : "disabled"} aria-label="${stream.label} ${stream.normalization.control_label}" />`;
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
    field.title = `${parameter.help} Any decimal within ${parameter.minimum}–${parameter.maximum} is accepted; ${parameter.step} is a suggested adjustment increment, not a validity grid.`;
    field.innerHTML = `<span>${parameter.label}</span><input class="stream-parameter stream-setting" data-parameter="${parameter.id}" type="number" min="${parameter.minimum}" max="${parameter.maximum}" step="any" value="${current.parameters[parameter.id]}" ${current.enabled ? "" : "disabled"} aria-label="${stream.label} ${parameter.label}" />`;
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
  const graphStatistics = state.defaults.graph_statistics;
  $("graph-statistics-enabled").checked = graphStatistics.enabled;
  $("graph-statistics-cutoff").value = graphStatistics.edge_probability_cutoff;
  const selectedGraphStatistics = new Set(graphStatistics.metrics || []);
  document.querySelectorAll(".graph-statistic").forEach((input) => {
    input.checked = selectedGraphStatistics.has(input.value);
  });
  const path = state.defaults.path;
  $("path-enabled").checked = path.enabled;
  $("path-start").value = path.start;
  $("path-target").value = path.target;
  $("path-top-k").value = path.top_k;
  $("path-max-hops").value = path.max_hops;
  $("path-cutoff").value = path.minimum_edge_probability;
  $("path-include-node-probabilities").checked = path.include_node_probabilities;
  $("ontology-directionality").checked = path.ontology_directionality_enabled;
  $("omnipath-directionality").checked = path.omnipath_directionality_enabled;
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
  setGraphStatisticsControls(graphStatistics.enabled);
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
    graph_statistics: {
      enabled: $("graph-statistics-enabled").checked,
      edge_probability_cutoff: Number($("graph-statistics-cutoff").value),
      metrics: Array.from(document.querySelectorAll(".graph-statistic:checked")).map(
        (input) => input.value
      ),
    },
    path: {
      enabled: $("path-enabled").checked,
      start: $("path-start").value.trim(),
      target: $("path-target").value.trim(),
      top_k: Number($("path-top-k").value),
      max_hops: Number($("path-max-hops").value),
      minimum_edge_probability: Number($("path-cutoff").value),
      include_node_probabilities: $("path-include-node-probabilities").checked,
      ontology_directionality_enabled: $("ontology-directionality").checked,
      omnipath_directionality_enabled: $("omnipath-directionality").checked,
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

function setGraphStatisticsControls(enabled) {
  $("graph-statistics-controls").querySelectorAll("input").forEach((control) => {
    control.disabled = !enabled;
  });
  $("graph-statistics-controls").classList.toggle("inactive", !enabled);
}

function setPathControls(enabled) {
  $("path-controls").querySelectorAll("input, button").forEach((control) => { control.disabled = !enabled; });
  $("path-controls").style.opacity = enabled ? "1" : ".45";
  setDirectionalityControls(enabled);
  setOntologyControls(enabled);
  setTemporalControls(enabled);
}

function setDirectionalityControls(pathEnabled) {
  $("omnipath-directionality").disabled =
    !pathEnabled || !$("ontology-directionality").checked;
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
  // A TypeError can also be raised by result rendering.  Classifying every
  // TypeError as a lost server connection hides the actionable JavaScript
  // message and makes an intact backend look offline.  Real fetch failures
  // already carry one of the browser-specific network phrases below.
  const disconnected = /failed to fetch|networkerror|load failed|network request failed/i.test(rawMessage);
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

function calibrationStreamLabel(stage, streamId) {
  const definitions = state.registry?.[`${stage}_streams`] || [];
  return definitions.find((stream) => stream.id === streamId)?.label || streamId.replaceAll("_", " ");
}

function renderCalibrationParameterStage(stage, fit) {
  const article = $(`${stage}-calibration-parameters`);
  const body = $(`${stage}-calibration-parameters-body`);
  body.innerHTML = "";
  if (!fit?.parameters?.length) {
    article.classList.add("hidden");
    return false;
  }
  article.classList.remove("hidden");
  const grouped = new Map();
  fit.parameters.forEach((parameter) => {
    if (!grouped.has(parameter.stream_id)) grouped.set(parameter.stream_id, {});
    grouped.get(parameter.stream_id)[parameter.parameter] = parameter;
  });
  grouped.forEach((parameters, streamId) => {
    const weight = parameters.weight;
    const scale = parameters.tq_multiplier;
    const row = document.createElement("tr");
    const cells = [
      calibrationStreamLabel(stage, streamId),
      formatEvidenceNumber(weight?.current),
      formatEvidenceNumber(weight?.fitted),
      formatEvidenceNumber(scale?.current),
      formatEvidenceNumber(scale?.preferred),
      formatEvidenceNumber(scale?.fitted),
    ];
    cells.forEach((value, index) => {
      const cell = document.createElement(index === 0 ? "th" : "td");
      if (index === 0) cell.scope = "row";
      cell.textContent = value;
      if (index === 2 && weight) {
        const delta = Number(weight.fitted) - Number(weight.current);
        cell.className = delta > 1e-9 ? "calibration-increase" : delta < -1e-9 ? "calibration-decrease" : "calibration-unchanged";
        cell.title = `Allowed weight range ${formatEvidenceNumber(weight.lower_bound)}–${formatEvidenceNumber(weight.upper_bound)}; regularization preference ${formatEvidenceNumber(weight.preferred)}.`;
      }
      if (index === 5 && scale) {
        const delta = Number(scale.fitted) - Number(scale.current);
        cell.className = delta > 1e-9 ? "calibration-increase" : delta < -1e-9 ? "calibration-decrease" : "calibration-unchanged";
        cell.title = `Allowed multiplier range ${formatEvidenceNumber(scale.lower_bound)}–${formatEvidenceNumber(scale.upper_bound)}; optimization used ${scale.optimization_scale || "linear"} scale.`;
      }
      row.appendChild(cell);
    });
    body.appendChild(row);
  });
  return true;
}

function renderCalibrationParameters(calibration) {
  const section = $("calibration-parameters");
  if (!calibration?.enabled) {
    section.classList.add("hidden");
    return;
  }
  const nodeVisible = renderCalibrationParameterStage("node", calibration.node);
  const edgeVisible = renderCalibrationParameterStage("edge", calibration.edge);
  section.classList.toggle("hidden", !nodeVisible && !edgeVisible);
}

function renderFullGraphNodeRanking(statistics, metricId) {
  const body = $("full-graph-node-rankings");
  body.innerHTML = "";
  const rows = statistics?.top_nodes_by_metric?.[metricId] || [];
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="4" class="muted">No ranking is available for this statistic.</td></tr>';
    return;
  }
  rows.forEach((row, index) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${index + 1}</td><td>${escapeHtml(row.symbol)}</td><td>${escapeHtml(row.name || "—")}</td><td class="score">${formatEvidenceNumber(row.value)}</td>`;
    body.appendChild(tr);
  });
}

function renderMetricOverview(statistics, containerId) {
  const container = $(containerId);
  container.innerHTML = "";
  const available = statistics?.available_metrics || [];
  available.forEach((metric, metricIndex) => {
    const rows = (statistics?.top_nodes_by_metric?.[metric.id] || []).slice(0, 10);
    if (!rows.length) return;
    const details = document.createElement("details");
    details.className = "metric-overview-card";
    if (metricIndex < 4) details.open = true;
    details.innerHTML = `<summary>${escapeHtml(metric.label)}</summary><ol>${rows.map((row) => (
      `<li><strong>${escapeHtml(row.symbol)}</strong><span class="metric-value">${formatEvidenceNumber(row.value)}</span></li>`
    )).join("")}</ol>`;
    container.appendChild(details);
  });
}

function renderFullGraphStatistics(statistics) {
  const section = $("full-graph-statistics-result");
  if (!statistics?.summary) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  const summary = statistics.summary;
  $("full-graph-statistics-definition").textContent = `p(edge) > ${formatEvidenceNumber(summary.edge_probability_cutoff_exclusive)}`;
  const fields = [
    ["Nodes", summary.node_count],
    ["Edges", summary.edge_count],
    ["Density", summary.density],
    ["Components", summary.connected_component_count],
    ["Largest component", summary.largest_component_node_count],
    ["Isolates", summary.isolate_count],
    ["Mean degree", summary.average_degree],
    ["Median degree", summary.median_degree],
    ["Maximum degree", summary.maximum_degree],
    ["Mean posterior strength", summary.average_posterior_strength],
    ["Mean clustering", summary.average_clustering_coefficient],
    ["Weighted mean clustering", summary.weighted_average_clustering_coefficient],
    ["Transitivity", summary.transitivity],
    ["Degree assortativity", summary.degree_assortativity],
    ["Diameter (largest component)", summary.largest_component_diameter_unweighted],
    ["Mean path length (largest component)", summary.largest_component_average_shortest_path_length_unweighted],
    ["Communities", summary.community_count],
    ["Weighted modularity", summary.probability_weighted_modularity],
    ["Articulation points", summary.articulation_point_count],
  ].filter(([, value]) => value !== null && value !== undefined);
  $("full-graph-summary").innerHTML = fields.map(([label, value]) => (
    `<div><dt>${escapeHtml(label)}</dt><dd>${Number.isInteger(Number(value)) ? formatInt(value) : formatEvidenceNumber(value)}</dd></div>`
  )).join("");
  const select = $("full-graph-metric");
  select.innerHTML = "";
  (statistics.available_metrics || []).forEach((metric) => {
    const option = document.createElement("option");
    option.value = metric.id;
    option.textContent = metric.label;
    select.appendChild(option);
  });
  if (select.options.length) {
    const preferred = Array.from(select.options).find((option) => option.value === "degree");
    select.value = preferred ? preferred.value : select.options[0].value;
    renderFullGraphNodeRanking(statistics, select.value);
  } else {
    renderFullGraphNodeRanking(statistics, "");
  }
  select.onchange = () => renderFullGraphNodeRanking(statistics, select.value);
  renderMetricOverview(statistics, "full-graph-metric-panels");
  const approximations = [];
  if (summary.clustering_is_approximate) approximations.push(`clustering (${formatInt(summary.clustering_neighbor_pair_samples_per_node)} neighbor pairs/node)`);
  if (summary.betweenness_is_approximate) approximations.push(`betweenness (${formatInt(summary.betweenness_approximation_source_count)} sources)`);
  if (summary.distance_centrality_is_approximate) approximations.push(`closeness/harmonic (${formatInt(summary.distance_centrality_landmark_count)} landmarks across components)`);
  if (summary.shortest_path_is_approximate) approximations.push(`path length and diameter (${formatInt(summary.shortest_path_landmark_count)} landmarks; diameter is a lower bound)`);
  if (approximations.length) {
    $("full-graph-statistics-definition").textContent += ` · deterministic approximations: ${approximations.join(", ")}`;
  }
}

function renderFoundPathNodeRanking(statistics, metricId) {
  const body = $("found-path-node-rankings");
  body.innerHTML = "";
  const rows = statistics?.top_nodes_by_metric?.[metricId] || [];
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="4" class="muted">No returned-path ranking is available.</td></tr>';
    return;
  }
  rows.forEach((row, index) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${index + 1}</td><td>${escapeHtml(row.symbol)}</td><td>${escapeHtml(row.name || "—")}</td><td class="score">${formatEvidenceNumber(row.value)}</td>`;
    body.appendChild(tr);
  });
}

function renderFoundPathStatistics(statistics) {
  const section = $("found-path-statistics-result");
  if (!statistics?.summary) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  const summary = statistics.summary;
  $("found-path-statistics-definition").textContent = `${formatInt(summary.returned_path_count || 0)} returned paths · exact edge union`;
  const fields = [
    ["Returned paths", summary.returned_path_count],
    ["Unique nodes", summary.unique_node_count ?? summary.node_count],
    ["Unique undirected edges", summary.unique_undirected_path_edge_count ?? summary.edge_count],
    ["Directed transitions", summary.directed_transition_count],
    ["Bidirectional pairs", summary.bidirectional_pair_count],
    ["Density", summary.density],
    ["Components", summary.connected_component_count],
    ["Mean degree", summary.average_degree],
    ["Median degree", summary.median_degree],
    ["Maximum degree", summary.maximum_degree],
    ["Mean posterior strength", summary.average_posterior_strength],
    ["Mean clustering", summary.average_clustering_coefficient],
    ["Weighted mean clustering", summary.weighted_average_clustering_coefficient],
    ["Transitivity", summary.transitivity],
    ["Degree assortativity", summary.degree_assortativity],
    ["Diameter", summary.largest_component_diameter_unweighted],
    ["Mean path length", summary.largest_component_average_shortest_path_length_unweighted],
    ["Communities", summary.community_count],
    ["Articulation points", summary.articulation_point_count],
  ].filter(([, value]) => value !== null && value !== undefined);
  $("found-path-summary").innerHTML = fields.map(([label, value]) => (
    `<div><dt>${escapeHtml(label)}</dt><dd>${Number.isInteger(Number(value)) ? formatInt(value) : formatEvidenceNumber(value)}</dd></div>`
  )).join("");
  const select = $("found-path-metric");
  select.innerHTML = "";
  (statistics.available_metrics || []).forEach((metric) => {
    const option = document.createElement("option");
    option.value = metric.id;
    option.textContent = metric.label;
    select.appendChild(option);
  });
  if (select.options.length) {
    const preferred = Array.from(select.options).find(
      (option) => option.value === "path_participation_count"
    );
    select.value = preferred ? preferred.value : select.options[0].value;
    renderFoundPathNodeRanking(statistics, select.value);
  } else {
    renderFoundPathNodeRanking(statistics, "");
  }
  select.onchange = () => renderFoundPathNodeRanking(statistics, select.value);
  renderMetricOverview(statistics, "found-path-metric-panels");
}

function renderResult(job) {
  const preview = job.preview || { metrics: {}, top_paths: [], files: [], warnings: [] };
  $("evidence-inspector-result").classList.add("hidden");
  $("evidence-inspector-message").textContent = "Choose a hypothesis to see its update ledger.";
  $("metric-nodes").textContent = formatInt(preview.metrics.selected_nodes);
  $("metric-edges").textContent = formatInt(preview.metrics.supported_edges);
  $("metric-paths").textContent = formatInt(preview.metrics.ranked_paths);
  renderProbabilityDistribution("node", preview.probability_distributions?.nodes);
  renderProbabilityDistribution("edge", preview.probability_distributions?.edges);
  renderFullGraphStatistics(preview.full_graph_statistics);
  renderFoundPathStatistics(preview.found_path_union_statistics);
  renderPathNetwork(preview.path_network);
  const nodeAwarePathScore = Boolean(
    job.summary?.path_finding?.node_probabilities_included_in_primary_score
  );
  $("path-score-label").textContent = nodeAwarePathScore
    ? "Geometric-mean node + edge score"
    : "Geometric-mean edge score";
  const calibration = preview.calibration;
  $("calibration-result").classList.toggle("hidden", !calibration?.enabled);
  renderCalibrationParameters(calibration);
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
    const ontologyOnly = Number(directionality.uniquely_oriented_by_ontology_only_count || 0);
    const kinaseOnly = Number(directionality.uniquely_oriented_by_kinase_predictor_only_count || 0);
    const kinaseAny = Number(directionality.uniquely_oriented_with_kinase_predictor_count || 0);
    const omnipathOnly = Number(directionality.uniquely_oriented_by_omnipath_only_count || 0);
    const both = Number(directionality.uniquely_oriented_by_both_count || 0);
    const precedence = Number(directionality.ontology_precedence_over_opposing_omnipath_count || 0);
    const kinasePrecedence = Number(directionality.kinase_predictor_precedence_over_opposing_omnipath_count || 0);
    const noDirection = Number(directionality.unresolved_no_direction_evidence_count ?? directionality.unresolved_no_matching_rule_count ?? 0);
    const conflicts = Number(directionality.unresolved_conflicting_direction_count ?? directionality.unresolved_conflicting_rules_count ?? 0);
    $("directionality-result-detail").textContent = `${formatInt(directionality.retained_unique_edge_count)} edges above the output cutoff; ontology-led: ${formatInt(ontologyOnly)}, added by KinasePredictor: ${formatInt(kinaseOnly)}, added by OmniPath: ${formatInt(omnipathOnly)}. ${formatInt(kinaseAny)} oriented edges carried KinasePredictor direction evidence and ${formatInt(both)} had agreeing ontology/OmniPath evidence. ${formatInt(precedence)} opposing OmniPath calls retained the ontology restriction; ${formatInt(kinasePrecedence)} retained the KinasePredictor restriction. ${formatInt(noDirection)} had no direction evidence and ${formatInt(conflicts)} remained bidirectional.`;
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
      row.className = "path-row-selectable";
      row.tabIndex = 0;
      const temporalCells = temporalExecuted
        ? `<td>${formatInt(path.temporal_n_scored)}</td><td class="score">${formatProbability(path.temporal_kendall_tau_mean)} [${formatProbability(path.temporal_kendall_tau_low)}, ${formatProbability(path.temporal_kendall_tau_high)}]</td>`
        : "";
      row.innerHTML = `<td>${path.rank}</td><td class="route">${path.path_symbols}</td><td>${path.hop_count}</td><td class="score">${formatScore(path.primary_path_score ?? path.geometric_mean_edge_probability ?? path.path_probability_product)}</td>${temporalCells}`;
      row.addEventListener("click", () => selectRankedPath(path, row));
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectRankedPath(path, row);
        }
      });
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
  $("save-session-html").disabled = false;
  $("session-export-link").classList.add("hidden");
  $("session-export-status").textContent = "The export will preserve this run and every node/edge inspection opened during the current browser session.";
  showPanel("result-state");
}

async function saveEntireSession() {
  if (!state.jobId) return;
  const button = $("save-session-html");
  const status = $("session-export-status");
  button.disabled = true;
  button.textContent = "Building complete HTML…";
  status.textContent = "Freezing the visible network's interpretation ledgers, then compressing and embedding every run file. This may take a little while.";
  try {
    const response = await fetch(`/api/jobs/${state.jobId}/session-report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        inspection_history: state.inspectionHistory,
        literature_interpretations: state.literatureHistory.map((item) => ({ cache_key: item.cache_key })),
        network_literature_analyses: state.networkLiteratureHistory.map((item) => ({ cache_key: item.cache_key })),
        client_state: {
          exported_at: new Date().toISOString(),
          visible_path_rank_limit: state.networkRankLimit,
          selected_path_rank: state.selectedPathRank,
        },
      }),
    });
    const report = await response.json();
    if (!response.ok) throw new Error(report.error || "Unable to create the session report");
    const link = document.createElement("a");
    link.href = report.url;
    link.download = report.file;
    document.body.appendChild(link);
    link.click();
    link.remove();
    const repeatLink = $("session-export-link");
    repeatLink.href = report.url;
    repeatLink.download = report.file;
    repeatLink.textContent = `Download ${report.file} again`;
    repeatLink.classList.remove("hidden");
    const reportSize = `${(Number(report.report_bytes || 0) / (1024 * 1024)).toFixed(1)} MB`;
    status.textContent = `${report.file} is ready (${reportSize}; ${formatInt(report.embedded_file_count)} embedded artifacts, ${formatInt(report.interpretation_hypothesis_count)} evidence ledgers, and ${formatInt(report.literature_interpretation_count || 0)} literature reports).`;
  } catch (error) {
    status.textContent = String(error?.message || error);
  } finally {
    button.disabled = false;
    button.textContent = "Save entire session (.html)";
  }
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
  state.inspectionHistory = [];
  state.literatureHistory = [];
  state.networkLiteratureHistory = [];
  state.networkLiteratureResearchId = null;
  state.networkLiteratureActive = false;
  if (state.networkLiteraturePollTimer) clearInterval(state.networkLiteraturePollTimer);
  state.networkLiteraturePollTimer = null;
  state.currentEvidenceInspection = null;
  $("evidence-inspector-result").classList.add("hidden");
  $("database-trace").classList.add("hidden");
  $("literature-result").classList.add("hidden");
  updateLiteratureControls();
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
    state.literatureInterpreter = payload.literature_interpreter || { available: false };
    if (state.literatureInterpreter.default_model) {
      $("literature-model").value = state.literatureInterpreter.default_model;
    }
    if (state.literatureInterpreter.default_api_base_url) {
      $("literature-api-base-url").value = state.literatureInterpreter.default_api_base_url;
    }
    if (state.literatureInterpreter.default_reasoning_effort) {
      $("literature-reasoning").value = state.literatureInterpreter.default_reasoning_effort;
    }
    restoreLiteratureConnectionPreferences();
    updateLiteratureControls();
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
$("node-evidence-form").addEventListener("submit", (event) => {
  event.preventDefault();
  inspectEvidence("node");
});
$("edge-evidence-form").addEventListener("submit", (event) => {
  event.preventDefault();
  inspectEvidence("edge");
});
$("cancel-job-button").addEventListener("click", cancelRun);
$("save-session-html").addEventListener("click", saveEntireSession);
$("research-pathway-network").addEventListener("click", researchPathwayNetwork);
$("cancel-network-literature").addEventListener("click", cancelNetworkLiterature);
$("literature-api-key").addEventListener("input", updateLiteratureControls);
for (const fieldId of ["literature-api-base-url", "literature-model"]) {
  $(fieldId).addEventListener("input", () => {
    saveLiteratureConnectionPreferences();
    updateLiteratureControls();
  });
}
$("literature-key-visibility").addEventListener("click", () => {
  const input = $("literature-api-key");
  const reveal = input.type === "password";
  input.type = reveal ? "text" : "password";
  $("literature-key-visibility").textContent = reveal ? "Hide key" : "Show key";
  $("literature-key-visibility").setAttribute("aria-pressed", String(reveal));
});
$("reset-button").addEventListener("click", () => { populateControls(); showPanel("empty-state"); });
$("calibration-enabled").addEventListener("change", (event) => setCalibrationControls(event.target.checked));
$("graph-statistics-enabled").addEventListener("change", (event) => setGraphStatisticsControls(event.target.checked));
$("path-enabled").addEventListener("change", (event) => setPathControls(event.target.checked));
$("ontology-directionality").addEventListener("change", () => setDirectionalityControls($("path-enabled").checked));
$("path-network-limit").addEventListener("change", (event) => {
  state.networkRankLimit = Number(event.target.value);
  drawPathNetwork();
  updateLiteratureControls();
});
$("path-network-relayout").addEventListener("click", () => {
  state.networkLayoutSeed += 1;
  drawPathNetwork();
});
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
window.addEventListener("resize", () => {
  if (!state.pathNetwork || $("path-network-section").classList.contains("hidden")) return;
  window.clearTimeout(state.networkResizeTimer);
  state.networkResizeTimer = window.setTimeout(drawPathNetwork, 140);
});
initialize();
