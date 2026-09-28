import test from "node:test";
import assert from "node:assert/strict";

import { elementText, parsePage, rescueAuthorNode } from "../src/dom.js";

function node(page, tag, text) {
  return page.candidates.find((candidate) =>
    candidate.element.localName === tag && elementText(candidate.element) === text).nodeId;
}

test("JSON-LD author names rescue the complete visible byline", () => {
  const page = parsePage(`<html><head><script type="application/ld+json">
    {"@type":"Article","author":[{"@type":"Person","name":"Ada Lovelace"},
    {"@type":"Person","name":"Grace Hopper"}]}</script></head>
    <body><article><section><div>Ada Lovelace</div><div>Grace Hopper</div></section>
    <p>Body text.</p></article></body></html>`);
  const partial = node(page, "div", "Ada Lovelace");
  const complete = node(page, "section", "Ada Lovelace Grace Hopper");
  assert.deepEqual(page.metadataAuthors, ["Ada Lovelace", "Grace Hopper"]);
  assert.equal(rescueAuthorNode(page, partial), complete);
  assert.equal(rescueAuthorNode(page, complete), complete);
});

test("meta author can rescue a missing author using the tightest child", () => {
  const page = parsePage(`<html><head><meta name="author" content="Davy Costa"></head>
    <body><article><span><a href="/author/davy-costa">Davy Costa</a></span></article></body></html>`);
  assert.equal(rescueAuthorNode(page, null), node(page, "a", "Davy Costa"));
});

test("organization and unmatched metadata do not override the model", () => {
  const page = parsePage(`<html><head><meta name="author" content="Thinking Machines Lab"></head>
    <body><article><p>By Ada Lovelace</p></article></body></html>`);
  const selected = node(page, "p", "By Ada Lovelace");
  assert.deepEqual(page.metadataAuthors, []);
  assert.equal(rescueAuthorNode(page, selected), selected);
});
