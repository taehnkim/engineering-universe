import { candidateTag, elementText, parentTag } from "./dom.js";

const DATE_LIKE_RE = /(?:\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b)|(?:\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}\b)/i;
const RELATIVE_DATE_RE = /\b(?:an?|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+(?:minute|hour|day|week|month|year)s?\s+ago\b/i;

function unicodeLength(value) {
  return [...value].length;
}

function depth(element) {
  let value = 0;
  let parent = element.parentNode;
  while (parent) {
    value += 1;
    parent = parent.parentNode;
  }
  return value;
}

function paragraphCount(element) {
  return (candidateTag({ element }) === "p" ? 1 : 0) + element.querySelectorAll("p").length;
}

function linkTextFraction(element, textLength) {
  if (textLength === 0) return 0;
  let linkLength = candidateTag({ element }) === "a" ? unicodeLength(elementText(element)) : 0;
  for (const link of element.querySelectorAll("a")) {
    linkLength += unicodeLength(elementText(link));
  }
  return Math.min(1, linkLength / textLength);
}

function encodeTag(tag, lookup) {
  return lookup.get(tag?.toLowerCase()) ?? 1;
}

export function featurizePage(page, model) {
  const lookup = new Map(model.vocabulary.tags.map((tag, index) => [tag, index]));
  const count = page.candidates.length;
  const tagIds = [];
  const parentTagIds = [];
  const numeric = [];
  for (const [index, candidate] of page.candidates.entries()) {
    const text = elementText(candidate.element);
    const textLength = unicodeLength(text);
    const raw = [
      Math.log1p(textLength),
      Math.log1p(paragraphCount(candidate.element)),
      linkTextFraction(candidate.element, textLength),
      Math.log1p(depth(candidate.element)),
      index / Math.max(1, count - 1),
      candidate.element.hasAttribute("datetime") ? 1 : 0,
      DATE_LIKE_RE.test(text) ? 1 : 0,
      RELATIVE_DATE_RE.test(text) ? 1 : 0,
    ];
    tagIds.push(encodeTag(candidateTag(candidate), lookup));
    parentTagIds.push(encodeTag(parentTag(candidate), lookup));
    numeric.push(
      raw.map(
        (value, featureIndex) =>
          (value - model.normalizer.mean[featureIndex]) /
          model.normalizer.std[featureIndex],
      ),
    );
  }
  return { tagIds, parentTagIds, numeric };
}
