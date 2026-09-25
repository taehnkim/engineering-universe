const FIELDS = ["article", "title", "authors", "date"];
const pagesElement = document.querySelector("#pages");
const runAllButton = document.querySelector("#run-all");
const uploadButton = document.querySelector("#upload-button");
const uploadInput = document.querySelector("#html-upload");
const includeDebugInput = document.querySelector("#include-debug");
const statusElement = document.querySelector("#status");
const totalsElement = document.querySelector("#totals");
const progressElement = document.querySelector("#progress");
const fieldDialog = document.querySelector("#field-dialog");
const fieldDialogTitle = document.querySelector("#field-dialog-title");
const fieldDialogMeta = document.querySelector("#field-dialog-meta");
const fieldDialogNote = document.querySelector("#field-dialog-note");
const fieldDialogOutput = document.querySelector("#field-dialog-output");
const textButton = document.querySelector("#field-dialog-show-text");
const htmlButton = document.querySelector("#field-dialog-show-html");
const renderedButton = document.querySelector("#field-dialog-show-rendered");
const renderedFrame = document.querySelector("#field-dialog-rendered");
const payloadDialog = document.querySelector("#payload-dialog");
const payloadDialogMeta = document.querySelector("#payload-dialog-meta");
const payloadDialogOutput = document.querySelector("#payload-dialog-output");
const infoTooltip = document.querySelector("#info-tooltip");
const cards = new Map();
const results = new Map();
const uploadedRawUrls = new Set();
let pages = [];
let runningAll = false;
let openSelection = null;
let uploadCount = 0;

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function updateTotals() {
  const completed = results.size;
  const timings = [...results.values()]
    .filter((result) => Number.isFinite(result?.inferenceMs))
    .map((result) => result.inferenceMs)
    .sort((a, b) => a - b);
  totalsElement.textContent = `${completed} / ${pages.length} pages`;
  progressElement.max = Math.max(1, pages.length);
  progressElement.value = completed;
  if (runningAll) return;
  if (!timings.length) {
    statusElement.textContent = completed ? "No successful inference runs"
      : pages.length ? "Starting inference…" : "Upload an HTML file to run inference";
    return;
  }
  const middle = Math.floor(timings.length / 2);
  const median = timings.length % 2 ? timings[middle] : (timings[middle - 1] + timings[middle]) / 2;
  const average = timings.reduce((sum, value) => sum + value, 0) / timings.length;
  const label = completed === pages.length ? "All pages complete" : `${completed} pages complete`;
  statusElement.textContent = `${label} · median ${median.toFixed(1)} ms / avg ${average.toFixed(1)} ms per page`;
}

function confidenceLabel(value) {
  return value == null ? "confidence unavailable" : `${value.toFixed(4)} confidence`;
}

function confidenceClass(value) {
  if (value == null) return "confidence missing";
  if (value > 0.8) return "confidence high";
  if (value >= 0.5) return "confidence medium";
  return "confidence low";
}

function setPayloadAvailable(link, available) {
  if (available) {
    link.href = "#payload";
    link.removeAttribute("aria-disabled");
    link.tabIndex = 0;
  } else {
    link.removeAttribute("href");
    link.setAttribute("aria-disabled", "true");
    link.tabIndex = -1;
  }
}

function snippet(value) {
  const text = (value ?? "").replace(/\s+/g, " ").trim();
  return text.length > 240 ? `${text.slice(0, 239)}…` : text;
}

function hideTooltip() {
  infoTooltip.hidden = true;
}

function showTooltip(target, message) {
  infoTooltip.textContent = message;
  infoTooltip.hidden = false;
  const targetBox = target.getBoundingClientRect();
  const tipBox = infoTooltip.getBoundingClientRect();
  infoTooltip.style.left = `${Math.max(8, Math.min(targetBox.left, window.innerWidth - tipBox.width - 8))}px`;
  const below = targetBox.bottom + 8;
  infoTooltip.style.top = `${below + tipBox.height <= window.innerHeight
    ? below : Math.max(8, targetBox.top - tipBox.height - 8)}px`;
}

function infoTerm(label, message) {
  const term = element("span", "info-term", label);
  term.tabIndex = 0;
  term.setAttribute("aria-describedby", "info-tooltip");
  term.addEventListener("pointerenter", () => showTooltip(term, message));
  term.addEventListener("pointerleave", hideTooltip);
  term.addEventListener("focus", () => showTooltip(term, message));
  term.addEventListener("blur", hideTooltip);
  term.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideTooltip();
  });
  return term;
}

window.addEventListener("scroll", hideTooltip, true);
window.addEventListener("resize", hideTooltip);

function showFormat(format) {
  const isText = format === "text";
  const isRendered = format === "rendered";
  for (const [button, buttonFormat] of [[textButton, "text"], [htmlButton, "html"], [renderedButton, "rendered"]]) {
    const selected = format === buttonFormat;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  fieldDialogOutput.hidden = isRendered;
  renderedFrame.hidden = !isRendered;
  if (isRendered) {
    const html = openSelection?.html ?? "<p>No content selected.</p>";
    renderedFrame.srcdoc = `<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; connect-src 'none'; img-src 'none'; media-src 'none'; style-src 'unsafe-inline'; font-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'"><style>body{margin:18px;overflow-wrap:anywhere}a,button,input,form{pointer-events:none}</style></head><body>${html}</body></html>`;
    fieldDialogNote.textContent = "Browser-rendered markup in an isolated frame. Scripts and external resources are blocked; site styles are not loaded.";
    return;
  }
  renderedFrame.srcdoc = "";
  const value = openSelection?.[isText ? "value" : format];
  fieldDialogOutput.textContent = value ?? "No content selected.";
  fieldDialogOutput.classList.toggle("html-source", format === "html");
  fieldDialogNote.textContent = isText
    ? "Full extracted text. Article paragraphs retain their line breaks."
    : "Selected-node markup, shown as text. npm and Python can serialize the same node differently.";
}

function showField(pageId, field, result) {
  openSelection = result.payload.fields[field];
  fieldDialogTitle.textContent = `${pageId} · ${field}`;
  fieldDialogMeta.replaceChildren(
    `Selected node ${openSelection?.id ?? "missing"} · `,
    element("span", confidenceClass(openSelection?.confidence), confidenceLabel(openSelection?.confidence)),
  );
  showFormat("text");
  fieldDialog.showModal();
}

function showPayload(pageId, result) {
  payloadDialogMeta.textContent = pageId;
  payloadDialogOutput.replaceChildren();
  const source = JSON.stringify(result.payload, null, 2);
  const token = /"(?:\\.|[^"\\])*"|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?|\b(?:true|false|null)\b/g;
  let cursor = 0;
  for (const match of source.matchAll(token)) {
    payloadDialogOutput.append(document.createTextNode(source.slice(cursor, match.index)));
    const value = match[0];
    const kind = value.startsWith('"')
      ? (source.slice(match.index + value.length).trimStart().startsWith(":") ? "key" : "string")
      : /^(?:true|false|null)$/.test(value) ? "literal" : "number";
    payloadDialogOutput.append(element("span", `json-${kind}`, value));
    cursor = match.index + value.length;
  }
  payloadDialogOutput.append(document.createTextNode(source.slice(cursor)));
  payloadDialog.showModal();
}

textButton.addEventListener("click", () => showFormat("text"));
htmlButton.addEventListener("click", () => showFormat("html"));
renderedButton.addEventListener("click", () => showFormat("rendered"));
document.querySelector("#field-dialog-close").addEventListener("click", () => fieldDialog.close());
fieldDialog.addEventListener("click", (event) => {
  if (event.target === fieldDialog) fieldDialog.close();
});
fieldDialog.addEventListener("close", () => { renderedFrame.srcdoc = ""; });
document.querySelector("#payload-dialog-close").addEventListener("click", () => payloadDialog.close());
payloadDialog.addEventListener("click", (event) => {
  if (event.target === payloadDialog) payloadDialog.close();
});

function renderResult(pageId, result) {
  const { output, page, latency } = cards.get(pageId);
  hideTooltip();
  output.replaceChildren();
  if (result.error) {
    latency.hidden = true;
    output.append(element("div", "error", result.error));
    return;
  }
  latency.textContent = `${result.inferenceMs.toFixed(1)} ms`;
  latency.hidden = false;
  if (result.payload.debug) {
    const summary = element("div", "result-summary");
    summary.append(infoTerm(`${result.payload.debug.candidateCount} candidates`,
      "HTML elements the model considered for this page."));
    summary.append(" · ");
    summary.append(infoTerm(result.payload.debug.modelVersion,
      "The trained model and author-refinement version used for this run."));
    output.append(summary);
  }
  const grid = element("div", "fields");
  for (const field of FIELDS) {
    const selection = result.payload.fields[field];
    const card = element("div", "field");
    const top = element("div", "field-top");
    top.append(element("span", "", field));
    top.append(element("span", confidenceClass(selection?.confidence), confidenceLabel(selection?.confidence)));
    card.append(top);
    card.append(element("div", "nodes", `node ${selection?.id ?? "missing"}`));
    const preview = snippet(selection?.value);
    card.append(element("p", `snippet${preview ? "" : " muted"}`,
      preview || "No content selected"));
    card.classList.add("openable");
    card.tabIndex = 0;
    card.setAttribute("role", "button");
    card.setAttribute("aria-haspopup", "dialog");
    card.setAttribute("aria-label", `Inspect predicted ${field} text and HTML for ${page.displayName ?? pageId}`);
    card.title = `Inspect full ${field} text and HTML`;
    card.addEventListener("click", () => showField(page.displayName ?? pageId, field, result));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        showField(page.displayName ?? pageId, field, result);
      }
    });
    grid.append(card);
  }
  output.append(grid);
}

async function runPage(page) {
  const { button, payloadLink, output, latency } = cards.get(page.id);
  button.disabled = true;
  setPayloadAvailable(payloadLink, false);
  latency.hidden = true;
  button.textContent = "Running…";
  results.delete(page.id);
  updateTotals();
  output.hidden = false;
  output.replaceChildren(element("div", "muted", "Running inference…"));
  try {
    const response = page.uploadedHtml === undefined
      ? await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pageId: page.id, debug: includeDebugInput.checked }),
      })
      : await fetch(`/api/run-upload${includeDebugInput.checked ? "?debug=1" : ""}`, {
        method: "POST",
        headers: { "Content-Type": "text/html; charset=utf-8" },
        body: page.uploadedHtml,
      });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error ?? `HTTP ${response.status}`);
    results.set(page.id, result);
    setPayloadAvailable(payloadLink, true);
    renderResult(page.id, result);
  } catch (error) {
    results.set(page.id, null);
    renderResult(page.id, { error: error.message });
  } finally {
    button.disabled = runningAll;
    button.textContent = "Run";
    updateTotals();
  }
}

function renderPage(page, atTop = false) {
  const card = element("article", "page");
  const head = element("div", "page-head");
  const main = element("div", "page-main");
  const titleRow = element("div", "page-title-row");
  titleRow.append(element("h2", "page-title", page.displayName ?? page.id));
  const latency = element("span", "inference-latency");
  latency.hidden = true;
  titleRow.append(latency);
  main.append(titleRow);
  main.append(element("div", "meta", page.website));
  head.append(main);
  const actions = element("div", "actions");
  const payloadLink = element("a", "", "Payload");
  setPayloadAvailable(payloadLink, false);
  payloadLink.addEventListener("click", (event) => {
    event.preventDefault();
    const result = results.get(page.id);
    if (result) showPayload(page.displayName ?? page.id, result);
  });
  actions.append(payloadLink);
  const rawLink = element("a", "", "Raw HTML");
  rawLink.href = page.rawUrl ?? `/api/html/${encodeURIComponent(page.id)}`;
  rawLink.target = "_blank";
  rawLink.rel = "noopener noreferrer";
  actions.append(rawLink);
  const sourceLink = element("a", "", "Source");
  if (/^https?:\/\//.test(page.url)) {
    sourceLink.href = page.url;
    sourceLink.target = "_blank";
    sourceLink.rel = "noopener noreferrer";
    actions.append(sourceLink);
  }
  const button = element("button", "", "Run");
  button.type = "button";
  button.addEventListener("click", () => runPage(page));
  actions.append(button);
  head.append(actions);
  card.append(head);
  const output = element("div", "result");
  output.hidden = true;
  card.append(output);
  if (atTop) pagesElement.prepend(card);
  else pagesElement.append(card);
  cards.set(page.id, { page, button, payloadLink, output, latency });
}

function renderPages() {
  pagesElement.replaceChildren();
  cards.clear();
  for (const page of pages) {
    renderPage(page);
  }
}

uploadButton.addEventListener("click", () => uploadInput.click());
uploadInput.addEventListener("change", async () => {
  const file = uploadInput.files?.[0];
  uploadInput.value = "";
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) {
    statusElement.textContent = "HTML file exceeds the 10 MB limit";
    return;
  }
  uploadButton.disabled = true;
  uploadButton.textContent = "Loading…";
  try {
    const html = await file.text();
    if (!html) throw new Error("HTML file is empty");
    const rawUrl = URL.createObjectURL(new Blob([html], { type: "text/plain" }));
    uploadedRawUrls.add(rawUrl);
    const page = {
      id: `upload-${++uploadCount}`,
      displayName: file.name,
      website: "Uploaded HTML",
      uploadedHtml: html,
      rawUrl,
    };
    pages.unshift(page);
    renderPage(page, true);
    runAllButton.textContent = `Run all ${pages.length}`;
    updateTotals();
    await runPage(page);
  } catch (error) {
    statusElement.textContent = error.message;
  } finally {
    uploadButton.disabled = runningAll;
    uploadButton.textContent = "Upload HTML";
  }
});
window.addEventListener("pagehide", () => {
  for (const rawUrl of uploadedRawUrls) URL.revokeObjectURL(rawUrl);
});

async function runAll() {
  if (runningAll) return;
  runningAll = true;
  runAllButton.disabled = true;
  uploadButton.disabled = true;
  results.clear();
  for (const { button, payloadLink, output, latency } of cards.values()) {
    button.disabled = true;
    setPayloadAvailable(payloadLink, false);
    latency.hidden = true;
    output.hidden = true;
  }
  updateTotals();
  try {
    for (const [index, page] of pages.entries()) {
      statusElement.textContent = `Running ${index + 1} of ${pages.length}: ${page.id}`;
      await runPage(page);
    }
  } finally {
    runningAll = false;
    runAllButton.disabled = false;
    uploadButton.disabled = false;
    for (const { button } of cards.values()) button.disabled = false;
    updateTotals();
  }
}

runAllButton.addEventListener("click", runAll);

try {
  const response = await fetch("/api/pages");
  if (!response.ok) throw new Error(`Could not load samples: HTTP ${response.status}`);
  pages = (await response.json()).pages;
  renderPages();
  updateTotals();
  runAllButton.textContent = `Run all ${pages.length}`;
  runAllButton.disabled = pages.length === 0;
  if (pages.length) void runAll();
} catch (error) {
  statusElement.textContent = error.message;
  statusElement.classList.add("error");
}
