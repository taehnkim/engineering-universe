import { parseHTML } from "linkedom";

// Extractus returns cleaned HTML rather than plain body text. Convert that
// output with its own parser dependency and retain word breaks at block tags.
export function textFromHtml(html) {
  if (!html) return null;
  const { document } = parseHTML(`<html><body>${html}</body></html>`);
  const parts = [];
  const blocks = new Set(["article", "blockquote", "br", "div", "h1", "h2", "h3", "h4",
    "h5", "h6", "li", "p", "section", "table", "td", "th", "tr"]);
  function visit(node) {
    if (node.nodeType === 3) parts.push(node.nodeValue);
    else if (node.nodeType === 1) {
      const block = blocks.has(node.localName);
      if (block) parts.push(" ");
      for (const child of node.childNodes) visit(child);
      if (block) parts.push(" ");
    }
  }
  visit(document.body);
  return parts.join("").replace(/\s+/g, " ").trim() || null;
}
