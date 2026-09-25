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
function length(value) {
  if (!/[\uD800-\uDBFF]/.test(value)) return value.length;
  let count = 0;
  for (const _ of value) count += 1;
  return count;
}
const log1p = Math.log1p;
const wordsOf = (value) => value ? value.split(/\s+/) : [];
const bool = (value) => value ? 1 : 0;
const rawAttr = (element, name) => element.getAttribute(name) ?? "";

function treeDistance(left, right, subtrees) {
  let a = left;
  let b = right;
  let aDepth = subtrees.get(a).depth;
  let bDepth = subtrees.get(b).depth;
  let distance = 0;
  while (aDepth > bDepth) { a = parent(a); aDepth -= 1; distance += 1; }
  while (bDepth > aDepth) { b = parent(b); bDepth -= 1; distance += 1; }
  while (a !== b) { a = parent(a); b = parent(b); distance += 2; }
  return distance;
}

function fixedAnchorDistances(root, anchor) {
  const ancestorSteps = new WeakMap();
  let steps = 0;
  for (let node = anchor; node?.nodeType === 1; node = node.parentNode) {
    ancestorSteps.set(node, steps++);
  }
  const distances = new WeakMap();
  function visit(node, parentDistance) {
    const distance = ancestorSteps.has(node) ? ancestorSteps.get(node) : parentDistance + 1;
    distances.set(node, distance);
    for (const child of node.children) visit(child, distance);
  }
  visit(root, 0);
  return distances;
}

function isDescendant(node, ancestor) {
  for (let current = parent(node); current; current = parent(current)) {
    if (current === ancestor) return true;
  }
  return false;
}

function capitalizedCount(words) {
  let count = 0;
  for (const word of words) {
    const first = word[0];
    if (!first) continue;
    if (first >= "A" && first <= "Z") count += 1;
    else if (first > "z" && /\p{L}/u.test(first) && first === first.toUpperCase()) count += 1;
  }
  return count;
}

function subtreeStats(root) {
  const byElement = new WeakMap();
  function visit(element, depth, inHeader, inArticle, inMain) {
    const textParts = [];
    let directTextLength = 0;
    let links = 0;
    let paragraphs = 0;
    let spans = 0;
    let linkTextLength = 0;
    let profileLink = false;
    for (const child of element.childNodes) {
      if (child.nodeType === 3) {
        const value = child.nodeValue.trim();
        if (value) {
          textParts.push(value);
          directTextLength += length(value);
        }
      } else if (child.nodeType === 1) {
        const childStats = visit(child, depth + 1,
          inHeader || tag(element) === "header",
          inArticle || tag(element) === "article",
          inMain || tag(element) === "main");
        if (childStats.text) textParts.push(childStats.text);
        const childTag = tag(child);
        links += childStats.links + (childTag === "a" ? 1 : 0);
        paragraphs += childStats.paragraphs + (childTag === "p" ? 1 : 0);
        spans += childStats.spans + (childTag === "span" ? 1 : 0);
        linkTextLength += childStats.linkTextLength +
          (childTag === "a" ? length(childStats.text) : 0);
        profileLink ||= childStats.profileLink ||
          (childTag === "a" && PROFILE_LINK_RE.test(rawAttr(child, "href")));
      }
    }
    const stats = {
      text: textParts.join(" "), links, paragraphs, spans, linkTextLength, profileLink,
      directTextLength, depth, inHeader, inArticle, inMain,
    };
    byElement.set(element, stats);
    return stats;
  }
  if (root) visit(root, 1, false, false, false);
  return byElement;
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

function dateAnchorIndices(candidates, texts, wordLists) {
  const values = [];
  for (let index = 0; index < candidates.length; index += 1) {
    const element = candidates[index].element;
    const text = texts[index];
    const itemprop = rawAttr(element, "itemprop").toLowerCase();
    const readingOnly = READING_TIME_RE.test(text) && text.trim().match(READING_TIME_RE)?.[0] === text.trim();
    if (tag(element) === "time" || element.hasAttribute("datetime") ||
        itemprop === "datepublished" || itemprop === "datemodified" ||
        (wordLists[index].length <= 20 && !readingOnly &&
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
  const textLength = context.textLengths[index];
  const words = context.wordLists[index];
  const local = textLength <= 500 && words.length <= 80;
  const stats = context.subtrees.get(element);
  const name = context.tags[index];
  const children = [...element.children];
  const semantics = [element, ...children]
    .flatMap((item) => attributeValues(item).map(([, value]) => value)).join(" ");
  const ownItemprop = rawAttr(element, "itemprop").toLowerCase();
  const capitalized = context.capitalizedCounts[index];
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
  const linkTextLength = (name === "a" ? textLength : 0) + stats.linkTextLength;
  return [
    log1p(textLength),
    log1p((name === "p" ? 1 : 0) + stats.paragraphs),
    textLength ? Math.min(1, linkTextLength / textLength) : 0,
    log1p(stats.depth),
    position,
    bool(element.hasAttribute("datetime")),
    bool(local && DATE_LIKE_RE.test(text)),
    bool(local && RELATIVE_DATE_RE.test(text)),
    log1p(words.length),
    capitalized / Math.max(1, words.length),
    log1p(stats.links),
    bool(local && BY_PREFIX_RE.test(text)),
    bool(local && WRITTEN_BY_RE.test(text)),
    bool(AUTHOR_ATTRIBUTE_RE.test(semantics)),
    bool(BYLINE_ATTRIBUTE_RE.test(semantics)),
    bool(stats.profileLink),
    bool(personNameShape(text, words, capitalized)),
    bool(local && MULTIPLE_NAME_RE.test(text)),
    bool(words.length > 0 && words.length <= 20),
    bool(stats.inHeader),
    bool(local && PUBLISHED_MARKER_RE.test(text)),
    bool(local && UPDATED_MARKER_RE.test(text)),
    bool(local && READING_TIME_RE.test(text)),
    bool(name === "time"),
    bool(ownItemprop === "datepublished"),
    bool(ownItemprop === "datemodified"),
    log1p(children.length),
    log1p(stats.spans),
    bool(element.children.length === 0),
    stats.directTextLength / Math.max(1, textLength),
    bool(stats.inArticle),
    bool(stats.inMain),
    titleDistance,
    bool(title && index < titleIndex),
    bool(title && parent(element) === parent(title)),
    title ? log1p(context.titleDistances.get(element)) : 0,
    dateDistance,
    bool(date && index < dateIndex),
    bool(date && parent(element) === parent(date)),
    date ? log1p(treeDistance(element, date, context.subtrees)) : 0,
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
  const subtrees = subtreeStats(page.document.documentElement);
  page.subtreeStats = subtrees;
  const tagLookup = new Map(model.vocabulary.tags.map((name, index) => [name, index]));
  const semanticLookup = new Map(model.semanticVocabulary.tokens.map((name, index) => [name, index]));
  const encodeTag = (element) => tagLookup.get(tag(element)) ?? 1;
  const tags = candidates.map(({ element }) => tag(element));
  const texts = candidates.map(({ element }) => subtrees.get(element).text);
  const textLengths = texts.map(length);
  const wordLists = texts.map(wordsOf);
  const capitalizedCounts = wordLists.map(capitalizedCount);
  const titleIndex = titleAnchorIndex(candidates);
  const titleDistances = titleIndex < 0 ? null : fixedAnchorDistances(
    page.document.documentElement, candidates[titleIndex].element);
  const context = {
    candidates, tags, texts, textLengths, wordLists, capitalizedCounts, subtrees, titleDistances,
    titleIndex,
    articleRange: articleAnchorRange(candidates),
    dateIndices: dateAnchorIndices(candidates, texts, wordLists),
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
      encodeTokens(textShapeTokens(element, texts[index], subtrees.get(element).links,
        wordLists[index], textLengths[index], capitalizedCounts[index]), 12, semanticLookup)),
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
