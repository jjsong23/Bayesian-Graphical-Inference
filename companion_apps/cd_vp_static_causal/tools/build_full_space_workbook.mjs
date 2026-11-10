import fs from "node:fs/promises";
import path from "node:path";
import { pathToFileURL } from "node:url";

const artifactPath = process.env.ARTIFACT_TOOL_MODULE_PATH;
const { SpreadsheetFile, Workbook } = await import(artifactPath ? pathToFileURL(path.resolve(artifactPath)).href : "@oai/artifact-tool");

const [runArg, figureArg, outputArg] = process.argv.slice(2);
if (!runArg || !figureArg || !outputArg) {
  throw new Error("usage: node build_full_space_workbook.mjs RUN_DIR FIGURE_DIR OUTPUT_XLSX");
}
const runDir = path.resolve(runArg);
const figureDir = path.resolve(figureArg);
const outputFile = path.resolve(outputArg);
const outputDir = path.dirname(outputFile);
await fs.mkdir(outputDir, { recursive: true });

const FONT = "Aptos";
const COLORS = {
  dark: "#143B33", teal: "#1F6B5B", mint: "#DCEFE8", pale: "#F3F7F5",
  gold: "#E7B34A", red: "#C7443E", gray: "#5E706B", line: "#CBD8D3", white: "#FFFFFF",
};

function parseTsv(text) {
  const cleaned = text.trim();
  if (!cleaned) return [];
  const lines = cleaned.split(/\r?\n/);
  const headers = lines[0].split("\t");
  return lines.slice(1).filter(Boolean).map((line) => {
    const values = line.split("\t");
    return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
  });
}

async function readTsv(name) {
  return parseTsv(await fs.readFile(path.join(runDir, name), "utf8"));
}

function finite(value, fallback = null) {
  if (value === "" || value === null || value === undefined) return fallback;
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function boolValue(value) {
  const normalized = String(value).toLowerCase();
  if (normalized === "true") return true;
  if (normalized === "false") return false;
  return value;
}

function typed(value) {
  if (value === "" || value === null || value === undefined) return null;
  const bool = boolValue(value);
  if (typeof bool === "boolean") return bool;
  const number = Number(value);
  return Number.isFinite(number) && /^[-+]?\d*\.?\d+(e[-+]?\d+)?$/i.test(String(value)) ? number : value;
}

function writeRows(sheet, startCell, headers, rows) {
  const matrix = [headers, ...rows.map((row) => headers.map((header) => typed(row[header])))];
  sheet.getRange(startCell).write(matrix);
  return matrix.length;
}

function headerStyle(range) {
  range.format = {
    fill: COLORS.teal,
    font: { name: FONT, size: 10, bold: true, color: COLORS.white },
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "all", style: "thin", color: COLORS.line },
  };
}

function bodyStyle(range) {
  range.format = {
    font: { name: FONT, size: 10, color: COLORS.dark },
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: "#E1E8E5" },
  };
}

function titleBlock(sheet, title, subtitle, lastColumn) {
  sheet.mergeCells(`A1:${lastColumn}2`);
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${lastColumn}2`).format = {
    fill: COLORS.dark,
    font: { name: FONT, size: 22, bold: true, color: COLORS.white },
    verticalAlignment: "center",
  };
  sheet.mergeCells(`A3:${lastColumn}3`);
  sheet.getRange("A3").values = [[subtitle]];
  sheet.getRange(`A3:${lastColumn}3`).format = {
    fill: COLORS.mint,
    font: { name: FONT, size: 10, italic: true, color: COLORS.dark },
    verticalAlignment: "center",
  };
  sheet.getRange(`A1:${lastColumn}1`).format.rowHeight = 24;
  sheet.getRange(`A2:${lastColumn}2`).format.rowHeight = 24;
  sheet.getRange(`A3:${lastColumn}3`).format.rowHeight = 28;
}

function formatTable(sheet, startRow, endRow, endColumn) {
  headerStyle(sheet.getRange(`A${startRow}:${endColumn}${startRow}`));
  if (endRow > startRow) bodyStyle(sheet.getRange(`A${startRow + 1}:${endColumn}${endRow}`));
  sheet.freezePanes.freezeRows(startRow);
}

function flatten(object, prefix = "") {
  const rows = [];
  for (const [key, value] of Object.entries(object ?? {})) {
    const label = prefix ? `${prefix}.${key}` : key;
    if (value && typeof value === "object" && !Array.isArray(value)) rows.push(...flatten(value, label));
    else rows.push({ field: label, value: Array.isArray(value) ? JSON.stringify(value) : value });
  }
  return rows;
}

const manifest = JSON.parse(await fs.readFile(path.join(runDir, "full_space_manifest.json"), "utf8"));
const provenance = JSON.parse(await fs.readFile(path.join(runDir, "full_space_provenance.json"), "utf8"));
const configuration = JSON.parse(await fs.readFile(path.join(runDir, "full_space_resolved_configuration.json"), "utf8"));
const rankings = await readTsv("full_graph_rankings.tsv");
const quartiles = await readTsv("full_edge_coverage_summary.tsv");
const nodeCoverage = await readTsv("full_node_coverage.tsv");
const dynamicFailures = await readTsv("full_dynamic_fit_failures.tsv");
const figureIndex = parseTsv(await fs.readFile(path.join(figureDir, "figure_index.tsv"), "utf8"));

const dynamicRows = rankings
  .filter((row) => row.dynamic_fit_status !== "not_allocated")
  .toSorted((a, b) => finite(b.model_evidence_approximation, -Infinity) - finite(a.model_evidence_approximation, -Infinity));
const topRows = rankings
  .toSorted((a, b) => finite(b.structural_preference, -Infinity) - finite(a.structural_preference, -Infinity))
  .slice(0, 500);
const strategyCounts = Object.entries(rankings.reduce((counts, row) => {
  counts[row.strategy] = (counts[row.strategy] ?? 0) + 1;
  return counts;
}, {})).map(([strategy, count]) => ({ strategy, graph_count: count, share: count / rankings.length }));
const nodeCoverageSorted = nodeCoverage.toSorted((a, b) => finite(b.graphs_containing, 0) - finite(a.graphs_containing, 0));

const workbook = Workbook.create();
const summary = workbook.worksheets.add("Summary");
const topGraphs = workbook.worksheets.add("Top graphs");
const dynamic = workbook.worksheets.add("Dynamic fits");
const coverage = workbook.worksheets.add("Coverage");
const failures = workbook.worksheets.add("Failures");
const figures = workbook.worksheets.add("Figure index");
const methods = workbook.worksheets.add("Methods & provenance");

for (const sheet of [summary, topGraphs, dynamic, coverage, failures, figures, methods]) {
  sheet.showGridLines = false;
  sheet.getRange("A1:Z2000").format.font = { name: FONT, size: 10, color: COLORS.dark };
}

// Summary sheet.
titleBlock(summary, "20-minute whole-space causal graph exploration", `Run: ${path.basename(runDir)} · Generated ${manifest.created_at}`, "P");
summary.getRange("A5:B5").values = [["Run metric", "Value"]];
summary.getRange("A6:B13").values = [
  ["Unique graphs generated", manifest.actual_graph_count],
  ["Runtime (minutes)", manifest.elapsed_seconds / 60],
  ["Eligible nodes", manifest.full_registry_node_count],
  ["Nodes observed", manifest.covered_node_count],
  ["Eligible edges", manifest.full_registry_edge_count],
  ["Edges observed", manifest.covered_edge_count],
  ["Edge coverage", manifest.covered_edge_fraction],
  ["Generation budget reached", manifest.generation_budget_exhausted],
];
headerStyle(summary.getRange("A5:B5")); bodyStyle(summary.getRange("A6:B13"));
summary.getRange("B7").format.numberFormat = "0.0";
summary.getRange("B10:B11").format.numberFormat = "#,##0";
summary.getRange("B12").format.numberFormat = "0.0%";
summary.getRange("D5:E5").values = [["Dynamic-scoring metric", "Value"]];
summary.getRange("D6:E11").values = [
  ["Graphs selected", manifest.dynamic_fit_selected],
  ["Fits attempted", manifest.dynamic_fit_attempted],
  ["Fits succeeded", manifest.dynamic_fit_succeeded],
  ["Fits failed", manifest.dynamic_fit_failed],
  ["Unusual allocations", manifest.unusual_dynamic_allocation],
  ["Attempt success rate", manifest.dynamic_fit_attempted ? manifest.dynamic_fit_succeeded / manifest.dynamic_fit_attempted : null],
];
headerStyle(summary.getRange("D5:E5")); bodyStyle(summary.getRange("D6:E11"));
summary.getRange("E11").format.numberFormat = "0.0%";
summary.getRange("G5:H5").values = [["Interpretation", "Value"]];
const structuralValues = rankings.map((row) => finite(row.structural_preference)).filter((value) => value !== null);
const successfulDynamics = dynamicRows.filter((row) => row.dynamic_fit_status === "dynamic_fit");
const dynamicImprovements = successfulDynamics.map((row) => finite(row.graph_specific_log_likelihood_improvement)).filter((value) => value !== null);
const predictiveValues = successfulDynamics.map((row) => finite(row.predictive_validation)).filter((value) => value !== null);
const bestStructural = structuralValues.reduce((best, value) => Math.max(best, value), Number.NEGATIVE_INFINITY);
const sortedStructural = structuralValues.toSorted((a, b) => a - b);
summary.getRange("G6:H11").values = [
  ["Best structural preference", Number.isFinite(bestStructural) ? bestStructural : null],
  ["Median structural preference", sortedStructural.length ? sortedStructural[Math.floor(sortedStructural.length / 2)] : null],
  ["Best dynamic improvement", dynamicImprovements.length ? Math.max(...dynamicImprovements) : null],
  ["Best predictive validation", predictiveValues.length ? Math.max(...predictiveValues) : null],
  ["Graphs with feedback", rankings.filter((row) => String(row.has_feedback_cycle).toLowerCase() === "true").length],
  ["Top predictive PNGs", figureIndex.length],
];
headerStyle(summary.getRange("G5:H5")); bodyStyle(summary.getRange("G6:H11"));
summary.getRange("H6:H9").format.numberFormat = "0.000";

summary.getRange("A15:G15").values = [["Confidence quartile", "Eligible edges", "Covered edges", "Coverage", "Appearances", "Mean appearances", "Maximum appearances"]];
summary.getRange("A16:G19").values = quartiles.map((row) => [
  `Q${row.confidence_quartile}`, finite(row.eligible_edges), finite(row.covered_edges), finite(row.coverage_fraction),
  finite(row.total_appearances), finite(row.mean_appearances), finite(row.maximum_appearances),
]);
headerStyle(summary.getRange("A15:G15")); bodyStyle(summary.getRange("A16:G19"));
summary.getRange("D16:D19").format.numberFormat = "0.0%";
summary.getRange("F16:F19").format.numberFormat = "0.00";
const coverageChart = summary.charts.add("bar", [summary.getRange("A15:A19"), summary.getRange("D15:D19")]);
coverageChart.title = "Edge exposure by source-confidence quartile";
coverageChart.titleTextStyle.typeface = FONT;
coverageChart.hasLegend = false;
coverageChart.xAxis = { numberFormatCode: "0%", numberFormatSourceLinked: false, textStyle: { typeface: FONT } };
coverageChart.yAxis = { textStyle: { typeface: FONT } };
coverageChart.setPosition("I5", "P19");

summary.getRange("A22:C22").values = [["Proposal strategy", "Graphs", "Share"]];
summary.getRange("A23:C28").values = strategyCounts.map((row) => [row.strategy, row.graph_count, row.share]);
headerStyle(summary.getRange("A22:C22")); bodyStyle(summary.getRange("A23:C28"));
summary.getRange("C23:C28").format.numberFormat = "0.0%";
const strategyChart = summary.charts.add("bar", [summary.getRange("A22:A28"), summary.getRange("B22:B28")]);
strategyChart.title = "Graph-generation strategy mix";
strategyChart.titleTextStyle.typeface = FONT;
strategyChart.hasLegend = false;
strategyChart.xAxis = { numberFormatCode: "#,##0", numberFormatSourceLinked: false, textStyle: { typeface: FONT } };
strategyChart.yAxis = { textStyle: { typeface: FONT } };
strategyChart.setPosition("E22", "L37");

summary.mergeCells("A31:P31"); summary.getRange("A31").values = [["Interpretive guardrails"]]; headerStyle(summary.getRange("A31:P31"));
summary.mergeCells("A32:P36");
summary.getRange("A32").values = [[
  "The graph bank is an exploration ensemble, not a posterior sample. Source scores are rank-mapped to sparse proposal preferences. " +
  "Structural preference ranks graph compatibility with those priors; dynamic metrics are available only for the deliberately selected subset. " +
  "Coverage records exposure to the generator, not biological support. The full TSV/JSONL ledgers remain beside this workbook for auditability."
]];
summary.getRange("A32:P36").format = { fill: "#FFF6DC", font: { name: FONT, size: 11, color: COLORS.dark }, wrapText: true, verticalAlignment: "center", borders: { preset: "outside", style: "thin", color: COLORS.gold } };
summary.freezePanes.freezeRows(3);
summary.getRange("A:P").format.columnWidth = 14;
summary.getRange("A:A").format.columnWidth = 30; summary.getRange("D:D").format.columnWidth = 26; summary.getRange("G:G").format.columnWidth = 28;

// Top graphs.
titleBlock(topGraphs, "Top 500 graphs by structural preference", "Dynamic columns are blank when a graph was not allocated to the dynamic-fitting budget.", "N");
const graphHeaders = ["graph_id", "strategy", "node_count", "edge_count", "has_feedback_cycle", "measured_node_count", "unmeasured_intermediate_count", "structural_preference", "dynamic_fit_status", "model_evidence_approximation", "graph_specific_log_likelihood_improvement", "predictive_validation", "dynamic_allocation", "canonical_sha256"];
const topEnd = 5 + topRows.length;
writeRows(topGraphs, "A5", graphHeaders, topRows);
formatTable(topGraphs, 5, topEnd, "N");
topGraphs.getRange(`H6:L${topEnd}`).format.numberFormat = "0.000";
topGraphs.getRange(`H6:H${topEnd}`).conditionalFormats.add("colorScale", { colors: ["#F4F7F5", "#B8DDCF", "#176B57"], thresholds: ["min", { type: "percentile", value: 50 }, "max"] });
topGraphs.getRange("A:A").format.columnWidth = 24; topGraphs.getRange("B:B").format.columnWidth = 28; topGraphs.getRange("I:I").format.columnWidth = 20; topGraphs.getRange("M:M").format.columnWidth = 24; topGraphs.getRange("N:N").format.columnWidth = 24;
topGraphs.getRange("C:H").format.columnWidth = 14; topGraphs.getRange("J:L").format.columnWidth = 18;

// Dynamic fits.
titleBlock(dynamic, "Dynamically fitted graph subset", "Sorted by approximate model evidence. Failed fits are retained and explained.", "N");
const dynEnd = 5 + dynamicRows.length;
writeRows(dynamic, "A5", graphHeaders, dynamicRows);
formatTable(dynamic, 5, dynEnd, "N");
if (dynEnd > 5) {
  dynamic.getRange(`H6:L${dynEnd}`).format.numberFormat = "0.000";
  dynamic.getRange(`J6:J${dynEnd}`).conditionalFormats.add("colorScale", { colors: ["#FBE9E7", "#FFF6DC", "#B8DDCF"], thresholds: ["min", { type: "percentile", value: 50 }, "max"] });
}
dynamic.getRange("A:A").format.columnWidth = 24; dynamic.getRange("B:B").format.columnWidth = 28; dynamic.getRange("I:I").format.columnWidth = 20; dynamic.getRange("M:M").format.columnWidth = 24; dynamic.getRange("N:N").format.columnWidth = 24;
dynamic.getRange("C:H").format.columnWidth = 14; dynamic.getRange("J:L").format.columnWidth = 18;

// Coverage.
titleBlock(coverage, "Generator coverage", "Node appearances and edge-quartile exposure across the complete saved graph bank.", "H");
coverage.getRange("A5:G5").values = [["Confidence quartile", "Eligible edges", "Covered edges", "Coverage", "Appearances", "Mean appearances", "Maximum appearances"]];
coverage.getRange("A6:G9").values = quartiles.map((row) => [`Q${row.confidence_quartile}`, finite(row.eligible_edges), finite(row.covered_edges), finite(row.coverage_fraction), finite(row.total_appearances), finite(row.mean_appearances), finite(row.maximum_appearances)]);
headerStyle(coverage.getRange("A5:G5")); bodyStyle(coverage.getRange("A6:G9")); coverage.getRange("D6:D9").format.numberFormat = "0.0%";
coverage.getRange("A12:E12").values = [["Node", "Source score", "Mapped preference", "Graphs containing", "Measured flag (source)"]];
coverage.getRange("A13:E" + (12 + nodeCoverageSorted.length)).values = nodeCoverageSorted.map((row) => [row.node, finite(row.source_score), finite(row.mapped_preference), finite(row.graphs_containing), row.measured]);
const covEnd = 12 + nodeCoverageSorted.length;
headerStyle(coverage.getRange("A12:E12")); bodyStyle(coverage.getRange(`A13:E${covEnd}`));
coverage.getRange(`B13:C${covEnd}`).format.numberFormat = "0.000000";
coverage.getRange(`D13:D${covEnd}`).format.numberFormat = "#,##0";
coverage.getRange(`D13:D${covEnd}`).conditionalFormats.add("dataBar", { color: COLORS.teal, gradient: true, thresholds: ["min", "max"] });
coverage.freezePanes.freezeRows(12);
coverage.getRange("A:A").format.columnWidth = 24; coverage.getRange("B:C").format.columnWidth = 18; coverage.getRange("D:E").format.columnWidth = 22;

// Failures.
titleBlock(failures, "Dynamic-fit failures", "A failed dynamic fit does not invalidate structural exploration; it marks insufficient or unstable measured support.", "J");
const failureHeaders = dynamicFailures.length ? Object.keys(dynamicFailures[0]) : ["status"];
const failureRows = dynamicFailures.length ? dynamicFailures : [{ status: "No dynamic-fit failures were recorded." }];
const failureEnd = 5 + failureRows.length;
writeRows(failures, "A5", failureHeaders, failureRows);
formatTable(failures, 5, failureEnd, String.fromCharCode(64 + Math.min(26, failureHeaders.length)));
failures.getRange("A:Z").format.columnWidth = 18; failures.getRange("A:A").format.columnWidth = 24;

// Figure index and embedded graph thumbnails.
titleBlock(figures, "Top predictive graph PNGs", "Ranked by held-out predictive validation (negative balanced MSE); values closer to zero are better.", "N");
const figureHeaders = Object.keys(figureIndex[0] ?? { figure: 1, file: "", graph_id: "", selection_reason: "" });
const figureEnd = 5 + figureIndex.length;
writeRows(figures, "A5", figureHeaders, figureIndex);
formatTable(figures, 5, figureEnd, String.fromCharCode(64 + figureHeaders.length));
figures.getRange("A:N").format.columnWidth = 16; figures.getRange("B:B").format.columnWidth = 42; figures.getRange("C:C").format.columnWidth = 24; figures.getRange("D:D").format.columnWidth = 38;
for (let index = 0; index < figureIndex.length; index += 1) {
  const imagePath = path.join(figureDir, figureIndex[index].file);
  const base64 = (await fs.readFile(imagePath)).toString("base64");
  const imageRow = 9 + Math.floor(index / 2) * 20;
  const imageCol = (index % 2) * 7;
  figures.images.add({ dataUrl: `data:image/png;base64,${base64}`, anchor: { from: { row: imageRow, col: imageCol }, extent: { widthPx: 520, heightPx: 345 } } });
}

// Methods and provenance.
titleBlock(methods, "Methods and provenance", "Machine-readable configurations and full ledgers are preserved in the run directory.", "F");
const methodRows = [
  { field: "run_directory", value: runDir },
  { field: "figure_directory", value: figureDir },
  ...flatten(manifest, "manifest"),
  ...flatten(provenance, "provenance"),
  ...flatten(configuration, "configuration"),
];
methods.getRange("A5:B5").values = [["Field", "Value"]];
methods.getRange("A6:B" + (5 + methodRows.length)).values = methodRows.map((row) => [row.field, typed(row.value)]);
headerStyle(methods.getRange("A5:B5")); bodyStyle(methods.getRange(`A6:B${5 + methodRows.length}`));
methods.getRange("A:A").format.columnWidth = 48; methods.getRange("B:B").format.columnWidth = 100;
methods.getRange(`B6:B${5 + methodRows.length}`).format.wrapText = true;
methods.freezePanes.freezeRows(5);

// Final visual and numerical QA.
workbook.recalculate();
const previews = [
  ["Summary", "A1:P37"], ["Top graphs", "A1:N30"], ["Dynamic fits", "A1:N30"],
  ["Coverage", "A1:H35"], ["Failures", "A1:J30"], ["Figure index", "A1:N65"], ["Methods & provenance", "A1:F45"],
];
const qa = [];
for (const [sheetName, range] of previews) {
  const inspection = await workbook.inspect({ kind: "region", sheetId: sheetName, range, maxChars: 3500, tableMaxRows: 8, tableMaxCols: 10, tableMaxCellChars: 100 });
  qa.push({ sheet: sheetName, range, inspection: inspection.ndjson });
  const preview = await workbook.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(outputDir, `workbook_preview_${sheetName.toLowerCase().replace(/[^a-z0-9]+/g, "_")}.png`), new Uint8Array(await preview.arrayBuffer()));
}
const formulaInspection = await workbook.inspect({ kind: "formula", maxChars: 5000, options: { maxResults: 100 } });
qa.push({ formula_inspection: formulaInspection.ndjson });
await fs.writeFile(path.join(outputDir, "workbook_qa.json"), JSON.stringify(qa, null, 2), "utf8");

const exported = await SpreadsheetFile.exportXlsx(workbook);
await exported.save(outputFile);
console.log(JSON.stringify({ output: outputFile, sheets: previews.map(([name]) => name), top_graph_rows: topRows.length, dynamic_rows: dynamicRows.length, node_rows: nodeCoverageSorted.length, figures: figureIndex.length }, null, 2));
