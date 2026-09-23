import assert from "node:assert/strict";
import test from "node:test";
import { parseHTML } from "linkedom";

import { parsePage, readableText } from "../src/dom.js";

function articleText(html) {
  const { document } = parseHTML(`<article>${html}</article>`);
  return readableText(document.querySelector("article"));
}

test("semantic table rows become labeled bullets without losing surrounding prose", () => {
  const text = articleText(`
    <p>Prices are per million tokens.</p>
    <table>
      <caption>GPT-6 API pricing</caption>
      <thead><tr><th>Model</th><th>Input</th><th>Output</th><th>Price reduction</th></tr></thead>
      <tbody>
        <tr><th>GPT-5.6 Sol → GPT-6 Sol</th><td>$4 → $2</td><td>$20 → $10</td><td>50% cheaper</td></tr>
        <tr><th>GPT-5.6 Luna → GPT-6 Luna</th><td>$0.20 → $0.10</td><td>$1.20 → $0.50</td><td>50% cheaper</td></tr>
      </tbody>
    </table>
    <p>Choose a model.</p>
  `);
  assert.equal(text, `Prices are per million tokens.\n\nGPT-6 API pricing\n\n` +
    `- GPT-5.6 Sol → GPT-6 Sol\n` +
    `  - Input: $4 → $2\n` +
    `  - Output: $20 → $10\n` +
    `  - Price reduction: 50% cheaper\n` +
    `- GPT-5.6 Luna → GPT-6 Luna\n` +
    `  - Input: $0.20 → $0.10\n` +
    `  - Output: $1.20 → $0.50\n` +
    `  - Price reduction: 50% cheaper\n\nChoose a model.`);
});

test("all-bold td header rows are recognized, as in the saved OpenAI page", () => {
  const text = articleText(`<table><tbody>
    <tr><td><p><b>Model</b></p></td><td><p><b>Input</b></p></td><td><p><b>Output</b></p></td></tr>
    <tr><td><p>GPT-5.6 Sol → <b>GPT-6 Sol</b></p></td><td><p>$4 → <b>$2</b></p></td><td><p>$20 → <b>$10</b></p></td></tr>
  </tbody></table>`);
  assert.equal(text, `- GPT-5.6 Sol → GPT-6 Sol\n` +
    `  - Input: $4 → $2\n` +
    `  - Output: $20 → $10`);
});

test("repeated div-grid rows with a blank corner become labeled bullets", () => {
  const html = `
    <p>Model improvements.</p>
    <figure><div class="lg:-mx-6">
      <div class="grid grid-cols-5"><div></div><div>Grok 4.7 xHigh</div><div>Grok 4.6 High</div></div>
      <div class="grid grid-cols-5"><div><b>Input token price</b><small>$ per million</small></div><div>$2</div><div>$2</div></div>
      <div class="grid grid-cols-5"><div>Output token price</div><div>$6</div><div>$6</div></div>
    </div></figure>
    <p>After the comparison.</p>
  `;
  const expected = `Model improvements.\n\n` +
    `- Input token price $ per million\n` +
    `  - Grok 4.7 xHigh: $2\n` +
    `  - Grok 4.6 High: $2\n` +
    `- Output token price\n` +
    `  - Grok 4.7 xHigh: $6\n` +
    `  - Grok 4.6 High: $6\n\nAfter the comparison.`;
  assert.equal(articleText(html), expected);
  const cleaned = parsePage(`<html><body><article>${html}</article></body></html>`);
  assert.equal(readableText(cleaned.document.querySelector("article")), expected);
});

test("ambiguous grids and spanning table cells keep their original text", () => {
  const grid = articleText(`<div><div class="grid"><div>Card A</div><div>Card B</div></div>
    <div class="grid"><div>Card C</div><div>Card D</div></div>
    <div class="grid"><div>Card E</div><div>Card F</div></div></div>`);
  assert.doesNotMatch(grid, /- Card/);
  assert.match(grid, /Card A/);

  const spanning = articleText(`<table><thead><tr><th>Model</th><th>Score</th></tr></thead>
    <tbody><tr><td colspan="2">No comparable score</td></tr></tbody></table>`);
  assert.doesNotMatch(spanning, /- No comparable score/);
  assert.match(spanning, /No comparable score/);
});
