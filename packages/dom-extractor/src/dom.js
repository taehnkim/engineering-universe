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

function sourceUrl(document) {
  const canonical = document.querySelector('link[rel~="canonical"][href]')?.getAttribute("href");
  const openGraph = document.querySelector('meta[property="og:url"][content]')?.getAttribute("content");
  for (const value of [canonical, openGraph]) {
    if (!value) continue;
    try {
      const url = new URL(value);
      if (url.protocol === "http:" || url.protocol === "https:") return url.href;
    } catch {
      // A relative URL has no origin in a raw HTML string.
    }
  }
  return null;
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

function tableRows(element) {
  if (element.localName === "table") {
    const rows = [...element.querySelectorAll("tr")]
      .filter((row) => row.closest("table") === element);
    const firstCells = rows.length ? [...rows[0].children] : [];
    const boldHeader = firstCells.length >= 2 && firstCells.every((cell) => {
      const bold = cell.querySelector("b, strong");
      return bold && elementText(cell) === elementText(bold);
    });
    if (!rows.length || !(
      rows[0].parentElement?.localName === "thead" ||
      firstCells.some((cell) => cell.localName === "th") || boldHeader
    )) return null;
    const cells = rows.map((row) => [...row.children]
      .filter((cell) => cell.localName === "th" || cell.localName === "td"));
    if (cells.some((row) => row.some((cell) =>
      Number(cell.getAttribute("colspan") || 1) !== 1 ||
      Number(cell.getAttribute("rowspan") || 1) !== 1))) return null;
    return cells;
  }

  // A frequent CSS-table shape: a wrapper of repeated grid rows. Requiring a
  // blank top-left cell avoids treating ordinary card grids as comparison tables.
  if (element.localName !== "div") return null;
  const rows = [...element.children];
  if (rows.length < 3 || rows.some((row) =>
    row.localName !== "div" || !row.classList.contains("grid"))) return null;
  const cells = rows.map((row) => [...row.children]);
  // Chrome-v2 prunes empty divs. The blank corner of a comparison grid can
  // therefore disappear while its data rows keep their label column.
  if (cells[0]?.length === cells[1]?.length - 1) cells[0].unshift(null);
  if (cells[0]?.length < 3 || (cells[0][0] && elementText(cells[0][0]) !== "")) return null;
  return cells;
}

function formattedTable(element) {
  const cells = tableRows(element);
  if (!cells || cells.length < 2) return null;
  const width = cells[0].length;
  if (width < 2 || width > 16 || cells.some((row) => row.length !== width)) return null;
  const matrix = cells.map((row) => row.map((cell) => cell ? elementText(cell) : ""));
  if (matrix[0].slice(1).some((value) => !value) ||
      matrix.slice(1).some((row) => row.some((value) => !value))) return null;

  const headers = matrix[0];
  const lines = [];
  for (const row of matrix.slice(1)) {
    lines.push(`- ${row[0]}`);
    for (let index = 1; index < width; index++) {
      lines.push(`  - ${headers[index]}: ${row[index]}`);
    }
  }
  const caption = element.localName === "table"
    ? [...element.children].find((child) => child.localName === "caption")
    : null;
  return caption ? `${elementText(caption)}\n\n${lines.join("\n")}` : lines.join("\n");
}

export function readableText(element) {
  const selectedTable = formattedTable(element);
  if (selectedTable !== null) return selectedTable;
  const parts = [];
  const tables = [];
  function walk(node) {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) parts.push(child.nodeValue.replace(/\s+/g, " "));
      else if (child.nodeType === 1) {
        const name = child.localName.toLowerCase();
        if (name === "br") { parts.push("\n"); continue; }
        const table = formattedTable(child);
        if (table !== null) {
          parts.push("\n\n", `\u0000${tables.length}\u0000`, "\n\n");
          tables.push(table);
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
    .replace(/\u0000(\d+)\u0000/g, (_, index) => tables[Number(index)]);
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
  const pageSourceUrl = sourceUrl(document);
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
  return { document, candidates, sourceUrl: pageSourceUrl, originalPositions };
}

export function cssSelector(element, page) {
  const segments = [];
  for (let node = element; node?.nodeType === 1; node = node.parentElement) {
    segments.push(`${tagName(node)}:nth-of-type(${page.originalPositions.get(node)})`);
  }
  return segments.reverse().join(" > ");
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
