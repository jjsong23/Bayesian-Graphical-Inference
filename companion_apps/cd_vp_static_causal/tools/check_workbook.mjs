import { pathToFileURL } from "node:url";
import path from "node:path";

const artifactPath = process.env.ARTIFACT_TOOL_MODULE_PATH;
const { FileBlob, SpreadsheetFile } = await import(artifactPath ? pathToFileURL(path.resolve(artifactPath)).href : "@oai/artifact-tool");
const [workbookPath] = process.argv.slice(2);
if (!workbookPath) throw new Error("usage: node check_workbook.mjs WORKBOOK_XLSX");
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const sheets = await workbook.inspect({ kind: "sheet", include: "id,name", maxChars: 4000 });
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
const figures = await workbook.inspect({ kind: "region", sheetId: "Figure index", range: "A1:N12", maxChars: 5000, tableMaxRows: 12, tableMaxCols: 14 });
console.log(JSON.stringify({ sheets: sheets.ndjson, errors: errors.ndjson, figure_index: figures.ndjson }, null, 2));
