import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

test("an installed package extracts HTML with no Python or Go on PATH", () => {
  const directory = mkdtempSync(join(tmpdir(), "dom-extractor-install-"));
  try {
    const packageRoot = new URL("../", import.meta.url).pathname;
    const archive = execFileSync("npm", ["pack", "--ignore-scripts", "--pack-destination", directory], {
      cwd: packageRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    }).trim().split("\n").at(-1);
    execFileSync("npm", ["install", "--offline", "--ignore-scripts", "--omit=dev",
      "--no-audit", "--no-fund", "--prefix", directory, join(directory, archive)], {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "pipe"],
    });
    const installed = join(directory, "node_modules", "@eng-universe", "dom-extractor");
    assert.ok(readdirSync(join(installed, "dist")).includes("model.weights.bin"));
    const schema = JSON.parse(readFileSync(join(installed, "schema.json"), "utf8"));
    assert.deepEqual(schema.required, ["modelVersion", "fields"]);
    const metadata = JSON.parse(readFileSync(join(installed, "package.json"), "utf8"));
    assert.equal(Object.keys(metadata.dependencies ?? {}).length, 0);

    const consumer = join(directory, "consumer.mjs");
    writeFileSync(consumer, `import { extract, extractMany, modelVersion } from "@eng-universe/dom-extractor";
const html = '<html><body><h1>Standalone test</h1><article><p>Real article text.</p></article></body></html>';
console.log(JSON.stringify({
  result: await extract(html),
  inspected: await extract(html, { include: ["html", "source", "debug"] }),
  batch: await extractMany([html, " "]),
  modelVersion,
}));\n`);
    const output = execFileSync(process.execPath, [consumer], {
      cwd: directory,
      env: { ...process.env, PATH: "" },
      encoding: "utf8",
    });
    const { result, inspected, batch, modelVersion } = JSON.parse(output);
    assert.equal(result.modelVersion, modelVersion);
    assert.equal(result.fields.title.text, "Standalone test");
    assert.equal(result.fields.body.text, "Real article text.");
    assert.deepEqual(Object.keys(result.fields), ["title", "body", "date", "byline"]);
    assert.equal(inspected.fields.title.html, "<h1>Standalone test</h1>");
    assert.equal(typeof inspected.fields.title.source.selector, "string");
    assert.ok("rejected" in inspected.debug);
    assert.equal(batch.results[1].error.code, "emptyInput");
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});
