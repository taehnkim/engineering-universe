import { mkdir, readFile, writeFile } from "node:fs/promises";
import { Resvg } from "@resvg/resvg-js";

const summary = JSON.parse(await readFile(new URL("results/summary.json", import.meta.url), "utf8"));
const sizes = JSON.parse(await readFile(new URL("results/sizes.json", import.meta.url), "utf8"));
const out = new URL("results/charts/", import.meta.url);
await mkdir(out, { recursive: true });

const names = ["dom-extractor", "readability", "trafilatura", "extractus", "newspaper"];
const display = {
  "dom-extractor": "DOM Extractor",
  readability: "Readability.js",
  trafilatura: "Trafilatura",
  extractus: "Extractus",
  newspaper: "Newspaper4k",
};
const palette = {
  background: "#f9f8f4", text: "#151718", muted: "#666965", rail: "#e8e8e3",
  grid: "#d1d2cd", primary: "#0875e8", peer: "#b4cfe7",
};
const font = "Menlo, SFMono-Regular, ui-monospace, monospace";
const xml = (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const text = (x, y, content, size = 22, color = palette.text, anchor = "start", weight = 400) =>
  `<text x="${x}" y="${y}" text-anchor="${anchor}" fill="${color}" ` +
  `font-family="${font}" font-size="${size}" font-weight="${weight}">${xml(content)}</text>`;
const rect = (x, y, width, height, fill) =>
  `<rect x="${x}" y="${y}" width="${width}" height="${height}" fill="${fill}"/>`;

function frame(width, height, title, subtitle) {
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">`,
    rect(0, 0, width, height, palette.background),
    text(70, 76, title, 34, palette.text, "start", 700),
    ...(subtitle ? [text(width - 70, 76, subtitle, 20, palette.muted, "end")] : []),
  ];
}

async function save(name, svg) {
  await writeFile(new URL(`${name}.svg`, out), svg);
  await writeFile(new URL(`${name}.png`, out), new Resvg(svg, {
    fitTo: { mode: "original" },
    font: { loadSystemFonts: true },
  }).render().asPng());
}

function horizontalBars({ title, subtitle, name, values, max, ticks, format, footnotes }) {
  const width = 1600;
  const height = 900;
  const left = 390;
  const right = 1450;
  const chartWidth = right - left;
  const top = 205;
  const gap = 83;
  const barHeight = 36;
  const parts = frame(width, height, title, subtitle);
  for (const tick of ticks) {
    const x = left + chartWidth * tick / max;
    parts.push(`<line x1="${x}" y1="165" x2="${x}" y2="${top + gap * 4 + barHeight}" stroke="${palette.grid}" stroke-width="1"/>`);
    parts.push(text(x, 145, format(tick), 18, palette.muted, "middle"));
  }
  values.forEach(({ key, value }, index) => {
    const y = top + index * gap;
    const own = key === "dom-extractor";
    parts.push(text(left - 25, y + 28, display[key], 22, palette.text, "end", own ? 700 : 400));
    parts.push(rect(left, y, chartWidth, barHeight, palette.rail));
    parts.push(rect(left, y, Math.max(4, chartWidth * value / max), barHeight,
      own ? palette.primary : palette.peer));
    parts.push(text(right + 22, y + 28, format(value), 21,
      own ? palette.text : palette.muted, "start", own ? 700 : 400));
  });
  footnotes.forEach((line, index) => parts.push(text(70, 690 + index * 34, line, 18, palette.muted)));
  parts.push(text(70, 855, name === "package-size"
    ? "Engineering Universe · measured on macOS arm64"
    : "Engineering Universe · same 659 saved HTML pages · no live fetches", 18, palette.muted));
  parts.push("</svg>");
  return save(name, parts.join(""));
}

const mb = (bytes) => bytes / 1_000_000;
const sizeFormat = (value) => value < 1 ? `${(value * 1000).toFixed(0)} kB` : `${value.toFixed(1)} MB`;
await horizontalBars({
  title: "Installed runtime footprint",
  subtitle: "package + required dependencies · lower is better",
  name: "package-size",
  values: names.map((key) => ({ key, value: mb(sizes.libraries[key].installedBytes) }))
    .sort((a, b) => a.value - b.value),
  max: 70,
  ticks: [0, 17.5, 35, 52.5, 70],
  format: sizeFormat,
  footnotes: [
    "Logical installed file bytes; Node/Python interpreters and standard libraries excluded.",
    `DOM Extractor includes parser + 29.6 kB weights; its compressed npm tarball is ${sizeFormat(mb(sizes.libraries["dom-extractor"].compressedTarballBytes))}.`,
    "Readability.js includes jsdom, as used for its Node extraction. Python bytecode excluded.",
  ],
});

const median = (key) => summary.libraries[key].latencyMs.median;
await horizontalBars({
  title: "Warm extraction latency",
  subtitle: "median milliseconds per full HTML page · lower is better",
  name: "latency",
  values: names.map((key) => ({ key, value: median(key) })).sort((a, b) => a.value - b.value),
  max: 70,
  ticks: [0, 17.5, 35, 52.5, 70],
  format: (value) => `${value.toFixed(1)} ms`,
  footnotes: [
    "Each library ran 659 pages in one warm, sequential process after 10 warm-up pages.",
    "Time includes HTML parsing, extraction, and conversion to field text; excludes file I/O/import.",
    `Node ${summary.protocol.node}; ${summary.protocol.cpu}. Python tools used separate uv environments.`,
  ],
});

async function accuracyChart(scope, title, subtitle, filename, footnotes) {
  const width = 1600;
  const height = 1080;
  const left = 395;
  const right = 1450;
  const chartWidth = right - left;
  const top = 215;
  const groupHeight = 196;
  const rowHeight = 29;
  const barHeight = 21;
  const parts = frame(width, height, title, "");
  parts.push(text(70, 119, subtitle, 20, palette.muted));
  for (const tick of [0, 25, 50, 75, 100]) {
    const x = left + chartWidth * tick / 100;
    parts.push(`<line x1="${x}" y1="177" x2="${x}" y2="${top + groupHeight * 3 + 5 * rowHeight + barHeight}" stroke="${palette.grid}" stroke-width="1"/>`);
    parts.push(text(x, 160, `${tick}%`, 18, palette.muted, "middle"));
  }
  const fields = ["title", "body", "date", "byline"];
  fields.forEach((field, group) => {
    const y0 = top + group * groupHeight;
    const count = summary.libraries["dom-extractor"].scopes[scope].fields[field].presentPages;
    parts.push(text(70, y0 + 23, field.toUpperCase(), 23, palette.text, "start", 700));
    parts.push(text(70, y0 + 49, `n=${count} present`, 17, palette.muted));
    names.forEach((key, index) => {
      const y = y0 + index * rowHeight;
      const own = key === "dom-extractor";
      const value = summary.libraries[key].scopes[scope].fields[field].accuracyAt90 * 100;
      parts.push(text(left - 23, y + 18, display[key], 18, palette.text, "end", own ? 700 : 400));
      parts.push(rect(left, y, chartWidth, barHeight, palette.rail));
      parts.push(rect(left, y, Math.max(2, chartWidth * value / 100), barHeight,
        own ? palette.primary : palette.peer));
      parts.push(text(right + 20, y + 18, `${value.toFixed(1)}%`, 18,
        own ? palette.text : palette.muted, "start", own ? 700 : 400));
    });
  });
  footnotes.forEach((line, index) => parts.push(text(70, 978 + index * 30, line, 17, palette.muted)));
  parts.push("</svg>");
  await save(filename, parts.join(""));
}

await accuracyChart("nontraining", "Field accuracy on non-training sites",
  "200 validation + 30 test pages · 9 sites · higher is better", "accuracy-nontraining", [
    "Accuracy = share of present labels with token F1 ≥ 0.90; same calendar dates count as matches.",
    "Byline prefixes ignored. Gold text comes from human-selected nodes in project-cleaned HTML.",
    "Validation sites informed model selection; the test set is one Meta site.",
  ]);
await accuracyChart("all", "Field accuracy on all reviewed pages",
  "659 pages · 27 sites · higher is better", "accuracy-all", [
    "429 training + 200 validation + 30 test pages; training pages are in-sample.",
    "Present-label text agreement (token F1 ≥ 0.90), not exact DOM-node accuracy.",
    "Gold text comes from human-selected nodes in project-cleaned HTML.",
  ]);
await accuracyChart("test", "Field accuracy on the held-out site",
  "30 Meta Engineering pages · one site only · higher is better", "accuracy-test", [
    "This test split has only one site; it does not establish performance on unseen sites generally.",
    "Present-label text agreement (token F1 ≥ 0.90), not exact DOM-node accuracy.",
    "Gold text comes from human-selected nodes in project-cleaned HTML.",
  ]);
console.log("Wrote five SVG and PNG charts to results/charts/");
