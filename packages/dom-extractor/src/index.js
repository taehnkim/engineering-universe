import model from "./model.generated.js";
import { DOM_CLEANUP_VERSION, parsePage, selectedContent } from "./dom.js";
import { predict } from "./scoring.js";

export const fields = Object.freeze([...model.fields]);

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
  const page = await parsePage(html);
  const { predictions, scores } = predict(page, model);
  const byId = new Map(page.candidates.map((candidate) => [candidate.nodeId, candidate]));
  const selections = Object.fromEntries(fields.map((field) => {
    const id = predictions[field];
    return [field, id === null ? null : {
      ...selectedContent(byId.get(id)),
      confidence: selectedConfidence(field, id, page, scores),
    }];
  }));
  if (options.debug === true) {
    selections.debug = {
      candidateCount: page.candidates.length,
      cleanupVersion: DOM_CLEANUP_VERSION,
      featureVersion: model.featureVersion,
      modelVersion: model.modelVersion,
      checkpointSha256: model.checkpointSha256,
      domBackend: "javascript",
    };
  }
  return selections;
}

export async function extractField(html, field, options = {}) {
  if (!fields.includes(field)) throw new TypeError(`unknown field: ${field}`);
  return (await extract(html, options))[field];
}

export { extractRelativePublicationDate, resolveRelativeDate } from "./postprocess.js";
