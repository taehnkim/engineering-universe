import test from "node:test";
import assert from "node:assert/strict";

import { parsePublicationDate } from "../src/date-parser.js";

test("parses publication dates inside day-first and mixed text", () => {
  assert.equal(parsePublicationDate("Published on Monday, 23 September 2024")?.iso, "2024-09-23");
  assert.equal(parsePublicationDate("LONDON, 22 September 2026 —")?.iso, "2026-09-22");
  assert.equal(parsePublicationDate("le 22/09/2026 par Marina Wiesel")?.iso, "2026-09-22");
  assert.equal(parsePublicationDate("le 12/08/2026 par Marina Wiesel")?.iso, "2026-08-12");
  assert.equal(parsePublicationDate("April 22, 2026 by Justina Bartulevičienė")?.iso,
    "2026-04-22");
  assert.equal(parsePublicationDate("Posted 9/29/23")?.iso, "2023-09-29");
});

test("parses datetime attributes and rejects reading times or invalid dates", () => {
  assert.equal(parsePublicationDate("2026-09-24T10:46:00+02:00")?.iso, "2026-09-24");
  assert.equal(parsePublicationDate("5 min read"), null);
  assert.equal(parsePublicationDate("31 February 2026"), null);
  assert.equal(parsePublicationDate("09/11/2026"), null);
});
