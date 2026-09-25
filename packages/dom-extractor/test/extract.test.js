import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parseHTML } from "linkedom";
import { cssSelector, parsePage } from "../src/dom.js";

import {
  extract,
  extractField,
  extractRelativePublicationDate,
  fields,
  resolveRelativeDate,
  schemaVersion,
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

test("published v1 schema describes selected fields and optional HTML/debug", async () => {
  const schema = JSON.parse(await readFile(new URL("../schema.v1.json", import.meta.url), "utf8"));
  assert.equal(schema.$schema, "https://json-schema.org/draft/2020-12/schema");
  assert.deepEqual(schema.required, ["type", "schemaVersion", "modelVersion", "sourceUrl", "fields"]);
  assert.deepEqual(Object.keys(schema.properties.fields.properties), fields);
  assert.equal(schema.$defs.selection.properties.html.type, "string");
  assert.ok(!schema.$defs.selection.required.includes("html"));
  assert.equal(schema.properties.debug.type, "object");
  assert.ok(!schema.required.includes("debug"));
  assert.equal(schema.properties.fields.properties.authors.$ref, "#/$defs/authorSelection");
  assert.deepEqual(schema.$defs.authorSelection.anyOf[1], { type: "null" });
});

test("extractField validates and returns a selection or missing", async () => {
  const title = await extractField(HTML, "title");
  assert.ok(title === null || typeof title.html === "string");
  await assert.rejects(() => extractField(HTML, "not-a-field"), /unknown field/);
});

test("versioned output is field-selectable, one-based, and text-only by default", async () => {
  const legacy = await extract(HTML);
  const result = await extract(HTML, {
    version: "1.0.0", fields: ["title", "authors", "article"],
  });

  assert.deepEqual(Object.keys(result), ["type", "schemaVersion", "modelVersion", "sourceUrl", "fields"]);
  assert.equal(result.type, "article");
  assert.equal(result.schemaVersion, schemaVersion);
  assert.equal(result.sourceUrl, null);
  assert.deepEqual(Object.keys(result.fields), ["title", "authors", "article"]);
  assert.equal(result.fields.title.id, legacy.title.nodeId + 1);
  assert.equal(result.fields.authors.value, legacy.authors.text);
  assert.equal(typeof result.fields.authors.value, "string");
  assert.equal(result.fields.article.value, legacy.article.text);
  assert.equal("html" in result.fields.article, false);
  assert.equal(result.fields.article.confidence,
    Number(legacy.article.confidence.toFixed(4)));
  const { document } = parseHTML(HTML);
  assert.equal(
    document.querySelector(result.fields.title.selector).textContent,
    "A small DOM extraction test",
  );
  const htmlOnly = await extract(HTML, {
    version: "1.0.0", fields: ["authors"], formats: ["html"],
  });
  assert.equal(typeof htmlOnly.fields.authors.value, "string");
  assert.equal(typeof htmlOnly.fields.authors.html, "string");
});

test("v1 selectors point into the original HTML, even when cleanup removes siblings", () => {
  const html = `<html><body><p class="cookie-banner">Accept cookies</p>
    <p>Keep this content</p></body></html>`;
  const page = parsePage(html);
  const kept = page.candidates.find((candidate) => candidate.element.textContent === "Keep this content");
  const selector = cssSelector(kept.element, page);
  assert.match(selector, /p:nth-of-type\(2\)$/);
  assert.equal(parseHTML(html).document.querySelector(selector).textContent, "Keep this content");
});

test("HTML, source URL, and debug metadata are opt-in in v1", async () => {
  const html = HTML.replace("<body>",
    '<head><link rel="canonical" href="https://example.com/article"></head><body>');
  const result = await extract(html, {
    fields: ["article"], formats: ["text", "html"], debug: true,
  });
  assert.equal(result.sourceUrl, "https://example.com/article");
  assert.match(result.fields.article.html, /^<article>/);
  assert.equal(result.debug.domBackend, "javascript");

  const overridden = await extract(html, {
    version: "1.0.0", fields: ["title"], sourceUrl: "https://other.example/post",
  });
  assert.equal(overridden.sourceUrl, "https://other.example/post");
  assert.equal("debug" in overridden, false);
  assert.equal("html" in overridden.fields.title, false);

  const ogOnly = await extract(HTML.replace("<body>",
    '<head><meta property="og:url" content="https://example.org/og-post"></head><body>'),
  { version: "1.0.0", fields: [] });
  assert.equal(ogOnly.sourceUrl, "https://example.org/og-post");
  assert.deepEqual(ogOnly.fields, {});
});

test("missing authors are null in v1", async () => {
  const result = await extract("<html><body><h1>Title</h1></body></html>", {
    version: "1.0.0", fields: ["authors"],
  });
  assert.deepEqual(Object.keys(result.fields), ["authors"]);
  assert.equal(result.fields.authors, null);
});

test("v1 normalizes an unambiguous written date without losing its raw text", async () => {
  const html = `<html><body><main><header>
    <h1>How we contain Claude across products</h1>
    <time class="post-date">Published May 25, 2026</time>
    <p class="byline">Written by Max McGuinness</p>
    </header><article>
    <p>Twelve months ago, we rejected the idea of granting access to the system.</p>
    <p>Today we built a better system with clear boundaries.</p>
    </article></main></body></html>`;
  const result = await extract(html, { version: "1.0.0", fields: ["date"] });
  assert.equal(result.fields.date.value, "2026-05-25");
  assert.equal(result.fields.date.raw, "Published May 25, 2026");
});

test("v1 validates output options and extractField preserves both versions", async () => {
  await assert.rejects(() => extract(HTML, { version: "2.0.0" }), /unsupported output version/);
  await assert.rejects(() => extract(HTML, { fields: ["unknown"] }), /fields must be an array/);
  await assert.rejects(() => extract(HTML, { formats: ["markdown"] }), /formats must contain/);
  await assert.rejects(() => extract(HTML, { sourceUrl: "/relative" }), /sourceUrl must be an absolute/);
  const legacy = await extractField(HTML, "title");
  const versioned = await extractField(HTML, "title", { version: "1.0.0" });
  assert.equal(versioned.id, legacy.nodeId + 1);
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
