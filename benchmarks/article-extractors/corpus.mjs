import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { parsePage, readableText } from "../../packages/dom-extractor/src/dom.js";

const here = fileURLToPath(new URL(".", import.meta.url));
const dataset = resolve(process.argv[2] ?? resolve(here, "../../data/learned_extraction/raw"));
const output = resolve(here, ".local/gold.json");
const manifest = JSON.parse(await readFile(resolve(dataset, "manifest.json"), "utf8"));
const fields = { title: "title", body: "article", date: "date", byline: "authors" };
const pages = [];
const skipped = [];
for (const record of manifest.pages) {
  let annotation;
  try {
    annotation = JSON.parse(await readFile(resolve(dataset, "annotations", `${record.page_id}.json`), "utf8"));
  } catch (error) {
    if (error.code === "ENOENT") continue;
    throw error;
  }
  if (annotation.review_status !== "reviewed" || annotation.needs_review) continue;
  const htmlPath = resolve(dataset, record.html_path);
  const html = await readFile(htmlPath, "utf8");
  const page = parsePage(html);
  const byId = new Map(page.candidates.map(({ nodeId, element }) => [nodeId, element]));
  const expected = {};
  let valid = true;
  for (const [publicField, labelField] of Object.entries(fields)) {
    const nodeId = annotation.labels[labelField];
    if (nodeId !== null && !byId.has(nodeId)) {
      skipped.push({ pageId: record.page_id, field: publicField, nodeId });
      valid = false;
      break;
    }
    expected[publicField] = nodeId === null ? null : readableText(byId.get(nodeId));
  }
  if (!valid) continue;
  pages.push({
    pageId: record.page_id,
    split: record.split,
    website: record.website,
    url: record.url,
    scrapedAt: record.scraped_at,
    htmlPath,
    expected,
  });
}
await mkdir(resolve(here, ".local"), { recursive: true });
await writeFile(output, JSON.stringify({ dataset, pages, skipped }, null, 2) + "\n");
console.log(JSON.stringify({
  pages: pages.length,
  splits: Object.fromEntries([...new Set(pages.map((page) => page.split))].map((split) =>
    [split, pages.filter((page) => page.split === split).length])),
  skipped,
  output,
}, null, 2));
