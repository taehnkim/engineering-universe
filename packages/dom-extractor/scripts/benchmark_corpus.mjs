import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { performance } from "node:perf_hooks";

import { extract } from "../dist/index.js";

const datasetDir = process.argv[2];
if (!datasetDir) throw new Error("usage: node scripts/benchmark_corpus.mjs DATASET_DIR");
const manifest = JSON.parse(await readFile(join(datasetDir, "manifest.json"), "utf8"));
const pages = [];
for (const row of manifest.pages) {
  if (pages.length === 10) break;
  const path = join(datasetDir, "annotations", `${row.page_id}.json`);
  let annotation;
  try { annotation = JSON.parse(await readFile(path, "utf8")); }
  catch (error) { if (error.code === "ENOENT") continue; throw error; }
  if (annotation.review_status !== "reviewed" || annotation.needs_review) continue;
  pages.push(await readFile(join(datasetDir, row.html_path), "utf8"));
}
if (pages.length !== 10) throw new Error(`expected 10 reviewed pages, got ${pages.length}`);
for (const html of pages) await extract(html);
const milliseconds = [];
for (let round = 0; round < 10; round += 1) {
  for (const html of pages) {
    const started = performance.now();
    await extract(html);
    milliseconds.push(performance.now() - started);
  }
}
milliseconds.sort((a, b) => a - b);
console.log(JSON.stringify({
  pages: pages.length,
  runs: milliseconds.length,
  meanMs: milliseconds.reduce((sum, value) => sum + value, 0) / milliseconds.length,
  medianMs: milliseconds[Math.floor(milliseconds.length / 2)],
  p95Ms: milliseconds[Math.ceil(milliseconds.length * .95) - 1],
}, null, 2));
