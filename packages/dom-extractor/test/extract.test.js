import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  extract,
  extractField,
  extractRelativePublicationDate,
  fields,
  resolveRelativeDate,
} from "../src/index.js";

const HTML = `<!doctype html><html><body>
  <nav>Site navigation</nav>
  <main>
    <header>
      <h1>A small DOM extraction test</h1>
      <p class="byline">By Ada Lovelace</p>
      <time datetime="2026-09-19">September 19, 2026</time>
    </header>
    <article><p>This is the article body.</p><p>It has two paragraphs.</p></article>
  </main>
  <footer>Footer links</footer>
</body></html>`;

test("extract returns every model field and diagnostics", async () => {
  const result = await extract(HTML, { scrapedAt: "2026-09-21T12:00:00Z" });

  assert.deepEqual(Object.keys(result.predictions), fields);
  assert.equal(result.article?.text, "This is the article body.\n\nIt has two paragraphs.");
  assert.equal(result.article_text, result.article?.text);
  assert.equal(result.article_html, result.article?.html);
  assert.ok(result.article_confidence >= 0 && result.article_confidence <= 1);
  assert.equal(result.title?.text, "A small DOM extraction test");
  assert.equal(result.predictions.article, 8);
  assert.equal(result.predictions.title, 5);
  assert.equal(result.diagnostics.cleanupVersion, "chrome-v2");
  assert.ok(result.diagnostics.candidateCount > 0);
  for (const field of fields) {
    assert.ok(result[field] === null || typeof result[field].text === "string");
    assert.equal(result[`${field}_text`], result[field]?.text ?? null);
    assert.equal(result[`${field}_html`], result[field]?.html ?? null);
  }
});

test("extractField validates and returns a selection or missing", async () => {
  const title = await extractField(HTML, "title");
  assert.ok(title === null || typeof title.html === "string");
  await assert.rejects(() => extractField(HTML, "not-a-field"), /unknown field/);
});

test("relative publication dates exclude reading times and resolve from scrape time", () => {
  assert.equal(extractRelativePublicationDate("5 min read"), null);
  assert.equal(extractRelativePublicationDate("Published 2 days ago"), "2 days ago");
  assert.equal(
    resolveRelativeDate("2 days ago", "2026-09-21T12:00:00Z"),
    "2026-09-19T12:00:00.000Z",
  );
});

test("the documented sample output stays current", async () => {
  const sampleUrl = new URL("../examples/", import.meta.url);
  const html = await readFile(new URL("sample.html", sampleUrl), "utf8");
  const expected = JSON.parse(
    await readFile(new URL("sample-output.json", sampleUrl), "utf8"),
  );

  assert.deepEqual(
    await extract(html, { scrapedAt: "2026-09-22T12:00:00Z" }),
    expected,
  );
});
