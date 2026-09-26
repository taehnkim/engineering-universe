import assert from "node:assert/strict";
import test from "node:test";

import { elementText, parsePage } from "../src/dom.js";

test("JS preparation keeps a hero-overlay h1 but removes popup headings", () => {
  const html = `<html><body><main>
    <div class="image-overlay"><h1>Real article title</h1></div>
    <div class="popup-overlay"><h1>Subscribe now</h1></div>
    <div class="image-overlay" role="dialog"><h1>Sign in</h1></div>
    <div class="image-overlay" aria-hidden="true"><h1>Hidden title</h1></div>
    <article><p>Article body</p></article>
  </main></body></html>`;
  const page = parsePage(html);
  const headings = page.candidates.filter((candidate) => candidate.element.localName === "h1");
  assert.deepEqual(headings.map((candidate) => elementText(candidate.element)), ["Real article title"]);
});
