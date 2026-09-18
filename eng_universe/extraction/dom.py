"""Deterministic HTML parsing and candidate-node assignment."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Iterator

from bs4 import BeautifulSoup, Tag


PARSER = "html.parser"
EXCLUDED_SUBTREES = frozenset({"head", "script", "style", "noscript", "template"})


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
            "text": element.get_text(" ", strip=True),
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


def parse_html(html: str) -> ParsedPage:
    """Parse HTML and assign contiguous candidate IDs in document pre-order.

    The original HTML string is retained byte-for-byte (after its caller has
    decoded bytes to text). IDs are assigned only to selectable elements;
    excluded subtrees never consume IDs.
    """

    dom = BeautifulSoup(html, PARSER)
    candidates = tuple(
        Candidate(node_id=node_id, element=element)
        for node_id, element in enumerate(_candidate_elements(dom))
    )
    return ParsedPage(
        original_html=html,
        html_hash=html_sha256(html),
        dom=dom,
        candidates=candidates,
        node_by_id={candidate.node_id: candidate.element for candidate in candidates},
        id_by_element_identity={
            id(candidate.element): candidate.node_id for candidate in candidates
        },
    )


def annotation_html(page: ParsedPage) -> str:
    """Return a disposable DOM copy with candidate IDs attached for the UI."""

    copy = parse_html(page.original_html)
    for candidate in copy.candidates:
        candidate.element["data-eu-node-id"] = str(candidate.node_id)
    for script in copy.dom.find_all("script"):
        script.decompose()
    return str(copy.dom)
