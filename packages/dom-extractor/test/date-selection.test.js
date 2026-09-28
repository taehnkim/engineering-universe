import test from "node:test";
import assert from "node:assert/strict";

import { parsePage, elementText } from "../src/dom.js";
import { selectDateNode } from "../src/date-selection.js";

const model = { fields: ["date"] };
const scores = (page) => page.candidates.map(() => [0]);
const id = (page, tag, text) => page.candidates.find((candidate) =>
  candidate.element.localName === tag && elementText(candidate.element) === text)?.nodeId;

test("a date in the headline cannot beat the nearby publication date", () => {
  const related = Array.from({ length: 80 }, (_, index) => `<p>Paragraph ${index}.</p>`).join("");
  const page = parsePage(`<html><body><main><h1>Incident Report: July 22, 2026</h1>
    <span class="post-date">Aug 14, 2026</span><article><p>Details.</p>${related}
    <span class="post-date">Nov 11, 2025</span></article></main></body></html>`);
  const predictions = { title: id(page, "h1", "Incident Report: July 22, 2026"),
    date: id(page, "h1", "Incident Report: July 22, 2026") };
  assert.equal(selectDateNode(page, predictions, scores(page), model)?.nodeId,
    id(page, "span", "Aug 14, 2026"));
});

test("a nearby day-first date rescues a missing prediction", () => {
  const page = parsePage(`<html><body><main><h1>Cooking with data</h1>
    <p>Published on Monday, 23 September 2024</p><article><p>Body.</p></article>
    </main></body></html>`);
  assert.equal(selectDateNode(page, { title: id(page, "h1", "Cooking with data"), date: null },
    scores(page), model)?.parsed.iso, "2024-09-23");
});

test("an empty time node provides its datetime attribute", () => {
  const page = parsePage(`<html><body><h1>New release</h1><time datetime="2026-09-24"></time>
    <article><p>Body.</p></article></body></html>`);
  const timeId = page.candidates.find((candidate) => candidate.element.localName === "time")?.nodeId;
  assert.equal(selectDateNode(page, { title: id(page, "h1", "New release"), date: timeId },
    scores(page), model)?.parsed.iso, "2026-09-24");
});

test("a historical citation deep in the body is not a publication date", () => {
  const filler = Array.from({ length: 80 }, (_, index) => `<p>Paragraph ${index}.</p>`).join("");
  const page = parsePage(`<html><body><h1>Resource hints</h1><article>${filler}
    <p>W3C, accessed July 15, 2025</p></article></body></html>`);
  assert.equal(selectDateNode(page, { title: id(page, "h1", "Resource hints"), date: null },
    scores(page), model), null);
});

test("a nearby long narrative date is not treated as the post date", () => {
  const page = parsePage(`<html><body><h1>Incident details</h1><article>
    <p>On September 1, 2024, at 3:30PM EST, a new machine came up. Within seconds
    the fleet stopped accepting requests, and engineers started an investigation.</p>
    </article></body></html>`);
  assert.equal(selectDateNode(page, { title: id(page, "h1", "Incident details"), date: null },
    scores(page), model), null);
});

test("a date inside a code example is not treated as the post date", () => {
  const page = parsePage(`<html><body><h1>API headers</h1><article>
    <pre><code>Stripe-Version: 2026-06-24.preview</code></pre>
    </article></body></html>`);
  assert.equal(selectDateNode(page, { title: id(page, "h1", "API headers"), date: null },
    scores(page), model), null);
});
