import { parseHTML } from "linkedom";

export const DOM_CLEANUP_VERSION = "chrome-v2";

const EXCLUDED_SUBTREES = new Set([
  "head",
  "script",
  "style",
  "noscript",
  "template",
]);

const CHROME_DROP_TAGS = new Set([
  "aside",
  "audio",
  "button",
  "canvas",
  "dialog",
  "embed",
  "footer",
  "form",
  "head",
  "iframe",
  "img",
  "input",
  "nav",
  "noscript",
  "object",
  "picture",
  "script",
  "select",
  "source",
  "style",
  "svg",
  "template",
  "track",
  "video",
]);

const EMPTY_PRUNABLE_TAGS = new Set(["div", "figure", "p", "section", "span"]);
const CHROME_TOKENS = /\b(?:backdrop|breadcrumb|comments?|consent|cookie|drawer|footer|lightbox|menu|modal|newsletter|overlay|pagination|popup|promo|recommend(?:ation|ations|ed)?|related|share|sidebar|social|subscribe|toast)\b/i;
const EXTRACTION_TOKENS = /author|byline|date|publish|time|headline|title|subtitle|sub-title|subhead|standfirst|dek|excerpt|description|lead/i;
const HIDDEN_STYLE = /(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important)?\s*(?:;|$)/i;

function tagName(element) {
  return element.localName.toLowerCase();
}

function allElements(document) {
  if (!document.documentElement) return [];
  return [document.documentElement, ...document.documentElement.querySelectorAll("*")];
}

function normalizeSemanticName(value) {
  return value
    .replace(/([A-Z]+)([A-Z][a-z])/g, "$1 $2")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[^a-zA-Z0-9]+/g, " ")
    .trim()
    .toLowerCase();
}

function semanticValue(element) {
  const values = [tagName(element), element.getAttribute("id") ?? ""];
  for (const name of ["class", "itemprop", "role", "aria-label"]) {
    const value = element.getAttribute(name);
    if (value) values.push(value);
  }
  return values.map(normalizeSemanticName).join(" ");
}

function isHidden(element) {
  return (
    element.hasAttribute("hidden") ||
    element.getAttribute("aria-hidden")?.trim().toLowerCase() === "true" ||
    element.getAttribute("aria-modal")?.trim().toLowerCase() === "true" ||
    HIDDEN_STYLE.test(element.getAttribute("style") ?? "")
  );
}

function looksLikePageChrome(element) {
  const name = tagName(element);
  if (name === "html" || name === "body") return false;
  const role = (element.getAttribute("role") ?? "").toLowerCase();
  if (["alertdialog", "contentinfo", "dialog", "navigation", "search"].includes(role)) {
    return true;
  }
  if (isHidden(element)) return true;
  const semantics = semanticValue(element);
  if (EXTRACTION_TOKENS.test(semantics)) return false;
  return CHROME_TOKENS.test(semantics);
}

function hasExcludedAncestor(element) {
  let current = element;
  while (current?.nodeType === 1) {
    if (EXCLUDED_SUBTREES.has(tagName(current))) return true;
    current = current.parentNode;
  }
  return false;
}

function directTextParts(node, parts) {
  for (const child of node.childNodes) {
    if (child.nodeType === 3) {
      const value = child.nodeValue.trim();
      if (value) parts.push(value);
    } else if (child.nodeType === 1) {
      directTextParts(child, parts);
    }
  }
}

export function elementText(element) {
  const parts = [];
  directTextParts(element, parts);
  return parts.join(" ");
}

const TEXT_BLOCKS = new Set([
  "article", "blockquote", "div", "figcaption", "h1", "h2", "h3", "h4", "h5", "h6",
  "header", "li", "main", "ol", "p", "pre", "section", "table", "td", "th", "ul",
]);

export function readableText(element) {
  const parts = [];
  function walk(node) {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) parts.push(child.nodeValue.replace(/\s+/g, " "));
      else if (child.nodeType === 1) {
        const name = child.localName.toLowerCase();
        if (name === "br") { parts.push("\n"); continue; }
        const block = TEXT_BLOCKS.has(name);
        if (block) parts.push("\n\n");
        walk(child);
        if (block) parts.push("\n\n");
      }
    }
  }
  walk(element);
  return parts.join("").replace(/[ \t]*\n[ \t]*/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
}

function stripPageChrome(document) {
  for (const element of allElements(document)) {
    if (CHROME_DROP_TAGS.has(tagName(element))) element.remove();
  }
  for (const element of allElements(document)) {
    if (element.parentNode && looksLikePageChrome(element)) element.remove();
  }
  for (const element of allElements(document).reverse()) {
    if (
      element.parentNode &&
      EMPTY_PRUNABLE_TAGS.has(tagName(element)) &&
      element.children.length === 0 &&
      elementText(element) === "" &&
      !EXTRACTION_TOKENS.test(semanticValue(element))
    ) {
      element.remove();
    }
  }
}

export function parsePage(html) {
  const { document } = parseHTML(html);
  const originalCandidates = allElements(document).filter(
    (element) => !hasExcludedAncestor(element),
  );
  const originalIds = new WeakMap(
    originalCandidates.map((element, nodeId) => [element, nodeId]),
  );
  stripPageChrome(document);
  const candidates = allElements(document)
    .filter((element) => originalIds.has(element) && !hasExcludedAncestor(element))
    .map((element) => ({ nodeId: originalIds.get(element), element }));
  return { document, candidates };
}

export function selectedContent(candidate) {
  return {
    nodeId: candidate.nodeId,
    html: candidate.element.outerHTML,
    text: readableText(candidate.element),
  };
}

export function candidateTag(candidate) {
  return tagName(candidate.element);
}

export function parentTag(candidate) {
  const parent = candidate.element.parentNode;
  return parent?.nodeType === 1 ? tagName(parent) : "[document]";
}
