import { candidateTag, elementText } from "./dom.js";
import {
  ACKNOWLEDGEMENTS_RE, AUTHOR_ATTRIBUTE_RE, BYLINE_ATTRIBUTE_RE, BY_PREFIX_RE,
  CONTRIBUTOR_RE, DATE_LIKE_RE, MULTIPLE_NAME_RE, PROFILE_LINK_RE,
  PUBLISHED_MARKER_RE, READING_TIME_RE, RELATIVE_DATE_RE, UPDATED_MARKER_RE,
  WRITTEN_BY_RE, attributeSemanticTokens, attributeValues, personNameShape,
  textShapeTokens,
} from "./semantics.js";

const tag = (element) => element?.nodeType === 1 ? element.localName.toLowerCase() :
  element?.nodeType === 9 ? "[document]" : null;
const parent = (element) => element?.parentNode?.nodeType === 1 ? element.parentNode : null;
const length = (value) => [...value].length;
const wordsOf = (value) => value ? value.split(/\s+/) : [];
const log1p = Math.log1p;
const bool = (value) => value ? 1 : 0;
const rawAttr = (element, name) => element.getAttribute(name) ?? "";

function depth(element) {
  let result = 0;
  for (let current = element.parentNode; current; current = current.parentNode) result += 1;
  return result;
}

function inside(element, name) {
  for (let current = parent(element); current; current = parent(current)) {
    if (tag(current) === name) return true;
  }
  return false;
}

function ancestorDistance(left, right) {
  const seen = new Map();
  let distance = 0;
  for (let current = left; current; current = current.parentNode) seen.set(current, distance++);
  distance = 0;
  for (let current = right; current; current = current.parentNode) {
    if (seen.has(current)) return distance + seen.get(current);
    distance += 1;
  }
  return distance + seen.size;
}

function isDescendant(node, ancestor) {
  for (let current = parent(node); current; current = parent(current)) {
    if (current === ancestor) return true;
  }
  return false;
}

function directTextLength(element) {
  let result = 0;
  for (const child of element.childNodes) {
    if (child.nodeType === 3) result += length(child.nodeValue.trim());
  }
  return result;
}

function siblingTag(element, next) {
  let sibling = next ? element.nextSibling : element.previousSibling;
  while (sibling && sibling.nodeType !== 1) sibling = next ? sibling.nextSibling : sibling.previousSibling;
  return tag(sibling);
}

function titleAnchorIndex(candidates) {
  return candidates.findIndex(({ element }) => tag(element) === "h1" ||
    rawAttr(element, "itemprop").toLowerCase().includes("headline"));
}

function articleAnchorRange(candidates) {
  for (const name of ["article", "main"]) {
    const start = candidates.findIndex(({ element }) => tag(element) === name);
    if (start < 0) continue;
    let end = start;
    for (let index = start + 1; index < candidates.length; index += 1) {
      if (isDescendant(candidates[index].element, candidates[start].element)) end = index;
    }
    return [start, end];
  }
  return [-1, -1];
}

function dateAnchorIndices(candidates, texts) {
  const values = [];
  for (let index = 0; index < candidates.length; index += 1) {
    const element = candidates[index].element;
    const text = texts[index];
    const itemprop = rawAttr(element, "itemprop").toLowerCase();
    const readingOnly = READING_TIME_RE.test(text) && text.trim().match(READING_TIME_RE)?.[0] === text.trim();
    if (tag(element) === "time" || element.hasAttribute("datetime") ||
        itemprop === "datepublished" || itemprop === "datemodified" ||
        (wordsOf(text).length <= 20 && !readingOnly &&
          (DATE_LIKE_RE.test(text) || RELATIVE_DATE_RE.test(text)))) values.push(index);
  }
  return values;
}

function nearestAnchor(index, anchors) {
  if (anchors.length === 0) return -1;
  let best = anchors[0];
  for (const anchor of anchors) {
    if (Math.abs(anchor - index) < Math.abs(best - index)) best = anchor;
  }
  return best;
}

function rawNumeric(candidate, count, index, context) {
  const element = candidate.element;
  const text = context.texts[index];
  const textLength = length(text);
  const words = wordsOf(text);
  const local = textLength <= 500 && words.length <= 80;
  const descendants = context.descendants[index];
  const children = descendants.filter((item) => parent(item) === element);
  const links = descendants.filter((item) => tag(item) === "a");
  const semantics = [element, ...children]
    .flatMap((item) => attributeValues(item).map(([, value]) => value)).join(" ");
  const ownItemprop = rawAttr(element, "itemprop").toLowerCase();
  const capitalized = words.filter((word) => word && /\p{L}/u.test(word[0]) && word[0] === word[0].toUpperCase()).length;
  const denominator = Math.max(1, count - 1);
  const position = index / denominator;
  const titleIndex = context.titleIndex;
  const dateIndex = nearestAnchor(index, context.dateIndices);
  const title = titleIndex >= 0 ? context.candidates[titleIndex].element : null;
  const date = dateIndex >= 0 ? context.candidates[dateIndex].element : null;
  const [articleStart, articleEnd] = context.articleRange;
  const titleDistance = title ? Math.abs(index - titleIndex) / denominator : 1;
  const dateDistance = date ? Math.abs(index - dateIndex) / denominator : 1;
  const articleStartDistance = articleStart >= 0 ? Math.abs(index - articleStart) / denominator : 1;
  const articleEndDistance = articleEnd >= 0 ? Math.abs(index - articleEnd) / denominator : 1;
  const linkTextLength = (tag(element) === "a" ? length(text) : 0) +
    links.reduce((total, link) => total + length(elementText(link)), 0);
  return [
    log1p(textLength),
    log1p((tag(element) === "p" ? 1 : 0) + descendants.filter((item) => tag(item) === "p").length),
    textLength ? Math.min(1, linkTextLength / textLength) : 0,
    log1p(depth(element)),
    position,
    bool(element.hasAttribute("datetime")),
    bool(local && DATE_LIKE_RE.test(text)),
    bool(local && RELATIVE_DATE_RE.test(text)),
    log1p(words.length),
    capitalized / Math.max(1, words.length),
    log1p(links.length),
    bool(local && BY_PREFIX_RE.test(text)),
    bool(local && WRITTEN_BY_RE.test(text)),
    bool(AUTHOR_ATTRIBUTE_RE.test(semantics)),
    bool(BYLINE_ATTRIBUTE_RE.test(semantics)),
    bool(links.some((link) => PROFILE_LINK_RE.test(rawAttr(link, "href")))),
    bool(personNameShape(text, words)),
    bool(local && MULTIPLE_NAME_RE.test(text)),
    bool(words.length > 0 && words.length <= 20),
    bool(inside(element, "header")),
    bool(local && PUBLISHED_MARKER_RE.test(text)),
    bool(local && UPDATED_MARKER_RE.test(text)),
    bool(local && READING_TIME_RE.test(text)),
    bool(tag(element) === "time"),
    bool(ownItemprop === "datepublished"),
    bool(ownItemprop === "datemodified"),
    log1p(children.length),
    log1p(descendants.filter((item) => tag(item) === "span").length),
    bool(descendants.length === 0),
    directTextLength(element) / Math.max(1, textLength),
    bool(inside(element, "article")),
    bool(inside(element, "main")),
    titleDistance,
    bool(title && index < titleIndex),
    bool(title && parent(element) === parent(title)),
    title ? log1p(ancestorDistance(element, title)) : 0,
    dateDistance,
    bool(date && index < dateIndex),
    bool(date && parent(element) === parent(date)),
    date ? log1p(ancestorDistance(element, date)) : 0,
    bool(title && index > titleIndex),
    bool(title && Math.abs(index - titleIndex) <= Math.max(8, Math.floor(count * 0.03))),
    articleStartDistance,
    articleEndDistance,
    bool(articleStart >= 0 && index < articleStart),
    bool(articleEnd >= 0 && index > articleEnd),
    bool(position >= 0.9),
    bool(local && ACKNOWLEDGEMENTS_RE.test(text)),
    bool(local && CONTRIBUTOR_RE.test(text)),
  ];
}

function encodeTokens(tokens, count, lookup) {
  const values = tokens.slice(0, count).map((token) => lookup.get(token) ?? 1);
  while (values.length < count) values.push(0);
  return values;
}

export function featurizePage(page, model) {
  const candidates = page.candidates;
  const tagLookup = new Map(model.vocabulary.tags.map((name, index) => [name, index]));
  const semanticLookup = new Map(model.semanticVocabulary.tokens.map((name, index) => [name, index]));
  const encodeTag = (element) => tagLookup.get(tag(element)) ?? 1;
  const texts = candidates.map(({ element }) => elementText(element));
  const descendants = candidates.map(({ element }) => [...element.querySelectorAll("*")]);
  const context = {
    candidates, texts, descendants,
    titleIndex: titleAnchorIndex(candidates),
    articleRange: articleAnchorRange(candidates),
    dateIndices: dateAnchorIndices(candidates, texts),
  };
  const features = {
    nodeIds: candidates.map(({ nodeId }) => nodeId),
    tagIds: candidates.map(({ element }) => encodeTag(element)),
    parentTagIds: candidates.map(({ element }) => encodeTag(element.parentNode)),
    grandparentTagIds: candidates.map(({ element }) => encodeTag(element.parentNode?.parentNode)),
    previousTagIds: candidates.map(({ element }) => tagLookup.get(siblingTag(element, false)) ?? 1),
    nextTagIds: candidates.map(({ element }) => tagLookup.get(siblingTag(element, true)) ?? 1),
    attributeTokenIds: candidates.map(({ element }) => encodeTokens(attributeSemanticTokens(element), 8, semanticLookup)),
    textShapeTokenIds: candidates.map(({ element }, index) =>
      encodeTokens(textShapeTokens(element, texts[index], descendants[index].filter((item) => tag(item) === "a").length), 12, semanticLookup)),
    numeric: [],
  };
  for (let index = 0; index < candidates.length; index += 1) {
    const raw = rawNumeric(candidates[index], candidates.length, index, context);
    if (raw.length !== model.normalizer.mean.length) {
      throw new Error(`feature count ${raw.length} differs from checkpoint ${model.normalizer.mean.length}`);
    }
    features.numeric.push(raw.map((value, column) =>
      Math.fround((Math.fround(value) - Math.fround(model.normalizer.mean[column])) /
        Math.fround(model.normalizer.std[column]))));
  }
  return features;
}

export { isDescendant };
