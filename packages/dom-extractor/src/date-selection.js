import { elementText } from "./dom.js";
import { parsePublicationDate } from "./date-parser.js";

const DATE_ROLE = /date|publish|timestamp|\btime\b|(?:post|entry)[_-]*meta|postmetadata/i;
const PUBLICATION_MARKER = /\b(?:published|posted)\b/i;
const DATE_DATELINE = /^[A-Z][A-Z/\s]+[,\s–—-]+\d{1,2}\s+[A-Za-z]+\s+\d{4}\b/u;

function candidateText(page, element) {
  return page.subtreeStats?.get(element)?.text ?? elementText(element);
}

function parsedCandidate(page, candidate) {
  const element = candidate.element;
  const text = candidateText(page, element);
  const datetime = element.getAttribute("datetime") ?? "";
  if (text.length > 600 && !datetime) return null;
  // Visible calendar text takes precedence if an attribute and displayed date
  // disagree around a timezone boundary. Empty <time> nodes use datetime.
  const parsed = parsePublicationDate(text) ?? parsePublicationDate(datetime);
  return parsed ? { candidate, element, text, parsed } : null;
}

function depth(element) {
  let value = 0;
  for (let node = element; node?.nodeType === 1; node = node.parentElement) value += 1;
  return value;
}

/**
 * Keep a valid model date; otherwise use one unambiguous visible date near the
 * title or explicitly marked as a publication date. Never use a headline date.
 */
export function selectDateNode(page, predictions, scores, model) {
  const titleIndex = page.candidates.findIndex((candidate) => candidate.nodeId === predictions.title);
  const title = titleIndex < 0 ? null : page.candidates[titleIndex].element;
  const headline = title?.closest("h1, h2, h3") ?? null;
  const dateSignal = (element, text) => {
    const semantics = [element.getAttribute("class"), element.getAttribute("id"),
      element.getAttribute("itemprop"), element.parentElement?.getAttribute("class")].join(" ");
    return element.localName === "time" || element.hasAttribute("datetime") ||
      DATE_ROLE.test(semantics) || PUBLICATION_MARKER.test(text);
  };
  const insideUnmarkedHeadline = (element, text) => Boolean(headline &&
    headline.contains(element) && !dateSignal(element, text));
  const selected = page.candidates.find((candidate) => candidate.nodeId === predictions.date);
  const selectedParsed = selected ? parsedCandidate(page, selected) : null;
  if (selectedParsed && !insideUnmarkedHeadline(selected.element, selectedParsed.text)) {
    return { nodeId: selected.nodeId, parsed: selectedParsed.parsed, rescued: false };
  }

  const dateIndex = model.fields.indexOf("date");
  const groups = new Map();
  for (const [index, candidate] of page.candidates.entries()) {
    const element = candidate.element;
    const found = parsedCandidate(page, candidate);
    if (!found) continue;
    if (element.closest("pre, code, samp, kbd") || element.querySelector("pre, code")) continue;
    if (headline && element.contains(headline)) continue;
    if (insideUnmarkedHeadline(element, found.text)) continue;
    const strong = dateSignal(element, found.text);
    const near = titleIndex >= 0 && index >= titleIndex - 25 && index <= titleIndex + 25 &&
      (found.text.length <= 120 || DATE_DATELINE.test(found.text));
    if (!strong && !near) continue;
    const entry = { ...found, strong, near, score: scores[index][dateIndex], depth: depth(element) };
    const group = groups.get(found.parsed.iso) ?? [];
    group.push(entry);
    groups.set(found.parsed.iso, group);
  }
  if (!groups.size) return selected
    ? { nodeId: selected.nodeId, parsed: selectedParsed?.parsed ?? null, rescued: false } : null;
  let available = [...groups.values()];
  if (available.length > 1) {
    const nearStrong = available.filter((group) =>
      group.some((entry) => entry.near && entry.strong));
    const nearGroups = available.filter((group) => group.some((entry) => entry.near));
    if (nearStrong.length === 1) available = nearStrong;
    else if (nearGroups.length === 1) available = nearGroups;
    else available = available.filter((group) => group.some((entry) => entry.strong));
    if (available.length !== 1) return selected
      ? { nodeId: selected.nodeId, parsed: selectedParsed?.parsed ?? null, rescued: false } : null;
  }
  const best = available[0].sort((left, right) =>
    left.text.length - right.text.length || right.depth - left.depth || right.score - left.score)[0];
  return { nodeId: best.candidate.nodeId, parsed: best.parsed, rescued: true };
}
