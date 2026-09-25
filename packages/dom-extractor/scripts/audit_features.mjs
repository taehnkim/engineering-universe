import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

import { parsePage } from "../src/dom.js";
import { featurizePage } from "../src/features.js";
import model from "../src/model.generated.js";

const [datasetDir, outputPath, referencePath] = process.argv.slice(2);
if (!datasetDir || !outputPath) {
  throw new Error("usage: node scripts/audit_features.mjs DATASET_DIR OUTPUT_JSON [REFERENCE_JSON]");
}
const manifest = JSON.parse(await readFile(join(datasetDir, "manifest.json"), "utf8"));
const digests = {};
for (const row of manifest.pages) {
  const html = await readFile(join(datasetDir, row.html_path), "utf8");
  const page = parsePage(html);
  const features = featurizePage(page, model);
  digests[row.page_id] = createHash("sha256").update(JSON.stringify(features)).digest("hex");
}
const report = { pages: manifest.pages.length, digests };
if (referencePath) {
  const reference = JSON.parse(await readFile(referencePath, "utf8"));
  const mismatches = Object.keys(digests).filter((id) => reference.digests[id] !== digests[id]);
  report.parity = { matched: manifest.pages.length - mismatches.length, mismatches };
}
await writeFile(outputPath, JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify({ pages: report.pages, parity: report.parity }, null, 2));
