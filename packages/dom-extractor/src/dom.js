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
  "header", "li", "main", "ol", "p", "pre", "section", "table", "td", "th", "tr", "ul",
]);

export function readableText(element) {
  const preformatted = (node) => node.localName === "pre" || node.localName === "textarea" ||
    /(?:^|;)\s*white-space\s*:\s*(?:pre|pre-wrap|break-spaces)\b/i.test(node.getAttribute("style") ?? "");
  if (preformatted(element)) return element.textContent;
  const parts = [];
  const preformattedParts = [];
  function walk(node) {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) parts.push(child.nodeValue.replace(/\s+/g, " "));
      else if (child.nodeType === 1) {
        const name = child.localName.toLowerCase();
        if (name === "br") { parts.push("\n"); continue; }
        if (preformatted(child)) {
          parts.push("\n\n", `\u0001${preformattedParts.length}\u0001`, "\n\n");
          preformattedParts.push(child.textContent);
          continue;
        }
        const block = TEXT_BLOCKS.has(name);
        if (block) parts.push("\n\n");
        walk(child);
        if (block) parts.push("\n\n");
      }
    }
  }
  walk(element);
  return parts.join("").replace(/[ \t]*\n[ \t]*/g, "\n")
    .replace(/\n{3,}/g, "\n\n").trim()
    .replace(/\u0001(\d+)\u0001/g, (_, index) => preformattedParts[Number(index)]);
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
  const originalPositions = new WeakMap();
  if (document.documentElement) originalPositions.set(document.documentElement, 1);
  for (const element of originalCandidates) {
    const siblingCounts = new Map();
    for (const child of element.children) {
      const name = tagName(child);
      const position = (siblingCounts.get(name) ?? 0) + 1;
      siblingCounts.set(name, position);
      originalPositions.set(child, position);
    }
  }
  stripPageChrome(document);
  const candidates = allElements(document)
    .filter((element) => originalIds.has(element) && !hasExcludedAncestor(element))
    .map((element) => ({ nodeId: originalIds.get(element), element }));
  return { document, candidates, originalPositions };
}

export function cssSelector(element, page) {
  const segments = [];
  for (let node = element; node?.nodeType === 1; node = node.parentElement) {
    segments.push(`${tagName(node)}:nth-of-type(${page.originalPositions.get(node)})`);
  }
  return segments.reverse().join(" > ");
}

export function candidateTag(candidate) {
  return tagName(candidate.element);
}

export function parentTag(candidate) {
  const parent = candidate.element.parentNode;
  return parent?.nodeType === 1 ? tagName(parent) : "[document]";
}
