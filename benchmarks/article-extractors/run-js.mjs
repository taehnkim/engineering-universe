import { readFile, writeFile } from "node:fs/promises";
import { performance } from "node:perf_hooks";
import { JSDOM, VirtualConsole } from "jsdom";
import { Readability } from "@mozilla/readability";
import { extractFromHtml } from "@extractus/article-extractor";
import { extract } from "../../packages/dom-extractor/dist/index.js";
import { textFromHtml } from "./html-text.mjs";

const name = process.argv[2];
if (!["dom-extractor", "readability", "extractus"].includes(name)) {
  throw new Error("usage: node run-js.mjs dom-extractor|readability|extractus");
}
const corpus = JSON.parse(await readFile(new URL(".local/gold.json", import.meta.url), "utf8"));

const runners = {
  "dom-extractor": async (html) => {
    const result = await extract(html);
    return Object.fromEntries(Object.entries(result.fields).map(([field, value]) => [field, value.text]));
  },
  readability: async (html, url) => {
    const dom = new JSDOM(html, {
      url: url || "https://example.invalid/",
      virtualConsole: new VirtualConsole(),
    });
    try {
      const article = new Readability(dom.window.document).parse();
      return {
        title: article?.title || null,
        body: article?.textContent?.trim() || null,
        date: article?.publishedTime || null,
        byline: article?.byline || null,
      };
    } finally {
      dom.window.close();
    }
  },
  extractus: async (html, url) => {
    const article = await extractFromHtml(html, url || "https://example.invalid/");
    return {
      title: article?.title || null,
      body: textFromHtml(article?.content),
      date: article?.published || null,
      byline: article?.author || null,
    };
  },
};

const run = runners[name];
for (const page of corpus.pages.slice(0, 10)) {
  const html = await readFile(page.htmlPath, "utf8");
  try { await run(html, page.url); } catch { /* Warm-up errors are counted in the main pass. */ }
}

const results = [];
for (const [index, page] of corpus.pages.entries()) {
  const html = await readFile(page.htmlPath, "utf8");
  const start = performance.now();
  try {
    const fields = await run(html, page.url);
    results.push({ pageId: page.pageId, latencyMs: performance.now() - start, fields });
  } catch (error) {
    results.push({ pageId: page.pageId, latencyMs: performance.now() - start,
      error: `${error.name}: ${error.message}` });
  }
  if ((index + 1) % 100 === 0 || index + 1 === corpus.pages.length) {
    console.log(`${name}: ${index + 1}/${corpus.pages.length}`);
  }
}
await writeFile(new URL(`.local/${name}.json`, import.meta.url),
  JSON.stringify({ name, results }, null, 2) + "\n");
console.log(JSON.stringify({ name, pages: results.length,
  failures: results.filter((row) => row.error).length }));
