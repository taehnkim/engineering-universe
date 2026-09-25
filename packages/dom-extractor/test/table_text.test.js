import assert from "node:assert/strict";
import test from "node:test";
import { parseHTML } from "linkedom";
import { readableText } from "../src/dom.js";

test("table content stays in source order without generated markdown bullets", () => {
  const { document } = parseHTML(`<article><p>Prices are per million tokens.</p>
    <table><caption>API pricing</caption><tr><th>Model</th><th>Input</th></tr>
    <tr><td>Sol</td><td>$2</td></tr></table><p>Choose a model.</p></article>`);
  const text = readableText(document.querySelector("article"));
  assert.ok(text.indexOf("Prices") < text.indexOf("API pricing"));
  assert.ok(text.indexOf("API pricing") < text.indexOf("Model"));
  assert.ok(text.indexOf("Model") < text.indexOf("Sol"));
  assert.ok(text.indexOf("Sol") < text.indexOf("Choose a model"));
  assert.doesNotMatch(text, /- Input:/);
});
