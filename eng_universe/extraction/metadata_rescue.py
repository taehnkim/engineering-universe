"""Conservative metadata-assisted rescue of visible author DOM nodes.

Structured metadata is evidence, not the output: the selected node must still
exist in the cleaned page. This is deliberately gated so metadata cannot move
an already matching model prediction.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from eng_universe.extraction.dom import ParsedPage

_HEAD_END = re.compile(r"</head\s*>", re.IGNORECASE)
_WORDS = re.compile(r"\w+", re.UNICODE)
_ORGANIZATION = re.compile(
    r"\b(?:lab|team|inc|ltd|company|corporation)\b", re.IGNORECASE
)
_ARTICLE_TYPES = frozenset({"Article", "BlogPosting", "NewsArticle", "TechArticle"})
_AUTHOR_META_KEYS = frozenset(
    {"author", "article:author", "dc.creator", "parsely-author"}
)
_MAX_HEAD_CHARS = 128_000
_MAX_AUTHOR_TEXT_CHARS = 250


def _normalized(value: str) -> str:
    return " ".join(_WORDS.findall(value.casefold()))


def _json_authors(value: object, depth: int = 0) -> list[str]:
    if depth > 20:
        return []
    if isinstance(value, list):
        return [name for item in value for name in _json_authors(item, depth + 1)]
    if not isinstance(value, dict):
        return []
    names: list[str] = []
    kind = value.get("@type", "")
    kinds = kind if isinstance(kind, list) else [kind]
    if any(item in _ARTICLE_TYPES for item in kinds):
        author = value.get("author")
        entries = author if isinstance(author, list) else [author]
        for entry in entries:
            if isinstance(entry, str):
                names.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
                names.append(entry["name"])
    for child in value.values():
        names.extend(_json_authors(child, depth + 1))
    return names


class _AuthorHeadParser(HTMLParser):
    """Collect only the two head element types needed for author evidence."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.names: list[str] = []
        self._json_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "meta":
            key = (values.get("name") or values.get("property") or "").casefold()
            if key in _AUTHOR_META_KEYS:
                self.names.append(values.get("content") or "")
        elif tag == "script" and values.get("type") == "application/ld+json":
            self._json_parts = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_data(self, data: str) -> None:
        if self._json_parts is not None:
            self._json_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "script" or self._json_parts is None:
            return
        try:
            self.names.extend(_json_authors(json.loads("".join(self._json_parts))))
        except (json.JSONDecodeError, TypeError):
            pass
        self._json_parts = None


def metadata_author_names(html: str) -> tuple[str, ...]:
    """Read person-like author names from head metadata, with bounded work."""

    match = _HEAD_END.search(html, 0, _MAX_HEAD_CHARS)
    if match is None:
        return ()
    parser = _AuthorHeadParser()
    parser.feed(html[: match.end()])
    return tuple(
        dict.fromkeys(
            name.strip()
            for name in parser.names
            if len(_normalized(name).split()) >= 2
            and not _ORGANIZATION.search(name)
        )
    )


def rescue_author_node(page: ParsedPage, selected_id: int | None) -> int | None:
    """Select the tightest visible node covering all metadata author names.

    The model remains authoritative when its node already covers the names or
    when metadata is absent, ambiguous, or not present in a short visible node.
    """

    names = metadata_author_names(page.original_html)
    if not names:
        return selected_id
    normalized_names = tuple(_normalized(name) for name in names)
    if selected_id is not None:
        selected_text = _normalized(page.candidate(selected_id).get_text(" ", strip=True))
        if all(name in selected_text for name in normalized_names):
            return selected_id
    matches: list[tuple[int, int, int]] = []
    for candidate in page.candidates:
        text = _normalized(candidate.element.get_text(" ", strip=True))
        if not text or len(text) > _MAX_AUTHOR_TEXT_CHARS:
            continue
        if all(name in text for name in normalized_names):
            matches.append(
                (len(text), -len(list(candidate.element.parents)), candidate.node_id)
            )
    return min(matches)[2] if matches else selected_id
