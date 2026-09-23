import assert from "node:assert/strict";
import test from "node:test";

import { extract, fields } from "../src/index.js";

function random(seed) {
  let state = seed >>> 0;
  return () => ((state = (Math.imul(state, 1664525) + 1013904223) >>> 0) / 2 ** 32);
}

test("malformed and noisy HTML keeps the four-field API well formed", async () => {
  const next = random(1701);
  const atoms = [
    "<p>Article paragraph.</p>", "<p>By <a href='/author/ada'>Ada</a></p>",
    "<h1>Example title</h1>", "<time>September 20, 2026</time>",
    "<svg><path d='x'/></svg>", "<script>throw Error('ignore')</script>",
    "<footer>Footer</footer>", "<div role='dialog'>Popup</div>",
    "<p>Unicode: café — 東京 &amp; Berlin</p>", "<div><span>nested",
    "</div>", "<br>", "<!-- comment -->", "&lt;escaped&gt;",
  ];
  for (let index = 0; index < 200; index += 1) {
    const chunks = Array.from({ length: 4 + Math.floor(next() * 30) }, () =>
      atoms[Math.floor(next() * atoms.length)]);
    const html = `<html><body><nav>Chrome</nav><article>${chunks.join("")}</article></body></html>`;
    const result = await extract(html);
    assert.deepEqual(Object.keys(result), fields);
    for (const field of fields) {
      const selection = result[field];
      if (selection) {
        assert.deepEqual(Object.keys(selection), ["nodeId", "html", "text", "confidence"]);
        assert.ok(selection.confidence >= 0 && selection.confidence <= 1);
        assert.ok(Number.isFinite(selection.confidence));
      }
    }
  }
});
