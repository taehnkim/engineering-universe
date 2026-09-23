import { readFile } from "node:fs/promises";
import { extract } from "../dist/index.js";

const [datasetDir, referencePath] = process.argv.slice(2);
if (!datasetDir || !referencePath) {
  throw new Error("usage: node scripts/audit_corpus.mjs DATASET_DIR PYTHON_REFERENCE_JSON");
}
const rows = JSON.parse(await readFile(referencePath, "utf8"));
const fields = ["article", "title", "authors", "date", "summary", "relative_date"];
const report = {
  pages: 0, predictionMismatchPages: 0, errors: [],
  fields: Object.fromEntries(fields.map((name) => [name, { sameAsPython: 0, jsCorrect: 0, pythonCorrect: 0 }])),
  sites: {},
};
for (const row of rows) {
  const html = await readFile(`${datasetDir}/${row.htmlPath}`, "utf8");
  let result;
  try {
    result = await extract(html, { scrapedAt: "2026-09-22T00:00:00Z" });
  } catch (error) {
    report.errors.push({ pageId: row.pageId, error: String(error) });
    continue;
  }
  report.pages += 1;
  const mismatch = fields.filter((name) => result.predictions[name] !== row.python[name]);
  if (mismatch.length) {
    report.predictionMismatchPages += 1;
    const site = report.sites[row.website] ??= { pages: 0, mismatches: 0 };
    site.mismatches += 1;
    if (report.errors.length < 30) report.errors.push({ pageId: row.pageId, mismatch });
  }
  (report.sites[row.website] ??= { pages: 0, mismatches: 0 }).pages += 1;
  for (const name of fields) {
    report.fields[name].sameAsPython += Number(result.predictions[name] === row.python[name]);
    report.fields[name].jsCorrect += Number(result.predictions[name] === row.expected[name]);
    report.fields[name].pythonCorrect += Number(row.python[name] === row.expected[name]);
  }
  if (report.pages % 100 === 0) console.error(`${report.pages}/${rows.length} pages`);
}
console.log(JSON.stringify(report, null, 2));
