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

const ARTICLE_TYPES = new Set(["Article", "BlogPosting", "NewsArticle", "TechArticle"]);
const AUTHOR_META_KEYS = new Set(["author", "article:author", "dc.creator", "parsely-author"]);
const ORGANIZATION_NAME = /\b(?:lab|team|inc|ltd|company|corporation)\b/i;

function normalizedAuthorText(value) {
  return (value.toLocaleLowerCase().match(/[\p{L}\p{N}_]+/gu) ?? []).join(" ");
}

function jsonAuthors(value, depth = 0) {
  if (depth > 20) return [];
  if (Array.isArray(value)) return value.flatMap((item) => jsonAuthors(item, depth + 1));
  if (value === null || typeof value !== "object") return [];
  const names = [];
  const kinds = Array.isArray(value["@type"]) ? value["@type"] : [value["@type"]];
  if (kinds.some((kind) => ARTICLE_TYPES.has(kind))) {
    const entries = Array.isArray(value.author) ? value.author : [value.author];
    for (const entry of entries) {
      if (typeof entry === "string") names.push(entry);
      else if (typeof entry?.name === "string") names.push(entry.name);
    }
  }
  for (const child of Object.values(value)) names.push(...jsonAuthors(child, depth + 1));
  return names;
}

function metadataAuthorNames(document, html) {
  const headEnd = /<\/head\s*>/i.exec(html.slice(0, 128_000));
  if (!headEnd) return [];
  const names = [];
  if (!document.head) return [];
  for (const tag of document.head.querySelectorAll("meta")) {
    const key = (tag.getAttribute("name") ?? tag.getAttribute("property") ?? "").toLowerCase();
    if (AUTHOR_META_KEYS.has(key)) names.push(tag.getAttribute("content") ?? "");
  }
  for (const script of document.head.querySelectorAll('script[type="application/ld+json"]')) {
    try { names.push(...jsonAuthors(JSON.parse(script.textContent))); }
    catch (error) { if (!(error instanceof SyntaxError)) throw error; }
  }
  return [...new Set(names.map((name) => name.trim()).filter((name) =>
    normalizedAuthorText(name).split(" ").length >= 2 && !ORGANIZATION_NAME.test(name)))];
}

export function rescueAuthorNode(page, selectedId) {
  if (!page.metadataAuthors.length) return selectedId;
  const names = page.metadataAuthors.map(normalizedAuthorText);
  const selected = page.candidates.find((candidate) => candidate.nodeId === selectedId);
  if (selected && names.every((name) => normalizedAuthorText(elementText(selected.element)).includes(name))) {
    return selectedId;
  }
  let best = null;
  for (const candidate of page.candidates) {
    const text = normalizedAuthorText(elementText(candidate.element));
    if (!text || text.length > 250 || !names.every((name) => text.includes(name))) continue;
    let depth = 0;
    for (let node = candidate.element; node?.nodeType === 1; node = node.parentElement) depth += 1;
    if (!best || text.length < best.length ||
        (text.length === best.length && depth > best.depth) ||
        (text.length === best.length && depth === best.depth && candidate.nodeId < best.nodeId)) {
      best = { length: text.length, depth, nodeId: candidate.nodeId };
    }
  }
  return best?.nodeId ?? selectedId;
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
  const root = document.documentElement;
  if (!root) return;
  function removeChildrenWhere(parent, predicate) {
    for (let child = parent.firstChild; child;) {
      const next = child.nextSibling;
      if (child.nodeType === 1) {
        if (predicate(child)) child.remove();
        else removeChildrenWhere(child, predicate);
      }
      child = next;
    }
  }
  removeChildrenWhere(root, (element) => CHROME_DROP_TAGS.has(tagName(element)));
  removeChildrenWhere(root, looksLikePageChrome);
  function pruneChildren(parent) {
    for (let child = parent.firstChild; child;) {
      const next = child.nextSibling;
      if (child.nodeType === 1) {
        pruneChildren(child);
        if (EMPTY_PRUNABLE_TAGS.has(tagName(child)) &&
            child.children.length === 0 && elementText(child) === "" &&
            !EXTRACTION_TOKENS.test(semanticValue(child))) child.remove();
      }
      child = next;
    }
  }
  pruneChildren(root);
}

export function parsePage(html) {
  const { document } = parseHTML(html);
  const metadataAuthors = metadataAuthorNames(document, html);
  const originalIds = new WeakMap();
  const originalPositions = new WeakMap();
  let nextId = 0;
  function assignOriginalIds(element) {
    if (EXCLUDED_SUBTREES.has(tagName(element))) return;
    originalIds.set(element, nextId++);
    const siblingCounts = new Map();
    for (const child of element.children) {
      const name = tagName(child);
      const position = (siblingCounts.get(name) ?? 0) + 1;
      siblingCounts.set(name, position);
      originalPositions.set(child, position);
      assignOriginalIds(child);
    }
  }
  if (document.documentElement) {
    originalPositions.set(document.documentElement, 1);
    assignOriginalIds(document.documentElement);
  }
  stripPageChrome(document);
  const candidates = [];
  function collectKept(element) {
    if (originalIds.has(element)) candidates.push({ nodeId: originalIds.get(element), element });
    for (const child of element.children) collectKept(child);
  }
  if (document.documentElement) collectKept(document.documentElement);
  return { document, candidates, originalPositions, metadataAuthors };
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
