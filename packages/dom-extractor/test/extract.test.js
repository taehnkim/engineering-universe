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

test("extract returns only the four selection fields by default", async () => {
  const result = await extract(HTML);

  assert.deepEqual(Object.keys(result), fields);
  assert.equal(result.article?.text, "This is the article body.\n\nIt has two paragraphs.");
  assert.ok(result.article.confidence >= 0 && result.article.confidence <= 1);
  assert.equal(result.title?.text, "A small DOM extraction test");
  assert.equal(result.article?.nodeId, 8);
  assert.equal(result.title?.nodeId, 5);
  for (const field of fields) {
    const selection = result[field];
    assert.ok(selection === null || typeof selection.text === "string");
    if (selection) assert.deepEqual(Object.keys(selection), ["nodeId", "html", "text", "confidence"]);
  }
});

test("debug metadata is present only when explicitly requested", async () => {
  const result = await extract(HTML, { debug: true });
  assert.deepEqual(Object.keys(result), [...fields, "debug"]);
  assert.deepEqual(Object.keys(result.debug), [
    "candidateCount", "cleanupVersion", "featureVersion", "modelVersion",
    "checkpointSha256", "domBackend",
  ]);
  assert.equal(result.debug.cleanupVersion, "chrome-v2");
  assert.ok(result.debug.candidateCount > 0);
  const withoutDebug = await extract(HTML, { debug: false });
  assert.deepEqual(Object.keys(withoutDebug), fields);
  for (const field of fields) assert.deepEqual(result[field], withoutDebug[field]);
});

test("published JSON Schema matches the four-field contract", async () => {
  const schema = JSON.parse(await readFile(new URL("../schema.json", import.meta.url), "utf8"));
  assert.equal(schema.$schema, "https://json-schema.org/draft/2020-12/schema");
  assert.deepEqual(schema.required, fields);
  assert.deepEqual(Object.keys(schema.properties), [...fields, "debug"]);
  assert.equal(schema.additionalProperties, false);
  assert.deepEqual(schema.$defs.selectionOrNull.anyOf[1], { type: "null" });
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
    await extract(html),
    expected,
  );
});
