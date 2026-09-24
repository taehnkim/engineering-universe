import model from "./model.generated.js";
import { DOM_CLEANUP_VERSION, parsePage, selectedContent } from "./dom.js";
import { predict } from "./scoring.js";
import { derivePublishedAt, normalizeScrapedAt } from "./postprocess.js";

export const fields = Object.freeze([...model.fields]);

/** Extract all fields with the bundled JavaScript DOM pipeline and checkpoint pair. */
export async function extract(html, options = {}) {
  if (typeof html !== "string") throw new TypeError("html must be a string");
  const page = await parsePage(html);
  const { predictions } = predict(page, model);
  const byId = new Map(page.candidates.map((candidate) => [candidate.nodeId, candidate]));
  const selections = Object.fromEntries(fields.map((field) => {
    const id = predictions[field];
    return [field, id === null ? null : selectedContent(byId.get(id))];
  }));
  const scrapedAt = normalizeScrapedAt(options.scrapedAt ?? new Date());
  return {
    ...selections,
    scrapedAt,
    publishedAt: derivePublishedAt(
      selections.date?.text ?? null,
      selections.relative_date?.text ?? null,
      scrapedAt,
    ),
    predictions,
    diagnostics: {
      candidateCount: page.candidates.length,
      cleanupVersion: DOM_CLEANUP_VERSION,
      featureVersion: model.featureVersion,
      modelVersion: model.modelVersion,
      checkpointSha256: model.checkpointSha256,
      domBackend: "javascript",
    },
  };
}

export async function extractField(html, field, options = {}) {
  if (!fields.includes(field)) throw new TypeError(`unknown field: ${field}`);
  return (await extract(html, options))[field];
}

export { extractRelativePublicationDate, resolveRelativeDate } from "./postprocess.js";
