import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { performance } from "node:perf_hooks";

import { extract } from "../dist/index.js";

const [datasetDir, outputPath, referencePath] = process.argv.slice(2);
if (!datasetDir || !outputPath) {
  throw new Error("usage: node scripts/benchmark_full.mjs DATASET_DIR OUTPUT_JSON [REFERENCE_JSON]");
}

const manifest = JSON.parse(await readFile(join(datasetDir, "manifest.json"), "utf8"));
const pages = [];
for (const page of manifest.pages) {
  pages.push({ id: page.page_id, html: await readFile(join(datasetDir, page.html_path), "utf8") });
}
for (const page of pages.slice(0, 10)) await extract(page.html);

const times = [];
const digests = {};
for (let round = 0; round < 3; round += 1) {
  for (const page of pages) {
    const started = performance.now();
    const result = await extract(page.html);
    times.push(performance.now() - started);
    if (round === 0) {
      digests[page.id] = createHash("sha256").update(JSON.stringify(result)).digest("hex");
    }
  }
}
times.sort((left, right) => left - right);
const percentile = (fraction) => times[Math.ceil(times.length * fraction) - 1];
const report = {
  pages: pages.length,
  runs: times.length,
  medianMs: percentile(0.5),
  p95Ms: percentile(0.95),
  meanMs: times.reduce((sum, value) => sum + value, 0) / times.length,
  digests,
};
if (referencePath) {
  const reference = JSON.parse(await readFile(referencePath, "utf8"));
  const mismatches = Object.keys(digests).filter((id) => reference.digests[id] !== digests[id]);
  report.parity = { matched: pages.length - mismatches.length, mismatches };
}
await writeFile(outputPath, JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify({
  pages: report.pages,
  runs: report.runs,
  medianMs: report.medianMs,
  p95Ms: report.p95Ms,
  meanMs: report.meanMs,
  parity: report.parity,
}, null, 2));
