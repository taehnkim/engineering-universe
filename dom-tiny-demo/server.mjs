import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { performance } from "node:perf_hooks";
import { extract } from "@eng-universe/dom-extractor";

const appDir = dirname(fileURLToPath(import.meta.url));
const defaultDataDir = resolve(appDir, "../data/learned_extraction/raw");
const dataDir = resolve(process.env.ANNOTATION_DATA_DIR ?? defaultDataDir);
const port = Number(process.env.PORT ?? 8770);
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;
const SAMPLE_IDS = [
  "anthropic-engineering-001",
  "airbnb-tech-001",
  "github-engineering-001",
  "google-developers-blog-012",
  "shopify-engineering-010",
  "stripe-com-blog-001",
  "stripe-dev-001",
  "nvidia-developer-blog-001",
  "ramp-builders-002",
  "fly-blog-006",
];

function json(response, status, value) {
  const body = JSON.stringify(value);
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  response.end(body);
}

async function loadSamples() {
  let manifestText;
  try {
    manifestText = await readFile(join(dataDir, "manifest.json"), "utf8");
  } catch (error) {
    if (error.code === "ENOENT") return [];
    throw error;
  }
  const manifest = JSON.parse(manifestText);
  const byId = new Map(manifest.pages.map((page) => [page.page_id, page]));
  const rows = [];
  for (const pageId of SAMPLE_IDS) {
    const page = byId.get(pageId);
    if (!page || !/^html\/[a-z0-9-]+\.html$/.test(page.html_path)) continue;
    let annotation;
    try {
      annotation = JSON.parse(await readFile(join(dataDir, "annotations", `${pageId}.json`), "utf8"));
      await stat(join(dataDir, page.html_path));
    } catch (error) {
      if (error.code === "ENOENT") continue;
      throw error;
    }
    if (annotation.review_status !== "reviewed" || annotation.needs_review) continue;
    rows.push({
      id: pageId,
      website: page.website,
      url: page.url,
      htmlPath: page.html_path,
    });
  }
  return rows;
}

async function readBody(request) {
  let body = "";
  for await (const chunk of request) {
    body += chunk;
    if (body.length > 4096) throw new Error("request body too large");
  }
  return JSON.parse(body || "{}");
}

async function readUploadedHtml(request) {
  const chunks = [];
  let size = 0;
  for await (const chunk of request) {
    size += chunk.length;
    if (size > MAX_UPLOAD_BYTES) {
      const error = new Error("HTML file exceeds the 10 MB limit");
      error.status = 413;
      throw error;
    }
    chunks.push(chunk);
  }
  if (!size) {
    const error = new Error("HTML file is empty");
    error.status = 400;
    throw error;
  }
  return Buffer.concat(chunks, size).toString("utf8");
}

async function runInference(html, debug = false) {
  const started = performance.now();
  const payload = await extract(html, { debug });
  return { inferenceMs: Number((performance.now() - started).toFixed(1)), payload };
}

async function createApp() {
  const samples = await loadSamples();
  const byId = new Map(samples.map((page) => [page.id, page]));
  const assets = new Map([
    ["/", ["index.html", "text/html; charset=utf-8"]],
    ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
    ["/style.css", ["style.css", "text/css; charset=utf-8"]],
  ]);
  return createServer(async (request, response) => {
    try {
      const url = new URL(request.url, "http://127.0.0.1");
      if (request.method === "GET" && assets.has(url.pathname)) {
        const [filename, type] = assets.get(url.pathname);
        const body = await readFile(join(appDir, filename));
        response.writeHead(200, {
          "Content-Type": type,
          "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'",
          "X-Content-Type-Options": "nosniff",
        });
        response.end(body);
        return;
      }
      if (request.method === "GET" && url.pathname === "/api/pages") {
        json(response, 200, { pages: samples.map(({ htmlPath, ...page }) => page) });
        return;
      }
      if (request.method === "GET" && url.pathname.startsWith("/api/html/")) {
        const page = byId.get(decodeURIComponent(url.pathname.slice("/api/html/".length)));
        if (!page) return json(response, 404, { error: "unknown page" });
        const html = await readFile(join(dataDir, page.htmlPath));
        response.writeHead(200, {
          "Content-Type": "text/plain; charset=utf-8",
          "X-Content-Type-Options": "nosniff",
          "Content-Security-Policy": "default-src 'none'",
        });
        response.end(html);
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/run") {
        const input = await readBody(request);
        const page = byId.get(input.pageId);
        if (!page) return json(response, 404, { error: "unknown page" });
        const html = await readFile(join(dataDir, page.htmlPath), "utf8");
        json(response, 200, { pageId: page.id, ...await runInference(html, input.debug === true) });
        return;
      }
      if (request.method === "POST" && url.pathname === "/api/run-upload") {
        if (!/^text\/html(?:;|$)/i.test(request.headers["content-type"] ?? "")) {
          return json(response, 415, { error: "upload must be an HTML file" });
        }
        const html = await readUploadedHtml(request);
        json(response, 200, await runInference(html, url.searchParams.get("debug") === "1"));
        return;
      }
      json(response, 404, { error: "not found" });
    } catch (error) {
      json(response, error.status ?? 500, { error: error.message });
    }
  });
}

const server = await createApp();
server.listen(port, "127.0.0.1", () => {
  console.log(`dom-tiny-demo ready at http://127.0.0.1:${server.address().port}/ (${dataDir})`);
});
