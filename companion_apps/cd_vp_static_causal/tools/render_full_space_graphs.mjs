import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import readline from "node:readline";
import zlib from "node:zlib";
import { pathToFileURL } from "node:url";

const skiaRoot = process.env.SKIA_CANVAS_MODULE_PATH;
const { Canvas, loadImage } = await import(skiaRoot ? pathToFileURL(path.resolve(skiaRoot)).href : "skia-canvas");

const [runArg, outputArg] = process.argv.slice(2);
if (!runArg || !outputArg) {
  throw new Error("usage: node render_full_space_graphs.mjs RUN_DIRECTORY OUTPUT_DIRECTORY");
}
const runDir = path.resolve(runArg);
const outputDir = path.resolve(outputArg);
await fsp.mkdir(outputDir, { recursive: true });

function parseTsv(text) {
  const lines = text.trim().split(/\r?\n/);
  const headers = lines[0].split("\t");
  return lines.slice(1).filter(Boolean).map((line) => {
    const values = line.split("\t");
    return Object.fromEntries(headers.map((header, index) => [header, values[index] ?? ""]));
  });
}

function numeric(row, key, fallback = Number.NEGATIVE_INFINITY) {
  const value = Number(row[key]);
  return Number.isFinite(value) ? value : fallback;
}

const rankings = parseTsv(await fsp.readFile(path.join(runDir, "full_graph_rankings.tsv"), "utf8"));
const selected = rankings
  .filter((row) => row.dynamic_fit_status === "dynamic_fit" && row.predictive_validation !== "" && Number.isFinite(Number(row.predictive_validation)))
  .toSorted((a, b) => numeric(b, "predictive_validation") - numeric(a, "predictive_validation"))
  .slice(0, 6)
  .map((row, index) => ({
    ...row,
    predictive_rank: index + 1,
    figure_reason: `Predictive rank ${index + 1} (validation ${numeric(row, "predictive_validation").toFixed(4)})`,
  }));
const wanted = new Set(selected.map((row) => row.graph_id));
const graphs = new Map();
const input = fs.createReadStream(path.join(runDir, "full_graph_bank.jsonl.gz")).pipe(zlib.createGunzip());
for await (const line of readline.createInterface({ input, crlfDelay: Infinity })) {
  if (!line) continue;
  const graph = JSON.parse(line);
  if (wanted.has(graph.graph_id)) graphs.set(graph.graph_id, graph);
  if (graphs.size === wanted.size) break;
}

function hash(text) {
  let value = 2166136261;
  for (const character of text) value = Math.imul(value ^ character.charCodeAt(0), 16777619);
  return value >>> 0;
}

function layoutGraph(graph, width, height) {
  const nodes = graph.nodes;
  const nodeIndex = new Map(nodes.map((node, index) => [node, index]));
  const adjacency = new Map(nodes.map((node) => [node, []]));
  for (const edge of graph.edges) {
    adjacency.get(edge.source_node_a)?.push(edge.source_node_b);
    adjacency.get(edge.source_node_b)?.push(edge.source_node_a);
  }
  const anchor = nodes.includes("PRKACA") ? "PRKACA" : nodes.toSorted((a, b) => adjacency.get(b).length - adjacency.get(a).length)[0];
  const distance = new Map([[anchor, 0]]);
  const queue = [anchor];
  while (queue.length) {
    const current = queue.shift();
    for (const neighbor of adjacency.get(current) ?? []) {
      if (!distance.has(neighbor)) {
        distance.set(neighbor, distance.get(current) + 1);
        queue.push(neighbor);
      }
    }
  }
  const positions = nodes.map((node, index) => {
    const level = distance.get(node) ?? 5;
    const angle = (2 * Math.PI * index) / Math.max(nodes.length, 1) + (hash(node) % 997) / 997;
    const radius = 70 + level * 120;
    return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
  });
  positions[nodeIndex.get(anchor)] = { x: 0, y: 0 };
  const targetSpring = 120;
  for (let iteration = 0; iteration < 360; iteration += 1) {
    const force = positions.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < positions.length; i += 1) {
      for (let j = i + 1; j < positions.length; j += 1) {
        let dx = positions[i].x - positions[j].x;
        let dy = positions[i].y - positions[j].y;
        const d2 = Math.max(100, dx * dx + dy * dy);
        const scale = 5200 / d2;
        force[i].x += dx * scale;
        force[i].y += dy * scale;
        force[j].x -= dx * scale;
        force[j].y -= dy * scale;
      }
    }
    for (const edge of graph.edges) {
      const i = nodeIndex.get(edge.source_node_a);
      const j = nodeIndex.get(edge.source_node_b);
      if (i === undefined || j === undefined) continue;
      const dx = positions[j].x - positions[i].x;
      const dy = positions[j].y - positions[i].y;
      const distanceNow = Math.max(1, Math.hypot(dx, dy));
      const scale = 0.018 * (distanceNow - targetSpring) / distanceNow;
      force[i].x += dx * scale;
      force[i].y += dy * scale;
      force[j].x -= dx * scale;
      force[j].y -= dy * scale;
    }
    const cooling = 0.9 * (1 - iteration / 400);
    for (let i = 0; i < positions.length; i += 1) {
      if (nodes[i] === anchor) continue;
      positions[i].x += Math.max(-10, Math.min(10, force[i].x)) * cooling;
      positions[i].y += Math.max(-10, Math.min(10, force[i].y)) * cooling;
    }
  }
  const xs = positions.map((point) => point.x);
  const ys = positions.map((point) => point.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const marginX = 120, top = 150, bottom = 130;
  return new Map(nodes.map((node, index) => [node, {
    x: marginX + ((positions[index].x - minX) / Math.max(1, maxX - minX)) * (width - 2 * marginX),
    y: top + ((positions[index].y - minY) / Math.max(1, maxY - minY)) * (height - top - bottom),
  }]));
}

function roundedRect(ctx, x, y, width, height, radius) {
  ctx.beginPath();
  ctx.roundRect(x, y, width, height, radius);
  ctx.closePath();
}

function edgeEndpoints(edge) {
  if (edge.direction === "a_to_b") return [edge.source_node_a, edge.source_node_b, "single"];
  if (edge.direction === "b_to_a") return [edge.source_node_b, edge.source_node_a, "single"];
  if (edge.direction === "both") return [edge.source_node_a, edge.source_node_b, "reciprocal"];
  return [edge.source_node_a, edge.source_node_b, "unresolved"];
}

async function renderGraph(graph, row, filename) {
  const width = 1800, height = 1200;
  const canvas = new Canvas(width, height);
  const ctx = canvas.getContext("2d");
  ctx.fillStyle = "#F4F7F5";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#12372F";
  ctx.font = "bold 32px Arial";
  ctx.fillText(row.figure_reason, 60, 52);
  ctx.font = "20px Arial";
  ctx.fillStyle = "#44635B";
  ctx.fillText(`${graph.graph_id} · ${graph.strategy} · ${graph.nodes.length} nodes · ${graph.edges.length} edges`, 60, 88);
  ctx.fillText(`Structural preference ${numeric(row, "structural_preference", 0).toFixed(3)} · Dynamic status ${row.dynamic_fit_status}`, 60, 118);
  const positions = layoutGraph(graph, width, height);
  for (const edge of graph.edges) {
    const [source, target, directionMode] = edgeEndpoints(edge);
    const a = positions.get(source), b = positions.get(target);
    if (!a || !b) continue;
    const color = edge.sign === "activation" ? "#16845B" : edge.sign === "inhibition" ? "#C43D32" : "#7A8B86";
    ctx.strokeStyle = color;
    ctx.lineWidth = 2 + 2 * Math.max(0, Math.min(1, Number(edge.mapped_preference) || 0));
    ctx.setLineDash(edge.mechanism === "physical" ? [] : edge.mechanism === "phosphorylation" ? [10, 5] : [4, 5]);
    const dx = b.x - a.x, dy = b.y - a.y, length = Math.max(1, Math.hypot(dx, dy));
    const ux = dx / length, uy = dy / length;
    const start = { x: a.x + ux * 25, y: a.y + uy * 25 };
    const end = { x: b.x - ux * 25, y: b.y - uy * 25 };
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.lineTo(end.x, end.y);
    ctx.stroke();
    const drawArrow = (point, angle) => {
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.moveTo(point.x, point.y);
      ctx.lineTo(point.x - 14 * Math.cos(angle - 0.45), point.y - 14 * Math.sin(angle - 0.45));
      ctx.lineTo(point.x - 14 * Math.cos(angle + 0.45), point.y - 14 * Math.sin(angle + 0.45));
      ctx.closePath();
      ctx.fill();
    };
    const forwardAngle = Math.atan2(end.y - start.y, end.x - start.x);
    if (directionMode === "single" || directionMode === "reciprocal") {
      drawArrow(end, forwardAngle);
    }
    if (directionMode === "reciprocal") {
      drawArrow(start, forwardAngle + Math.PI);
    }
    if (directionMode === "unresolved") {
      const midX = (start.x + end.x) / 2;
      const midY = (start.y + end.y) / 2;
      ctx.fillStyle = "rgba(255,255,255,0.95)";
      roundedRect(ctx, midX - 9, midY - 11, 18, 20, 4);
      ctx.fill();
      ctx.fillStyle = color;
      ctx.font = "bold 16px Arial";
      ctx.fillText("?", midX - 4, midY + 5);
    }
  }
  ctx.setLineDash([]);
  const degree = new Map(graph.nodes.map((node) => [node, 0]));
  for (const edge of graph.edges) {
    degree.set(edge.source_node_a, (degree.get(edge.source_node_a) ?? 0) + 1);
    degree.set(edge.source_node_b, (degree.get(edge.source_node_b) ?? 0) + 1);
  }
  for (const node of graph.nodes) {
    const point = positions.get(node);
    const radius = 14 + Math.min(12, (degree.get(node) ?? 0) * 1.4);
    ctx.fillStyle = node === "PRKACA" ? "#F4B942" : "#DCEFE8";
    ctx.strokeStyle = node === "PRKACA" ? "#8A5B00" : "#176B57";
    ctx.lineWidth = node === "PRKACA" ? 4 : 2;
    ctx.beginPath();
    ctx.arc(point.x, point.y, radius, 0, 2 * Math.PI);
    ctx.fill(); ctx.stroke();
    ctx.font = `${node === "PRKACA" ? "bold " : ""}16px Arial`;
    const labelWidth = ctx.measureText(node).width;
    ctx.fillStyle = "rgba(255,255,255,0.90)";
    roundedRect(ctx, point.x - labelWidth / 2 - 4, point.y + radius + 3, labelWidth + 8, 22, 5);
    ctx.fill();
    ctx.fillStyle = "#133C33";
    ctx.fillText(node, point.x - labelWidth / 2, point.y + radius + 19);
  }
  const legendY = height - 55;
  ctx.font = "17px Arial";
  const legend = [["Activation", "#16845B"], ["Inhibition", "#C43D32"], ["Unknown sign", "#7A8B86"]];
  legend.forEach(([label, color], index) => {
    const x = 60 + index * 210;
    ctx.strokeStyle = color; ctx.lineWidth = 4; ctx.beginPath(); ctx.moveTo(x, legendY); ctx.lineTo(x + 42, legendY); ctx.stroke();
    ctx.fillStyle = "#314C45"; ctx.fillText(label, x + 52, legendY + 6);
  });
  await canvas.saveAs(filename);
}

const figureRows = [];
for (let index = 0; index < selected.length; index += 1) {
  const row = selected[index];
  const graph = graphs.get(row.graph_id);
  if (!graph) continue;
  const slug = `${String(index + 1).padStart(2, "0")}_predictive_rank_${index + 1}_${row.graph_id.toLowerCase()}.png`;
  const filename = path.join(outputDir, slug);
  await renderGraph(graph, row, filename);
  figureRows.push({
    figure: index + 1, predictive_rank: index + 1, file: slug, graph_id: row.graph_id, selection_reason: row.figure_reason,
    strategy: row.strategy, node_count: Number(row.node_count), edge_count: Number(row.edge_count),
    structural_preference: numeric(row, "structural_preference", null), dynamic_fit_status: row.dynamic_fit_status,
    model_evidence_approximation: numeric(row, "model_evidence_approximation", null),
    predictive_validation: numeric(row, "predictive_validation", null),
  });
}

const contact = new Canvas(1800, 2400);
const contactCtx = contact.getContext("2d");
contactCtx.fillStyle = "#EEF3F0"; contactCtx.fillRect(0, 0, 1800, 2400);
contactCtx.fillStyle = "#12372F"; contactCtx.font = "bold 38px Arial"; contactCtx.fillText("Top predictive full-space signaling graphs", 50, 55);
for (let index = 0; index < figureRows.length; index += 1) {
  const image = await loadImage(path.join(outputDir, figureRows[index].file));
  const col = index % 2, row = Math.floor(index / 2);
  const x = 35 + col * 875, y = 85 + row * 755;
  contactCtx.drawImage(image, x, y, 850, 710);
}
await contact.saveAs(path.join(outputDir, "00_representative_graphs_contact_sheet.png"));

const headers = Object.keys(figureRows[0] ?? { figure: 0 });
const tsv = [headers.join("\t"), ...figureRows.map((row) => headers.map((header) => row[header] ?? "").join("\t"))].join("\n") + "\n";
await fsp.writeFile(path.join(outputDir, "figure_index.tsv"), tsv, "utf8");
await fsp.writeFile(path.join(outputDir, "figure_index.json"), JSON.stringify(figureRows, null, 2), "utf8");
console.log(JSON.stringify({ output_directory: outputDir, figure_count: figureRows.length, figures: figureRows }, null, 2));
