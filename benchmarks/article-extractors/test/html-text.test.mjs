import assert from "node:assert/strict";
import test from "node:test";
import { textFromHtml } from "../html-text.mjs";

test("Extractus HTML adapter keeps article text and block boundaries", () => {
  assert.equal(textFromHtml("<article><p>First <em>part</em>.</p><p>Second.</p></article>"),
    "First part. Second.");
  assert.equal(textFromHtml(""), null);
});
