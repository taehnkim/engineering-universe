import { readFile } from "node:fs/promises";

import { extract } from "../src/index.js";

const html = await readFile(new URL("./sample.html", import.meta.url), "utf8");
const result = await extract(html, {
  scrapedAt: "2026-09-22T12:00:00Z",
});

console.log(JSON.stringify(result, null, 2));
