import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { parseHTML } from "linkedom";
import model from "../src/model.generated.js";
import { parsePage } from "../src/dom.js";
import { predict } from "../src/scoring.js";
import { extract, extractMany, ExtractError, modelVersion } from "../src/index.js";

const HTML = '<html><body><main><h1 class="post-title">Don’t Vibe — Prove</h1><p class="byline">By Ada Lovelace</p><time>March 12, 2026</time><article><p>First   paragraph.</p><p>Second paragraph.</p></article></main></body></html>';

test("opt-in expansions add only the requested keys", async () => {
  const basic = await extract(HTML);
  const withHtml = await extract(HTML, { include: ["html"] });
  const withSource = await extract(HTML, { include: ["source"] });
  const withDebug = await extract(HTML, { include: ["debug"] });
  assert.deepEqual(Object.keys(basic), ["modelVersion", "fields"]);
  assert.deepEqual(Object.keys(withHtml), ["modelVersion", "fields"]);
  assert.deepEqual(Object.keys(withSource), ["modelVersion", "fields"]);
  assert.deepEqual(Object.keys(withDebug), ["modelVersion", "fields", "debug"]);
  for (const name of Object.keys(basic.fields)) {
    assert.ok(!("source" in withHtml.fields[name]));
    assert.ok(!("html" in withSource.fields[name]));
    assert.ok(!("html" in withDebug.fields[name]));
    if (withHtml.fields[name].text !== null) {
      assert.equal(typeof withHtml.fields[name].html, "string");
    } else assert.ok(!("html" in withHtml.fields[name]));
  }
  assert.ok("rejected" in withDebug.debug);
});

test("HTML expansion is the exact input substring and source selector is unique", async () => {
  const html = '<html><body><main><h1 class=\'post-title\' data-note="a  b">Don’t Vibe — Prove</h1><article><p>Story body.</p></article></main></body></html>';
  const result = await extract(html, { include: ["html", "source"] });
  assert.equal(result.fields.title.html, "<h1 class='post-title' data-note=\"a  b\">Don’t Vibe — Prove</h1>");
  const { document } = parseHTML(html);
  for (const field of Object.values(result.fields)) {
    if (!field.source) continue;
    const matches = document.querySelectorAll(field.source.selector);
    assert.equal(matches.length, 1);
    if (field.html) assert.equal(matches[0].textContent.trim(), field.text.replace(/\s+/g, " ").trim());
  }
});

test("normal whitespace collapses but preformatted lines survive", async () => {
  const html = '<html><body><h1>Keep — punctuation</h1><article><p> One   two </p><pre>consumer:\n    max_poll_records: 500\n  keep: yes</pre><p> Three    four. </p></article></body></html>';
  const result = await extract(html);
  assert.equal(result.fields.title.text, "Keep — punctuation");
  assert.ok(result.fields.body.text.includes("One two"));
  assert.ok(result.fields.body.text.includes("consumer:\n    max_poll_records: 500\n  keep: yes"));
  assert.ok(result.fields.body.text.includes("Three four."));
});

test("date stays as displayed, without ISO normalization", async () => {
  const html = '<html><body><h1>How we contain Claude across products</h1><time>Published May 25, 2026</time><article><p>A long article begins here.</p></article></body></html>';
  const result = await extract(html);
  if (result.fields.date.text !== null) {
    assert.equal(result.fields.date.text, "Published May 25, 2026");
  }
});

test("typed input and parse errors affect only their batch items", async () => {
  const inputs = [HTML, Buffer.from(HTML), "  ", "x".repeat(10 * 1024 * 1024 + 1), "<", HTML];
  const result = await extractMany(inputs);
  assert.deepEqual(result.results.map((row) => row.status), ["ok", "error", "error", "error", "error", "ok"]);
  assert.deepEqual(result.results.slice(1, 5).map((row) => row.error.code),
    ["invalidInput", "emptyInput", "inputTooLarge", "parseError"]);
  assert.deepEqual(result.results[0], result.results[5]);
  for (let i = 1; i < 5; i++) {
    await assert.rejects(extract(inputs[i]), (error) =>
      error instanceof ExtractError && error.code === result.results[i].error.code);
  }
});

test("invalid options reject the whole batch before any page runs", async () => {
  await assert.rejects(extract(HTML, { include: ["markdown"] }), TypeError);
  await assert.rejects(extractMany([HTML, " "], { include: ["markdown"] }), TypeError);
  await assert.rejects(extractMany("not an array"), (error) =>
    error instanceof ExtractError && error.code === "invalidInput");
});

test("confidence just above or below the internal threshold accepts or rejects", async () => {
  const html = "<html><body><h1>Threshold title</h1></body></html>";
  const titleIndex = model.fields.indexOf("title");
  const scores = predict(parsePage(html), model).scores;
  const best = Math.max(...scores.map((row) => row[titleIndex]));
  const saved = model.weights.missingScores[titleIndex];
  try {
    model.weights.missingScores[titleIndex] = best - 0.01;
    const above = await extract(html, { include: ["debug"] });
    assert.equal(above.fields.title.text, "Threshold title");
    assert.ok(above.fields.title.confidence > 0.5);
    model.weights.missingScores[titleIndex] = best + 0.01;
    const below = await extract(html, { include: ["debug", "source"] });
    assert.equal(below.fields.title.text, null);
    assert.ok(below.fields.title.confidence < 0.5);
    assert.equal(below.debug.rejected.title.text, "Threshold title");
    assert.equal(typeof below.fields.title.source.selector, "string");
  } finally {
    model.weights.missingScores[titleIndex] = saved;
  }
});

test("model failures use inferenceError and unexpected failures use internalError", async () => {
  const saved = model.weights.tagEmbedding;
  try {
    model.weights.tagEmbedding = null;
    await assert.rejects(extract(HTML), (error) => error.code === "inferenceError");
    const batch = await extractMany([HTML, HTML]);
    assert.deepEqual(batch.results.map((row) => row.error.code), ["inferenceError", "inferenceError"]);
  } finally {
    model.weights.tagEmbedding = saved;
  }
  const originalExp = Math.exp;
  try {
    Math.exp = () => { throw new Error("sentinel arithmetic failure"); };
    await assert.rejects(extract(HTML), (error) =>
      error.code === "internalError" && /sentinel arithmetic failure/.test(error.message));
    const batch = await extractMany([HTML]);
    assert.equal(batch.results[0].error.code, "internalError");
  } finally {
    Math.exp = originalExp;
  }
});

test("batch output has a single modelVersion and works beyond an internal chunk", async () => {
  const result = await extractMany(Array.from({ length: 102 }, () => HTML));
  assert.equal(result.modelVersion, modelVersion);
  assert.equal(result.results.length, 102);
  assert.ok(result.results.every((row) => row.status === "ok" && !("modelVersion" in row.result)));
});

test("published schema and sample match the API", async () => {
  const schema = JSON.parse(await readFile(new URL("../schema.json", import.meta.url), "utf8"));
  assert.deepEqual(schema.required, ["modelVersion", "fields"]);
  assert.deepEqual(schema.properties.fields.required, ["title", "body", "date", "byline"]);
  const html = await readFile(new URL("../examples/sample.html", import.meta.url), "utf8");
  const expected = JSON.parse(await readFile(new URL("../examples/sample-output.json", import.meta.url), "utf8"));
  assert.deepEqual(await extract(html), expected);
});
