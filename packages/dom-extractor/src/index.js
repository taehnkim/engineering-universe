import model from "./model.generated.js";
import { cssSelector, DOM_CLEANUP_VERSION, parsePage, readableText, selectedContent } from "./dom.js";
import { predict } from "./scoring.js";

export const fields = Object.freeze([...model.fields]);
export const schemaVersion = "1.0.0";

function requestedVersion(options) {
  const version = options.version ?? (
    options.fields !== undefined || options.formats !== undefined || options.sourceUrl !== undefined
      ? schemaVersion : "legacy"
  );
  if (version !== "legacy" && version !== schemaVersion) {
    throw new TypeError(`unsupported output version: ${version}`);
  }
  return version;
}

function requestedFields(options) {
  if (options.fields === undefined) return fields;
  if (!Array.isArray(options.fields) || options.fields.some((field) => !fields.includes(field))) {
    throw new TypeError(`fields must be an array of: ${fields.join(", ")}`);
  }
  return [...new Set(options.fields)];
}

function requestedFormats(options) {
  if (options.formats === undefined) return ["text"];
  if (!Array.isArray(options.formats) || options.formats.some((format) =>
    format !== "text" && format !== "html")) {
    throw new TypeError('formats must contain only "text" and "html"');
  }
  return [...new Set(options.formats)];
}

function roundedConfidence(value) {
  return Math.round(value * 10_000) / 10_000;
}

const MONTHS = new Map([
  "january", "february", "march", "april", "may", "june",
  "july", "august", "september", "october", "november", "december",
].map((name, index) => [name, index + 1]));

function isoDate(year, month, day) {
  const date = new Date(Date.UTC(Number(year), Number(month) - 1, Number(day)));
  if (date.getUTCFullYear() !== Number(year) ||
      date.getUTCMonth() + 1 !== Number(month) ||
      date.getUTCDate() !== Number(day)) return null;
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function normalizedDate(candidate, raw) {
  const element = candidate.element;
  const time = element.localName === "time" ? element : element.querySelector("time[datetime]");
  const datetime = time?.getAttribute("datetime") ?? "";
  const structured = /^(\d{4})-(\d{2})-(\d{2})(?:$|T)/.exec(datetime);
  if (structured) {
    const iso = isoDate(structured[1], structured[2], structured[3]);
    if (iso) return iso;
  }
  const numeric = /\b(\d{4})-(\d{1,2})-(\d{1,2})\b/.exec(raw);
  if (numeric) {
    const iso = isoDate(numeric[1], numeric[2], numeric[3]);
    if (iso) return iso;
  }
  const written = /\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})(?:st|nd|rd|th)?[,]?[\s]+(\d{4})\b/i.exec(raw);
  if (written) {
    const iso = isoDate(written[3], MONTHS.get(written[1].toLowerCase()), written[2]);
    if (iso) return iso;
  }
  return raw;
}

function resolvedSourceUrl(value, page) {
  if (value === undefined) return page.sourceUrl;
  if (value === null) return null;
  if (typeof value !== "string") throw new TypeError("sourceUrl must be an absolute HTTP URL");
  try {
    const url = new URL(value);
    if (url.protocol === "http:" || url.protocol === "https:") return url.href;
  } catch {
    // The caller must supply the URL's origin.
  }
  throw new TypeError("sourceUrl must be an absolute HTTP URL");
}

function selectedConfidence(field, id, page, scores) {
  const fieldIndex = fields.indexOf(field);
  const logits = scores.map((row) => row[fieldIndex]);
  logits.push(model.weights.missingScores[fieldIndex]);
  const selectedIndex = id === null ? page.candidates.length
    : page.candidates.findIndex((candidate) => candidate.nodeId === id);
  const maximum = logits.reduce((best, value) => Math.max(best, value), -Infinity);
  const weights = logits.map((value) => Math.exp(value - maximum));
  return weights[selectedIndex] / weights.reduce((sum, value) => sum + value, 0);
}

/** Extract all fields with the bundled JavaScript DOM pipeline and checkpoint pair. */
export async function extract(html, options = {}) {
  if (typeof html !== "string") throw new TypeError("html must be a string");
  const version = requestedVersion(options);
  const includedFields = requestedFields(options);
  const formats = version === "legacy" ? ["text", "html"] : requestedFormats(options);
  const page = await parsePage(html);
  const { predictions, scores } = predict(page, model);
  const byId = new Map(page.candidates.map((candidate) => [candidate.nodeId, candidate]));
  const selections = Object.fromEntries(includedFields.map((field) => {
    const id = predictions[field];
    if (id === null) return [field, null];
    const confidence = selectedConfidence(field, id, page, scores);
    if (version === "legacy") {
      return [field, {
        ...selectedContent(byId.get(id)), confidence,
      }];
    }
    const candidate = byId.get(id);
    const selection = { id: id + 1 };
    if (formats.includes("text") || field === "authors") {
      const raw = readableText(candidate.element);
      selection.value = field === "date" ? normalizedDate(candidate, raw) : raw;
      if (field === "date") selection.raw = raw;
    }
    if (formats.includes("html")) selection.html = candidate.element.outerHTML;
    selection.selector = cssSelector(candidate.element, page);
    selection.confidence = roundedConfidence(confidence);
    return [field, selection];
  }));
  const result = version === "legacy" ? selections : {
    type: "article",
    schemaVersion,
    modelVersion: model.modelVersion,
    sourceUrl: resolvedSourceUrl(options.sourceUrl, page),
    fields: selections,
  };
  if (options.debug === true) {
    result.debug = {
      candidateCount: page.candidates.length,
      cleanupVersion: DOM_CLEANUP_VERSION,
      featureVersion: model.featureVersion,
      modelVersion: model.modelVersion,
      checkpointSha256: model.checkpointSha256,
      domBackend: "javascript",
    };
  }
  return result;
}

export async function extractField(html, field, options = {}) {
  if (!fields.includes(field)) throw new TypeError(`unknown field: ${field}`);
  const version = requestedVersion(options);
  const result = await extract(html, { ...options, version, fields: [field] });
  return version === "legacy" ? result[field] : result.fields[field];
}

export { extractRelativePublicationDate, resolveRelativeDate } from "./postprocess.js";
