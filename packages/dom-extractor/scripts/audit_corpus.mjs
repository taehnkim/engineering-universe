import { readFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { extract } from "../dist/index.js";

const [datasetDir, referencePath] = process.argv.slice(2);
if (!datasetDir || !referencePath) {
  throw new Error("usage: node scripts/audit_corpus.mjs DATASET_DIR PYTHON_REFERENCE_JSON");
}
const rows = JSON.parse(await readFile(referencePath, "utf8"));
const fields = ["article", "title", "authors", "date"];
const hash = (value) => value === null ? null : createHash("sha256").update(value).digest("hex");
const report = {
  pages: 0, predictionMismatchPages: 0, errors: [],
  fields: Object.fromEntries(fields.map((name) => [name, {
    sameAsPython: 0, jsCorrect: 0, pythonCorrect: 0, textSameAsPython: 0,
    normalizedTextSameAsPython: 0, htmlSameAsPython: 0,
  }])),
  sites: {},
};
for (const row of rows) {
  const html = await readFile(`${datasetDir}/${row.htmlPath}`, "utf8");
  let result;
  try {
    result = await extract(html);
  } catch (error) {
    report.errors.push({ pageId: row.pageId, error: String(error) });
    continue;
  }
  report.pages += 1;
  const mismatch = fields.filter((name) => (result[name]?.nodeId ?? null) !== row.python[name]);
  if (mismatch.length) {
    report.predictionMismatchPages += 1;
    const site = report.sites[row.website] ??= { pages: 0, mismatches: 0 };
    site.mismatches += 1;
    if (report.errors.length < 30) report.errors.push({ pageId: row.pageId, mismatch });
  }
  (report.sites[row.website] ??= { pages: 0, mismatches: 0 }).pages += 1;
  for (const name of fields) {
    report.fields[name].sameAsPython += Number((result[name]?.nodeId ?? null) === row.python[name]);
    report.fields[name].jsCorrect += Number((result[name]?.nodeId ?? null) === row.expected[name]);
    report.fields[name].pythonCorrect += Number(row.python[name] === row.expected[name]);
    report.fields[name].textSameAsPython += Number(hash(result[name]?.text ?? null) === row.contentHashes[name].text);
    report.fields[name].normalizedTextSameAsPython += Number(hash(
      result[name]?.text == null ? null : result[name].text.replace(/\s+/g, " ").trim(),
    ) === row.contentHashes[name].normalizedText);
    report.fields[name].htmlSameAsPython += Number(hash(result[name]?.html ?? null) === row.contentHashes[name].html);
  }
  if (report.pages % 100 === 0) console.error(`${report.pages}/${rows.length} pages`);
}
console.log(JSON.stringify(report, null, 2));
