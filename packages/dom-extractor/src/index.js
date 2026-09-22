import model from "./model.generated.js";
import { DOM_CLEANUP_VERSION, parsePage, selectedContent } from "./dom.js";
import { featurizePage } from "./features.js";
import { derivePublishedAt, normalizeScrapedAt } from "./postprocess.js";

export const fields = Object.freeze([...model.fields]);

function linear(input, weight, bias) {
  return weight.map(
    (row, outputIndex) =>
      row.reduce((sum, value, inputIndex) => sum + value * input[inputIndex], 0) +
      bias[outputIndex],
  );
}

function relu(values) {
  return values.map((value) => Math.max(0, value));
}

function scoreCandidate(tagId, parentTagId, numeric) {
  const input = [
    ...model.weights.tagEmbedding[tagId],
    ...model.weights.tagEmbedding[parentTagId],
    ...numeric,
  ];
  const hidden0 = relu(
    linear(input, model.weights.linear0.weight, model.weights.linear0.bias),
  );
  const hidden1 = relu(
    linear(hidden0, model.weights.linear1.weight, model.weights.linear1.bias),
  );
  return linear(hidden1, model.weights.output.weight, model.weights.output.bias);
}

function predict(page) {
  if (page.candidates.length === 0) {
    return Object.fromEntries(fields.map((field) => [field, null]));
  }
  const features = featurizePage(page, model);
  const scores = page.candidates.map((_, index) =>
    scoreCandidate(
      features.tagIds[index],
      features.parentTagIds[index],
      features.numeric[index],
    ),
  );
  return Object.fromEntries(
    fields.map((field, fieldIndex) => {
      let bestIndex = page.candidates.length;
      let bestScore = model.weights.missingScores[fieldIndex];
      for (let index = 0; index < scores.length; index += 1) {
        if (scores[index][fieldIndex] > bestScore) {
          bestIndex = index;
          bestScore = scores[index][fieldIndex];
        }
      }
      return [field, bestIndex === page.candidates.length ? null : bestIndex];
    }),
  );
}

/**
 * Extract article content and metadata from raw HTML.
 *
 * @param {string} html
 * @param {{scrapedAt?: string | Date}} [options]
 */
export async function extract(html, options = {}) {
  if (typeof html !== "string") throw new TypeError("html must be a string");
  const page = parsePage(html);
  const selectedIndexes = predict(page);
  const selections = Object.fromEntries(
    fields.map((field) => {
      const index = selectedIndexes[field];
      return [field, index === null ? null : selectedContent(page.candidates[index])];
    }),
  );
  const scrapedAt = normalizeScrapedAt(options.scrapedAt ?? new Date());
  const result = {
    ...selections,
    scrapedAt,
    publishedAt: derivePublishedAt(
      selections.date?.text ?? null,
      selections.relative_date?.text ?? null,
      scrapedAt,
    ),
    predictions: Object.fromEntries(
      fields.map((field) => [
        field,
        selectedIndexes[field] === null
          ? null
          : page.candidates[selectedIndexes[field]].nodeId,
      ]),
    ),
    diagnostics: {
      candidateCount: page.candidates.length,
      cleanupVersion: DOM_CLEANUP_VERSION,
      modelVersion: model.modelVersion,
    },
  };
  return result;
}

/** Extract one field while using the same single-pass model prediction. */
export async function extractField(html, field) {
  if (!fields.includes(field)) throw new TypeError(`unknown field: ${field}`);
  return (await extract(html))[field];
}

export { extractRelativePublicationDate, resolveRelativeDate } from "./postprocess.js";
