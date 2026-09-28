import test from "node:test";
import assert from "node:assert/strict";
import { parsePage, elementText } from "../src/dom.js";
import { rerankTitleWithMetadata } from "../src/scoring.js";

const model = { fields: ["title"] };

function candidateId(page, text, tag) {
  return page.candidates.find((candidate) => elementText(candidate.element) === text &&
    (!tag || candidate.element.localName === tag))?.nodeId;
}

test("page-title suffix supplies a hint to select a visible article heading", () => {
  const page = parsePage(`<html><head><title>A Precise Article Title — Site Brand</title></head>
    <body><h1>Site Brand</h1><article><h2>A Precise Article Title</h2></article></body></html>`);
  assert.deepEqual(page.titleHints, ["A Precise Article Title — Site Brand", "A Precise Article Title"]);
  const predictions = { title: candidateId(page, "Site Brand") };
  const scores = page.candidates.map((candidate) =>
    [candidate.nodeId === predictions.title ? 2 : 1]);
  rerankTitleWithMetadata(page, predictions, scores, model);
  assert.equal(predictions.title, candidateId(page, "A Precise Article Title", "h2"));
});

test("a weak metadata match cannot displace the model title", () => {
  const page = parsePage(`<html><head><meta property="og:title" content="Latest company news"></head>
    <body><h1>Engineering report</h1><article><h2>Unrelated story</h2></article></body></html>`);
  const predictions = { title: candidateId(page, "Engineering report") };
  rerankTitleWithMetadata(page, predictions, page.candidates.map(() => [1]), model);
  assert.equal(predictions.title, candidateId(page, "Engineering report"));
});
