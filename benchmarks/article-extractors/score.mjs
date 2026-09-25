import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import os from "node:os";
import model from "../../packages/dom-extractor/src/model.generated.js";
import { FIELDS, exactMatch, fieldScore, percentile } from "./metrics.mjs";

const corpus = JSON.parse(await readFile(new URL(".local/gold.json", import.meta.url), "utf8"));
const names = ["dom-extractor", "readability", "trafilatura", "extractus", "newspaper"];
const versions = {
  "dom-extractor": "0.4.0 / article-0.1.0",
  readability: "0.6.0 + jsdom 26.1.0",
  trafilatura: "2.2.0",
  extractus: "9.0.1",
  newspaper: "0.9.6",
};
const output = {
  protocol: {
    description: "Warm, offline extraction from the same saved full-page HTML files; file I/O and imports excluded from timing.",
    metric: "Present-label pages: token F1, and fraction with F1 >= 0.90. Dates with the same parsed calendar day score 1. Byline prefixes are ignored.",
    missing: "Missing-label pages are excluded from present-only field accuracy; missing specificity is reported separately.",
    datasetPages: corpus.pages.length,
    goldSha256: createHash("sha256").update(JSON.stringify(corpus.pages.map((page) =>
      [page.pageId, page.split, page.expected]))).digest("hex"),
    checkpointSha256: model.checkpointSha256,
    cleanupVersion: model.cleanupVersion,
    featureVersion: model.featureVersion,
    splits: Object.fromEntries([...new Set(corpus.pages.map((page) => page.split))].map((split) =>
      [split, corpus.pages.filter((page) => page.split === split).length])),
    websites: new Set(corpus.pages.map((page) => page.website)).size,
    node: process.version,
    os: `${process.platform} ${process.arch}`,
    cpu: os.cpus()[0]?.model ?? "unknown",
  },
  libraries: {},
};

function scoreScope(pages, byId) {
  const fields = {};
  for (const field of FIELDS) {
    const present = pages.filter((page) => page.expected[field]);
    const missing = pages.filter((page) => !page.expected[field]);
    let sumF1 = 0;
    let matchedAt90 = 0;
    let exact = 0;
    for (const page of present) {
      const predicted = byId.get(page.pageId)?.fields?.[field] || null;
      const value = fieldScore(page.expected[field], predicted, field, page.scrapedAt);
      sumF1 += value;
      matchedAt90 += Number(value >= 0.9);
      exact += Number(exactMatch(page.expected[field], predicted, field, page.scrapedAt));
    }
    const correctlyMissing = missing.reduce((sum, page) =>
      sum + Number(!byId.get(page.pageId)?.fields?.[field]), 0);
    fields[field] = {
      presentPages: present.length,
      meanTokenF1: present.length ? sumF1 / present.length : null,
      matchedAt90,
      accuracyAt90: present.length ? matchedAt90 / present.length : null,
      exactTextMatches: exact,
      exactTextAccuracy: present.length ? exact / present.length : null,
      missingPages: missing.length,
      correctlyMissing,
      missingSpecificity: missing.length ? correctlyMissing / missing.length : null,
    };
  }
  return { pages: pages.length, fields };
}

for (const name of names) {
  const file = JSON.parse(await readFile(new URL(`.local/${name}.json`, import.meta.url), "utf8"));
  const byId = new Map(file.results.map((row) => [row.pageId, row]));
  if (byId.size !== corpus.pages.length || corpus.pages.some((page) => !byId.has(page.pageId))) {
    throw new Error(`${name} did not produce exactly one result per corpus page`);
  }
  const times = file.results.map((row) => row.latencyMs);
  output.libraries[name] = {
    version: versions[name],
    attempted: file.results.length,
    failures: file.results.filter((row) => row.error).length,
    fieldsReported: Object.fromEntries(FIELDS.map((field) => [field,
      file.results.filter((row) => row.fields?.[field]).length])),
    latencyMs: {
      median: percentile(times, 0.5),
      p95: percentile(times, 0.95),
      mean: times.reduce((sum, value) => sum + value, 0) / times.length,
    },
    scopes: {
      all: scoreScope(corpus.pages, byId),
      nontraining: scoreScope(corpus.pages.filter((page) => page.split !== "train"), byId),
      test: scoreScope(corpus.pages.filter((page) => page.split === "test"), byId),
      validation: scoreScope(corpus.pages.filter((page) => page.split === "validation"), byId),
    },
  };
}

await mkdir(new URL("results/", import.meta.url), { recursive: true });
await writeFile(new URL("results/summary.json", import.meta.url), JSON.stringify(output, null, 2) + "\n");
console.log(JSON.stringify(Object.fromEntries(Object.entries(output.libraries).map(([name, result]) => [name, {
  failures: result.failures,
  medianMs: result.latencyMs.median,
  heldOut: Object.fromEntries(FIELDS.map((field) => [field,
    result.scopes.test.fields[field].accuracyAt90])),
}])), null, 2));
