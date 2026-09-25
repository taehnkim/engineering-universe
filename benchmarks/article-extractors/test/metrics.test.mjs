import assert from "node:assert/strict";
import test from "node:test";
import { canonicalDate, exactMatch, fieldScore, tokenF1 } from "../metrics.mjs";

test("body token F1 penalizes omitted and extra text", () => {
  assert.equal(tokenF1("one two", "one two", "body"), 1);
  assert.equal(tokenF1("one two", "one", "body"), 2 / 3);
  assert.equal(tokenF1("one", "one two", "body"), 2 / 3);
});

test("byline comparison ignores only an attribution prefix", () => {
  assert.equal(fieldScore("By: Ada Lovelace", "Ada Lovelace", "byline"), 1);
  assert.equal(exactMatch("Written by Ada Lovelace", "Ada Lovelace", "byline"), true);
  assert.ok(fieldScore("Ada Lovelace", "Grace Hopper", "byline") < 0.5);
});

test("date comparison credits ISO versus written dates and relative dates", () => {
  assert.equal(canonicalDate("Published May 25, 2026"), "2026-05-25");
  assert.equal(canonicalDate("2026-05-25T12:00:00Z"), "2026-05-25");
  assert.equal(canonicalDate("2 days ago", "2026-09-25T12:00:00Z"), "2026-09-23");
  assert.equal(fieldScore("May 25, 2026", "2026-05-25", "date"), 1);
  assert.equal(canonicalDate("5 min read"), null);
});
