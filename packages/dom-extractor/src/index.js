import { parseDocument } from "htmlparser2";
import { selectAll } from "css-select";
import { cssSelector, parsePage, readableText } from "./dom.js";
import { predict } from "./scoring.js";

export const modelVersion = "article-0.1.0";
const THRESHOLD = 0.5;
const MAX_INPUT_BYTES = 10 * 1024 * 1024;
const BATCH_SIZE = 100;
const FIELD_MAP = Object.freeze({ title: "title", body: "article", date: "date", byline: "authors" });
const INCLUDES = new Set(["html", "source", "debug"]);
let modelPromise;

function loadModel() {
  // Importing the package only loads the API. The weights are read once, on
  // the first valid extraction, and the same promise is reused by every call.
  modelPromise ??= import("./model.generated.js").then(({ default: model }) => model);
  return modelPromise;
}

export class ExtractError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "ExtractError";
    this.code = code;
  }
}

function requestedIncludes(options) {
  if (options === null || typeof options !== "object" || Array.isArray(options)) {
    throw new TypeError("options must be an object");
  }
  if (Object.keys(options).some((key) => key !== "include")) {
    throw new TypeError("the only supported option is include");
  }
  const values = options.include ?? [];
  if (!Array.isArray(values) || values.some((value) => !INCLUDES.has(value))) {
    throw new TypeError('include must contain only "html", "source", and "debug"');
  }
  return new Set(values);
}

function validateHtml(html) {
  if (typeof html !== "string") throw new ExtractError("invalidInput", "html must be a string");
  if (!html.trim()) throw new ExtractError("emptyInput", "html is empty");
  if (Buffer.byteLength(html, "utf8") > MAX_INPUT_BYTES) {
    throw new ExtractError("inputTooLarge", "html exceeds the 10 MB limit");
  }
}

function rounded(value) {
  return Number.isFinite(value) ? Math.round(Math.max(0, Math.min(1, value)) * 10_000) / 10_000 : 0;
}

function probability(scores, fieldIndex, candidateIndex, model) {
  if (candidateIndex < 0) return 0;
  // Presence is a binary choice between the best real node and "missing".
  // A softmax over every DOM node would make the score depend on page length.
  const difference = scores[candidateIndex][fieldIndex] - model.weights.missingScores[fieldIndex];
  return 1 / (1 + Math.exp(-difference));
}

function sourceNode(sourceDocument, selector) {
  const matches = selectAll(selector, sourceDocument);
  if (matches.length !== 1) throw new Error(`source selector did not uniquely match: ${selector}`);
  return matches[0];
}

function asExtractError(error, fallbackCode) {
  if (error instanceof ExtractError) return error;
  return new ExtractError(fallbackCode,
    `${fallbackCode === "internalError" ? "Unexpected error" : "Model error"}: ${error?.message ?? String(error)}`);
}

async function extractFields(html, includes) {
  validateHtml(html);
  let page;
  try {
    page = parsePage(html);
  } catch (error) {
    throw new ExtractError("parseError", `Could not parse HTML: ${error.message}`);
  }
  if (!page.document.documentElement || page.candidates.length === 0) {
    throw new ExtractError("parseError", "HTML has no usable DOM elements");
  }
  let model;
  try {
    model = await loadModel();
  } catch (error) {
    throw new ExtractError("inferenceError", `Model loading failed: ${error.message}`);
  }
  let prediction;
  try {
    prediction = predict(page, model);
  } catch (error) {
    throw new ExtractError("inferenceError", `Model scoring failed: ${error.message}`);
  }

  const sourceDocument = includes.has("html") ? parseDocument(html, {
    withStartIndices: true, withEndIndices: true,
  }) : null;
  const indexById = new Map(page.candidates.map((candidate, index) => [candidate.nodeId, index]));
  const fields = {};
  const rejected = {};
  for (const [publicField, modelField] of Object.entries(FIELD_MAP)) {
    const fieldIndex = model.fields.indexOf(modelField);
    let bestIndex = -1;
    for (let index = 0; index < page.candidates.length; index += 1) {
      if (bestIndex < 0 || prediction.scores[index][fieldIndex] > prediction.scores[bestIndex][fieldIndex]) {
        bestIndex = index;
      }
    }
    const selectedIndex = indexById.get(prediction.predictions[modelField]) ?? -1;
    const candidateIndex = selectedIndex >= 0 ? selectedIndex : bestIndex;
    // The author refiner can move from the base model's coarse byline wrapper
    // to a child. Its presence confidence comes from the best coarse candidate,
    // not the child's (often tiny) base-model softmax share.
    const confidence = rounded(probability(prediction.scores, fieldIndex, bestIndex, model));
    const candidate = candidateIndex < 0 ? null : page.candidates[candidateIndex];
    const rawText = candidate ? readableText(candidate.element) : "";
    const accepted = selectedIndex >= 0 && confidence >= THRESHOLD && rawText !== "";
    const field = { text: accepted ? rawText : null, confidence };
    if (includes.has("source") && candidate) {
      field.source = { selector: cssSelector(candidate.element, page) };
    }
    if (includes.has("html") && accepted) {
      const selector = cssSelector(candidate.element, page);
      const source = sourceNode(sourceDocument, selector);
      if (source.startIndex == null || source.endIndex == null) {
        throw new Error(`source span unavailable: ${selector}`);
      }
      field.html = html.slice(source.startIndex, source.endIndex + 1);
    }
    if (includes.has("debug") && !accepted && candidate) rejected[publicField] = { text: rawText };
    fields[publicField] = field;
  }
  const result = { fields };
  if (includes.has("debug")) result.debug = { rejected };
  return result;
}

/** Extract article fields from one full page of decoded HTML. */
export async function extract(html, options = {}) {
  const includes = requestedIncludes(options);
  try {
    return { modelVersion, ...await extractFields(html, includes) };
  } catch (error) {
    throw asExtractError(error, "internalError");
  }
}

/** Extract pages in input order; an invalid page does not stop the batch. */
export async function extractMany(htmls, options = {}) {
  const includes = requestedIncludes(options);
  if (!Array.isArray(htmls)) throw new ExtractError("invalidInput", "htmls must be an array of strings");
  const results = [];
  for (let start = 0; start < htmls.length; start += BATCH_SIZE) {
    for (const html of htmls.slice(start, start + BATCH_SIZE)) {
      try {
        results.push({ status: "ok", result: await extractFields(html, includes) });
      } catch (error) {
        const typed = asExtractError(error, "internalError");
        results.push({ status: "error", error: { code: typed.code, message: typed.message } });
      }
    }
  }
  return { modelVersion, results };
}
