import { elementText } from "./dom.js";
import { isDescendant } from "./features.js";
import {
  AUTHOR_LABEL_RE, ACKNOWLEDGEMENTS_RE, BY_PREFIX_RE, CONTRIBUTOR_RE,
  DATE_LIKE_RE, PROFILE_LINK_RE, READING_TIME_RE, RELATIVE_DATE_RE,
} from "./semantics.js";

const MAX_LOCAL_CANDIDATES = 192;
const TAGS = ["a", "p", "div", "span", "dl", "dd", "em", "strong", "section"];
const tag = (element) => element.localName.toLowerCase();
const length = (value) => [...value].length;
const bool = (value) => value ? 1 : 0;
const rawAttr = (element, name) => element.getAttribute(name) ?? "";

function wordCounts(text) {
  const counts = new Map();
  for (const word of text.toLocaleLowerCase().match(/[\p{L}\p{N}_]+/gu) ?? []) {
    counts.set(word, (counts.get(word) ?? 0) + 1);
  }
  return counts;
}

function wordCoverage(left, right) {
  let total = 0;
  let overlap = 0;
  for (const [word, count] of left) {
    total += count;
    overlap += Math.min(count, right.get(word) ?? 0);
  }
  return overlap / Math.max(1, total);
}

function profileLinks(element) {
  const links = tag(element) === "a" ? [element] : [...element.querySelectorAll("a")];
  return new Set(links.map((link) => rawAttr(link, "href"))
    .filter((href) => PROFILE_LINK_RE.test(href))
    .map((href) => href.split("?")[0])).size;
}

function localCandidates(page, seedId, authorScores) {
  const seed = page.candidates.find((candidate) => candidate.nodeId === seedId)?.element;
  if (!seed) throw new Error(`unknown author seed: ${seedId}`);
  const idByElement = new Map(page.candidates.map(({ nodeId, element }) => [element, nodeId]));
  const indexById = new Map(page.candidates.map(({ nodeId }, index) => [nodeId, index]));
  const ancestors = [seedId];
  let current = seed.parentNode;
  for (let depth = 0; depth < 5 && current?.nodeType === 1; depth += 1) {
    const id = idByElement.get(current);
    if (id !== undefined) ancestors.push(id);
    current = current.parentNode;
  }
  const descendants = [];
  const frontier = [[seed, 0]];
  for (const [element, depth] of frontier) {
    if (depth >= 7) continue;
    for (const child of element.children) {
      frontier.push([child, depth + 1]);
      const id = idByElement.get(child);
      if (id !== undefined && elementText(child).trim()) descendants.push([id, depth + 1]);
    }
  }
  if (ancestors.length + descendants.length > MAX_LOCAL_CANDIDATES) {
    descendants.sort((left, right) => {
      const a = [bool(left[1] <= 3), authorScores[indexById.get(left[0])], -left[1]];
      const b = [bool(right[1] <= 3), authorScores[indexById.get(right[0])], -right[1]];
      for (let index = 0; index < a.length; index += 1) {
        if (a[index] !== b[index]) return b[index] - a[index];
      }
      return 0;
    });
    descendants.length = Math.max(0, MAX_LOCAL_CANDIDATES - ancestors.length);
  }
  return [...new Set([...ancestors, ...descendants.map(([id]) => id)])];
}

function boundaryRow(page, nodeId, seedId, seed, seedWords, seedLinks,
                     seedScore, authorScores, numeric, indexById) {
  const index = indexById.get(nodeId);
  const element = page.candidates[index].element;
  const text = elementText(element);
  const words = wordCounts(text);
  const links = profileLinks(element);
  const ownAttrs = ["class", "id", "itemprop", "aria-label", "rel"]
    .map((name) => rawAttr(element, name)).join(" ").toLocaleLowerCase();
  const extra = [
    bool(nodeId === seedId),
    bool(nodeId !== seedId && isDescendant(seed, element)),
    bool(nodeId !== seedId && isDescendant(element, seed)),
    Math.max(-20, Math.min(20, authorScores[index] - seedScore)),
    Math.log1p(length(text)) - Math.log1p(length(elementText(seed))),
    wordCoverage(seedWords, words),
    wordCoverage(words, seedWords),
    bool(DATE_LIKE_RE.test(text)),
    bool(RELATIVE_DATE_RE.test(text)),
    bool(READING_TIME_RE.test(text)),
    bool(BY_PREFIX_RE.test(text)),
    bool(AUTHOR_LABEL_RE.test(text)),
    bool(/\bauthor\b|\bbyline\b/i.test(ownAttrs)),
    (text.match(/(?<!\w)@[\w.-]+/gu) ?? []).length,
    links,
    links - seedLinks,
    element.querySelectorAll("a").length,
    element.children.length,
    ...TAGS.map((name) => bool(tag(element) === name)),
  ];
  return [...numeric[index], ...extra];
}

function score(row, boundary) {
  const normalized = row.map((value, index) =>
    Math.max(-6, Math.min(6, (value - boundary.mean[index]) / boundary.std[index])));
  const hidden = boundary.linear0.weight.map((weights, output) => {
    let result = boundary.linear0.bias[output];
    for (let index = 0; index < weights.length; index += 1) result += weights[index] * normalized[index];
    return Math.max(0, result);
  });
  let result = boundary.output.bias[0];
  for (let index = 0; index < hidden.length; index += 1) {
    result += boundary.output.weight[0][index] * hidden[index];
  }
  return result;
}

export function refineAuthor(page, seedId, authorScores, numeric, boundary) {
  const ids = localCandidates(page, seedId, authorScores);
  const indexById = new Map(page.candidates.map(({ nodeId }, index) => [nodeId, index]));
  const seed = page.candidates[indexById.get(seedId)].element;
  const seedWords = wordCounts(elementText(seed));
  const seedLinks = profileLinks(seed);
  const seedScore = authorScores[indexById.get(seedId)];
  const scores = ids.map((nodeId) => score(boundaryRow(
    page, nodeId, seedId, seed, seedWords, seedLinks, seedScore,
    authorScores, numeric, indexById), boundary));
  const seedIndex = ids.indexOf(seedId);
  let bestIndex = 0;
  for (let index = 1; index < scores.length; index += 1) {
    if (scores[index] > scores[bestIndex]) bestIndex = index;
  }
  return scores[bestIndex] - scores[seedIndex] <= boundary.changeMargin
    ? seedId : ids[bestIndex];
}
