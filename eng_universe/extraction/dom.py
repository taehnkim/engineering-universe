"""Deterministic HTML parsing and candidate-node assignment."""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag

PARSER = "html.parser"
DOM_CLEANUP_VERSION = "chrome-v2"
EXCLUDED_SUBTREES = frozenset({"head", "script", "style", "noscript", "template"})
CHROME_DROP_TAGS = frozenset(
    {
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
    }
)
CHROME_TOKENS = re.compile(
    r"\b(?:backdrop|breadcrumb|comments?|consent|cookie|drawer|footer|lightbox|menu|"
    r"modal|newsletter|overlay|pagination|popup|promo|recommend(?:ation|ations|ed)?|"
    r"related|share|sidebar|social|subscribe|toast)\b",
    re.IGNORECASE,
)
EXTRACTION_TOKENS = re.compile(
    r"author|byline|date|publish|time|headline|title|subtitle|sub-title|subhead|"
    r"standfirst|dek|excerpt|description|lead",
    re.IGNORECASE,
)
_ORIGINAL_NODE_ID = "data-eu-original-node-id"
_HIDDEN_STYLE = re.compile(
    r"(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important)?\s*(?:;|$)",
    re.IGNORECASE,
)
_EMPTY_PRUNABLE_TAGS = frozenset({"div", "figure", "p", "section", "span"})
_TEXT_BLOCKS = frozenset(
    {
        "article",
        "blockquote",
        "div",
        "figcaption",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "ul",
    }
)


def _cell_text(element: Tag) -> str:
    return element.get_text(" ", strip=True)


def _table_rows(element: Tag) -> list[list[Tag | None]] | None:
    if element.name == "table":
        rows = [
            row for row in element.find_all("tr") if row.find_parent("table") is element
        ]
        first = rows[0].find_all(["th", "td"], recursive=False) if rows else []
        bold_header = len(first) >= 2 and all(
            (bold := cell.find(["b", "strong"])) is not None
            and _cell_text(cell) == _cell_text(bold)
            for cell in first
        )
        if not rows or not (
            rows[0].parent.name == "thead"
            or any(cell.name == "th" for cell in first)
            or bold_header
        ):
            return None
        cells: list[list[Tag | None]] = [
            row.find_all(["th", "td"], recursive=False) for row in rows
        ]
        for row in cells:
            for cell in row:
                if (
                    cell is None
                    or cell.get("colspan", "1") != "1"
                    or cell.get("rowspan", "1") != "1"
                ):
                    return None
        return cells

    # This is a common CSS-table shape, but not every CSS grid is a table.
    if element.name != "div":
        return None
    rows = element.find_all(recursive=False)
    if len(rows) < 3 or any(
        row.name != "div" or "grid" not in row.get("class", []) for row in rows
    ):
        return None
    cells = [row.find_all(recursive=False) for row in rows]
    # Chrome-v2 can prune the empty top-left grid cell before text extraction.
    if len(cells[0]) == len(cells[1]) - 1:
        cells[0].insert(0, None)
    if len(cells[0]) < 3 or (cells[0][0] is not None and _cell_text(cells[0][0])):
        return None
    return cells


def _formatted_table(element: Tag) -> str | None:
    cells = _table_rows(element)
    if cells is None or len(cells) < 2:
        return None
    width = len(cells[0])
    if not 2 <= width <= 16 or any(len(row) != width for row in cells):
        return None
    matrix = [
        [_cell_text(cell) if cell is not None else "" for cell in row] for row in cells
    ]
    if any(not value for value in matrix[0][1:]) or any(
        not value for row in matrix[1:] for value in row
    ):
        return None

    headers = matrix[0]
    lines = []
    for row in matrix[1:]:
        lines.append(f"- {row[0]}")
        lines.extend(f"  - {headers[index]}: {row[index]}" for index in range(1, width))
    caption = (
        element.find("caption", recursive=False) if element.name == "table" else None
    )
    return (
        f"{_cell_text(caption)}\n\n" + "\n".join(lines) if caption else "\n".join(lines)
    )


def readable_text(element: Tag) -> str:
    """Keep block and paragraph boundaries while joining inline text."""

    selected_table = _formatted_table(element)
    if selected_table is not None:
        return selected_table
    parts: list[str] = []
    tables: list[str] = []

    def walk(node: Tag) -> None:
        for child in node.children:
            if isinstance(child, NavigableString):
                value = re.sub(r"\s+", " ", str(child))
                parts.append(value)
            elif isinstance(child, Tag):
                if child.name == "br":
                    parts.append("\n")
                    continue
                table = _formatted_table(child)
                if table is not None:
                    parts.extend(("\n\n", f"\0{len(tables)}\0", "\n\n"))
                    tables.append(table)
                    continue
                block = child.name in _TEXT_BLOCKS
                if block:
                    parts.append("\n\n")
                walk(child)
                if block:
                    parts.append("\n\n")

    walk(element)
    text = "".join(parts)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return re.sub(r"\0(\d+)\0", lambda match: tables[int(match.group(1))], text)


@dataclass(frozen=True, slots=True)
class Candidate:
    node_id: int
    element: Tag


@dataclass(slots=True)
class ParsedPage:
    original_html: str
    html_hash: str
    dom: BeautifulSoup
    candidates: tuple[Candidate, ...]
    node_by_id: dict[int, Tag]
    id_by_element_identity: dict[int, int]
    chrome_stripped: bool
    preview_html: str | None = None

    def candidate(self, node_id: int) -> Tag:
        try:
            return self.node_by_id[node_id]
        except KeyError as exc:
            raise ValueError(f"unknown candidate node ID: {node_id}") from exc

    def selected_content(self, node_id: int) -> dict[str, str | int]:
        element = self.candidate(node_id)
        return {
            "node_id": node_id,
            "html": str(element),
            "text": readable_text(element),
        }


def html_sha256(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()


def _has_excluded_ancestor(element: Tag) -> bool:
    current: Tag | None = element
    while current is not None:
        if current.name and current.name.lower() in EXCLUDED_SUBTREES:
            return True
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None
    return False


def _candidate_elements(dom: BeautifulSoup) -> Iterator[Tag]:
    # BeautifulSoup find_all(True) is deterministic document pre-order for a
    # fixed parser and input string.
    for element in dom.find_all(True):
        if not _has_excluded_ancestor(element):
            yield element


def _normalize_semantic_name(value: str) -> str:
    """Normalize CamelCase, snake_case, and kebab-case names into words."""

    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return re.sub(r"[^a-zA-Z0-9]+", " ", value).strip().lower()


def _semantic_value(element: Tag) -> str:
    values = [element.name or "", str(element.get("id", ""))]
    for name in ("class", "itemprop", "role", "aria-label"):
        value = element.get(name)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return " ".join(_normalize_semantic_name(value) for value in values)


def _is_hidden(element: Tag) -> bool:
    if element.has_attr("hidden"):
        return True
    if str(element.get("aria-hidden", "")).strip().lower() == "true":
        return True
    if str(element.get("aria-modal", "")).strip().lower() == "true":
        return True
    return bool(_HIDDEN_STYLE.search(str(element.get("style", ""))))


def looks_like_page_chrome(element: Tag) -> bool:
    """Return true for common page controls and non-article containers."""

    if element.name in {"html", "body"}:
        return False
    role = str(element.get("role", "")).lower()
    if role in {"alertdialog", "contentinfo", "dialog", "navigation", "search"}:
        return True
    if _is_hidden(element):
        return True
    semantics = _semantic_value(element)
    if EXTRACTION_TOKENS.search(semantics):
        return False
    return bool(CHROME_TOKENS.search(semantics))


def _strip_page_chrome(dom: BeautifulSoup) -> None:
    for element in list(dom.find_all(CHROME_DROP_TAGS)):
        element.decompose()
    for element in list(dom.find_all(True)):
        if element.parent is not None and looks_like_page_chrome(element):
            element.decompose()
    for element in reversed(list(dom.find_all(True))):
        if (
            element.parent is not None
            and element.name in _EMPTY_PRUNABLE_TAGS
            and not element.find(True)
            and not element.get_text(strip=True)
            and not EXTRACTION_TOKENS.search(_semantic_value(element))
        ):
            element.decompose()


def parse_html(
    html: str,
    *,
    strip_chrome: bool = False,
    backend: str = "python",
) -> ParsedPage:
    """Parse HTML and assign stable candidate IDs in document pre-order.

    The original HTML string is retained byte-for-byte (after its caller has
    decoded bytes to text). IDs are assigned only to selectable elements;
    excluded subtrees never consume IDs. Chrome cleanup removes candidates but
    keeps each surviving element's original ID.
    """

    if backend == "go":
        if not strip_chrome:
            raise ValueError("Go DOM preparation requires strip_chrome=True")
        from eng_universe.extraction.go_dom import prepare_html

        prepared = prepare_html(html)
        dom = BeautifulSoup(str(prepared["clean_html"]), PARSER)
        candidates = tuple(
            Candidate(
                node_id=int(element.attrs.pop(_ORIGINAL_NODE_ID)), element=element
            )
            for element in _candidate_elements(dom)
            if element.has_attr(_ORIGINAL_NODE_ID)
        )
        ids = [candidate.node_id for candidate in candidates]
        if ids != prepared["node_ids"]:
            raise ValueError(
                "Go DOM candidate IDs changed during Python reconstruction"
            )
        return ParsedPage(
            original_html=html,
            html_hash=html_sha256(html),
            dom=dom,
            candidates=candidates,
            node_by_id={
                candidate.node_id: candidate.element for candidate in candidates
            },
            id_by_element_identity={
                id(candidate.element): candidate.node_id for candidate in candidates
            },
            chrome_stripped=True,
            preview_html=str(prepared["preview_html"]),
        )
    if backend != "python":
        raise ValueError(f"unknown DOM backend: {backend}")
    dom = BeautifulSoup(html, PARSER)
    original_candidates = tuple(
        Candidate(node_id=node_id, element=element)
        for node_id, element in enumerate(_candidate_elements(dom))
    )
    if strip_chrome:
        for candidate in original_candidates:
            candidate.element[_ORIGINAL_NODE_ID] = str(candidate.node_id)
        _strip_page_chrome(dom)
        candidates = tuple(
            Candidate(
                node_id=int(element.attrs.pop(_ORIGINAL_NODE_ID)), element=element
            )
            for element in _candidate_elements(dom)
            if element.has_attr(_ORIGINAL_NODE_ID)
        )
    else:
        candidates = original_candidates
    return ParsedPage(
        original_html=html,
        html_hash=html_sha256(html),
        dom=dom,
        candidates=candidates,
        node_by_id={candidate.node_id: candidate.element for candidate in candidates},
        id_by_element_identity={
            id(candidate.element): candidate.node_id for candidate in candidates
        },
        chrome_stripped=strip_chrome,
    )


def annotation_html(page: ParsedPage) -> str:
    """Return the cleaned DOM with stable candidate IDs attached for the UI."""

    if page.preview_html is not None:
        return page.preview_html
    cleaned = (
        page
        if page.chrome_stripped
        else parse_html(page.original_html, strip_chrome=True)
    )
    preview = copy.deepcopy(cleaned.dom)
    for element, candidate in zip(
        preview.find_all(True), cleaned.candidates, strict=True
    ):
        element["data-eu-node-id"] = str(candidate.node_id)
    return str(preview)
