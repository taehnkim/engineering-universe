import { readFile } from "node:fs/promises";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import model from "../src/model.generated.js";
import { parsePage } from "../src/dom.js";
import { featurizePage } from "../src/features.js";
import { predict } from "../src/scoring.js";

const htmlPath = process.argv[2];
if (!htmlPath) throw new Error("usage: node scripts/audit_sample.mjs /absolute/page.html");
const root = fileURLToPath(new URL("../../../", import.meta.url));
const reference = spawnSync(process.env.PARITY_PYTHON ?? "python", [
  "packages/dom-extractor/scripts/python_parity.py", htmlPath, "--backend", "python",
], {
  cwd: root,
  env: { ...process.env, PYTHONPATH: root },
  encoding: "utf8",
  maxBuffer: 50 * 1024 * 1024,
});
if (reference.status !== 0) throw new Error(reference.stderr);
const expected = JSON.parse(reference.stdout);
const page = await parsePage(await readFile(htmlPath, "utf8"));
const actual = featurizePage(page, model);
for (const key of ["nodeIds", "tagIds", "parentTagIds", "grandparentTagIds",
  "previousTagIds", "nextTagIds", "attributeTokenIds", "textShapeTokenIds", "numeric"]) {
  const left = expected[key], right = actual[key];
  let changed = 0;
  let maxDifference = 0;
  const first = [];
  for (let row = 0; row < Math.min(left.length, right.length); row += 1) {
    const a = Array.isArray(left[row]) ? left[row] : [left[row]];
    const b = Array.isArray(right[row]) ? right[row] : [right[row]];
    if (a.length !== b.length) changed += 1;
    for (let column = 0; column < Math.min(a.length, b.length); column += 1) {
      const difference = Math.abs(a[column] - b[column]);
      if (difference > (key === "numeric" ? 0.0001 : 0)) {
        changed += 1;
        if (first.length < 5) first.push([row, column, a[column], b[column]]);
      }
      maxDifference = Math.max(maxDifference, difference);
    }
  }
  console.log(key, `rows ${left.length}/${right.length}`, `changed ${changed}`,
    `max diff ${maxDifference}`, first);
}
console.log("expected", expected.predictions);
console.log("actual", predict(page, model).predictions);
