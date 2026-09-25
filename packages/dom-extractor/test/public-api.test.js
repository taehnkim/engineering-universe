import assert from "node:assert/strict";
import test from "node:test";
import { extract, extractMany, modelVersion, ExtractError } from "../src/index.js";

const HTML = '<html><body><nav>Navigation</nav><main><h1>Don’t Vibe — Prove</h1><p>By Ada Lovelace</p><time>March 12, 2026</time><article><p>  First   paragraph. </p><p>Second paragraph.</p></article></main></body></html>';

test("extract returns the minimal four-field response", async () => {
  const result = await extract(HTML);
  assert.deepEqual(Object.keys(result), ["modelVersion", "fields"]);
  assert.equal(result.modelVersion, modelVersion);
  assert.deepEqual(Object.keys(result.fields), ["title", "body", "date", "byline"]);
  for (const field of Object.values(result.fields)) {
    assert.deepEqual(Object.keys(field), ["text", "confidence"]);
    assert.ok(field.text === null || typeof field.text === "string");
    assert.equal(field.confidence, Number(field.confidence.toFixed(4)));
  }
  assert.equal(result.fields.title.text, "Don’t Vibe — Prove");
});

test("extractMany keeps order and contains item failures", async () => {
  const batch = await extractMany([HTML, " ", HTML]);
  assert.deepEqual(Object.keys(batch), ["modelVersion", "results"]);
  assert.equal(batch.modelVersion, modelVersion);
  assert.deepEqual(batch.results.map((row) => row.status), ["ok", "error", "ok"]);
  assert.equal(batch.results[1].error.code, "emptyInput");
  assert.ok(!("modelVersion" in batch.results[0].result));
  assert.deepEqual(batch.results[0], batch.results[2]);
});

test("invalid input raises typed errors", async () => {
  await assert.rejects(extract(Buffer.from(HTML)), (error) =>
    error instanceof ExtractError && error.code === "invalidInput");
});
