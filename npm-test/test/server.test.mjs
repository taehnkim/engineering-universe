import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const appDir = dirname(dirname(fileURLToPath(import.meta.url)));

test("the vanilla UI server lists a reviewed page and runs the installed model", async () => {
  const fixture = await mkdtemp(join(tmpdir(), "npm-test-dataset-"));
  const pageId = "anthropic-engineering-001";
  const html = "<html><body><h1>Fixture title</h1><article><p>Fixture article.</p></article></body></html>";
  await Promise.all([
    mkdir(join(fixture, "html")),
    mkdir(join(fixture, "annotations")),
  ]);
  await writeFile(join(fixture, "html", `${pageId}.html`), html);
  await writeFile(join(fixture, "annotations", `${pageId}.json`), JSON.stringify({
    page_id: pageId,
    review_status: "reviewed",
    needs_review: false,
    labels: Object.fromEntries(["article", "title", "authors", "date"]
      .map((field) => [field, null])),
  }));
  await writeFile(join(fixture, "manifest.json"), JSON.stringify({ pages: [{
    page_id: pageId,
    company: "Fixture",
    website: "example.test",
    split: "test",
    url: "https://example.test/article",
    html_path: `html/${pageId}.html`,
    scraped_at: "2026-09-22T12:00:00Z",
  }] }));

  const server = spawn(process.execPath, [join(appDir, "server.mjs")], {
    cwd: appDir,
    env: { ...process.env, ANNOTATION_DATA_DIR: fixture, PORT: "0" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    const base = await new Promise((resolve, reject) => {
      const timeout = setTimeout(() => reject(new Error("server startup timed out")), 10_000);
      let output = "";
      server.once("error", reject);
      server.once("exit", (code) => reject(new Error(`server exited: ${code}`)));
      server.stdout.on("data", (chunk) => {
        output += String(chunk);
        const match = output.match(/http:\/\/127\.0\.0\.1:\d+\//);
        if (match) {
          clearTimeout(timeout);
          resolve(match[0].slice(0, -1));
        }
      });
    });
    const home = await fetch(base);
    assert.equal(home.status, 200);
    assert.match(await home.text(), /DOM extractor smoke test/);

    const listed = await (await fetch(`${base}/api/pages`)).json();
    assert.deepEqual(listed.pages.map((page) => page.id), [pageId]);
    const response = await fetch(`${base}/api/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pageId }),
    });
    assert.equal(response.status, 200);
    const result = await response.json();
    assert.equal(Object.keys(result.comparisons).length, 4);
    assert.equal(typeof result.comparisons.title.predicted, "number");
    assert.equal(result.articleText, "Fixture article.");
    assert.ok(result.inferenceMs >= 0);

    const raw = await fetch(`${base}/api/html/${pageId}`);
    assert.equal(raw.headers.get("content-type"), "text/plain; charset=utf-8");
    assert.equal(await raw.text(), html);
    const unknown = await fetch(`${base}/api/html/not-in-samples`);
    assert.equal(unknown.status, 404);
  } finally {
    server.kill();
    await rm(fixture, { recursive: true, force: true });
  }
});
