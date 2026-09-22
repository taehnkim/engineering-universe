"""Compact semantic and structural features shared by training and inference."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass

import numpy as np
from bs4 import Tag

from eng_universe.extraction.dom import Candidate, ParsedPage

FEATURE_VERSION = "semantic-v2"
MAX_SEMANTIC_VOCABULARY = 64
MAX_SEMANTIC_TOKENS = 12
SEMANTIC_PRIORITY_TOKENS = (
    "class:author",
    "class:authors",
    "class:byline",
    "class:written",
    "class:contributor",
    "class:profile",
    "class:person",
    "class:people",
    "class:team",
    "class:date",
    "class:published",
    "class:updated",
    "class:modified",
    "class:headline",
    "class:title",
    "class:summary",
    "class:subtitle",
    "class:post",
    "class:body",
    "class:content",
    "class:entry",
    "id:author",
    "id:authors",
    "id:byline",
    "id:date",
    "id:published",
    "id:updated",
    "id:title",
    "id:content",
    "id:article",
    "itemprop:author",
    "itemprop:name",
    "itemprop:headline",
    "itemprop:date",
    "itemprop:published",
    "itemprop:modified",
    "rel:author",
    "aria-label:author",
    "aria-label:published",
    "aria-label:updated",
    "text:by",
    "text:written",
    "text:author",
    "text:published",
    "text:updated",
)

DATE_LIKE_RE = re.compile(
    r"(?:\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b)"
    r"|(?:\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}\b)",
    re.IGNORECASE,
)
RELATIVE_DATE_RE = re.compile(
    r"\b(?:an?|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?:minute|hour|day|week|month|year)s?\s+ago\b",
    re.IGNORECASE,
)
READING_TIME_RE = re.compile(
    r"\b(?:an?|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s*"
    r"(?:min(?:ute)?s?|hours?)\s+(?:read|reading)\b",
    re.IGNORECASE,
)
READING_TIME_ONLY_RE = re.compile(
    rf"^\s*(?:{READING_TIME_RE.pattern})\s*$", re.IGNORECASE
)
BY_PREFIX_RE = re.compile(r"^\s*(?:by\b|written\s+by\b|author\s*:?)", re.IGNORECASE)
WRITTEN_BY_RE = re.compile(r"^\s*written\s+by\b", re.IGNORECASE)
PUBLISHED_MARKER_RE = re.compile(r"\b(?:publish(?:ed)?|posted)\b", re.IGNORECASE)
UPDATED_MARKER_RE = re.compile(r"\b(?:updated?|modified)\b", re.IGNORECASE)
MULTIPLE_NAME_RE = re.compile(r"(?:\s(?:and|und|et|y)\s|[,;&/·•])", re.IGNORECASE)
PROFILE_LINK_RE = re.compile(
    r"(?:/|\b)(?:author|authors|profile|people|person|team|contributors?)(?:/|\b)|/@",
    re.IGNORECASE,
)
TOKEN_RE = re.compile(r"\w+", re.UNICODE)
AUTHOR_ATTRIBUTE_RE = re.compile(
    r"\b(?:author|authors|contributor|contributors|profile|person|people)\b",
    re.IGNORECASE,
)
BYLINE_ATTRIBUTE_RE = re.compile(r"\b(?:byline|written[\s_-]*by)\b", re.IGNORECASE)

NUMERIC_FEATURE_NAMES = (
    "log_descendant_text_length",
    "log_descendant_paragraph_count",
    "descendant_link_text_fraction",
    "log_dom_depth",
    "relative_document_position",
    "has_datetime_attribute",
    "contains_absolute_date",
    "contains_relative_date",
    "log_word_count",
    "capitalized_token_ratio",
    "log_descendant_link_count",
    "contains_by_prefix",
    "contains_written_by",
    "contains_author_attribute",
    "contains_byline_attribute",
    "contains_profile_link",
    "person_name_shape",
    "multiple_name_separator",
    "short_text",
    "inside_header",
    "contains_published_marker",
    "contains_updated_marker",
    "contains_reading_time",
    "tag_is_time",
    "itemprop_date_published",
    "itemprop_date_modified",
    "log_direct_child_count",
    "log_descendant_span_count",
    "is_leaf",
    "direct_text_fraction",
    "inside_article",
    "inside_main",
    "distance_from_title",
    "before_title",
    "same_parent_as_title",
    "log_tree_distance_from_title",
    "distance_from_date",
    "before_date",
    "same_parent_as_date",
    "log_tree_distance_from_date",
)
CONTINUOUS_FEATURE_INDICES = (
    0,
    1,
    2,
    3,
    4,
    8,
    9,
    10,
    26,
    27,
    29,
    32,
    35,
    36,
    39,
)


@dataclass(frozen=True, slots=True)
class TagVocabulary:
    tags: tuple[str, ...]

    PAD = "<pad>"
    UNKNOWN = "<unknown>"

    @classmethod
    def fit(cls, pages: Iterable[ParsedPage]) -> TagVocabulary:
        observed: set[str] = set()
        for page in pages:
            for candidate in page.candidates:
                observed.add(candidate.element.name.lower())
                parent = candidate.element.parent
                if isinstance(parent, Tag) and parent.name:
                    observed.add(parent.name.lower())
        return cls((cls.PAD, cls.UNKNOWN, *sorted(observed)))

    @property
    def lookup(self) -> dict[str, int]:
        return {tag: index for index, tag in enumerate(self.tags)}

    def encode(self, tag: str | None) -> int:
        if not tag:
            return 1
        return self.lookup.get(tag.lower(), 1)

    def to_dict(self) -> dict[str, object]:
        return {"tags": list(self.tags)}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> TagVocabulary:
        return cls(tuple(str(tag) for tag in value["tags"]))  # type: ignore[index]


@dataclass(frozen=True, slots=True)
class SemanticVocabulary:
    """Bounded vocabulary for semantic attributes and short text prefixes."""

    tokens: tuple[str, ...]

    PAD = "<pad>"
    UNKNOWN = "<unknown>"

    @classmethod
    def fit(
        cls,
        pages: Iterable[ParsedPage],
        max_size: int = MAX_SEMANTIC_VOCABULARY,
    ) -> SemanticVocabulary:
        counts: Counter[str] = Counter()
        for page in pages:
            for candidate in page.candidates:
                counts.update(semantic_tokens(candidate.element))
        available = max(0, max_size - 2)
        priority = [token for token in SEMANTIC_PRIORITY_TOKENS if counts[token]]
        frequent = sorted(
            (token for token in counts if token not in priority),
            key=lambda token: (-counts[token], token),
        )
        selected = (priority + frequent)[:available]
        return cls((cls.PAD, cls.UNKNOWN, *selected))

    @property
    def lookup(self) -> dict[str, int]:
        return {token: index for index, token in enumerate(self.tokens)}

    def encode(self, values: Sequence[str]) -> np.ndarray:
        lookup = self.lookup
        encoded = [lookup.get(value, 1) for value in values[:MAX_SEMANTIC_TOKENS]]
        encoded.extend([0] * (MAX_SEMANTIC_TOKENS - len(encoded)))
        return np.asarray(encoded, dtype=np.int64)

    def to_dict(self) -> dict[str, object]:
        return {"tokens": list(self.tokens), "max_tokens": MAX_SEMANTIC_TOKENS}

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> SemanticVocabulary:
        return cls(tuple(str(token) for token in value["tokens"]))  # type: ignore[index]


@dataclass(frozen=True, slots=True)
class FeatureNormalizer:
    mean: tuple[float, ...]
    std: tuple[float, ...]

    @classmethod
    def fit(cls, matrices: Sequence[np.ndarray]) -> FeatureNormalizer:
        if not matrices:
            raise ValueError("cannot fit feature normalization without training pages")
        values = np.concatenate(matrices, axis=0)
        mean = np.zeros(values.shape[1], dtype=np.float32)
        std = np.ones(values.shape[1], dtype=np.float32)
        indices = list(CONTINUOUS_FEATURE_INDICES)
        mean[indices] = values[:, indices].mean(axis=0)
        fitted_std = values[:, indices].std(axis=0)
        std[indices] = np.where(fitted_std < 1e-6, 1.0, fitted_std)
        return cls(
            tuple(float(item) for item in mean), tuple(float(item) for item in std)
        )

    def transform(self, matrix: np.ndarray) -> np.ndarray:
        return (matrix - np.asarray(self.mean, dtype=np.float32)) / np.asarray(
            self.std, dtype=np.float32
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> FeatureNormalizer:
        return cls(
            tuple(float(item) for item in value["mean"]),  # type: ignore[index]
            tuple(float(item) for item in value["std"]),  # type: ignore[index]
        )


@dataclass(frozen=True, slots=True)
class PageFeatures:
    node_ids: np.ndarray
    tag_ids: np.ndarray
    parent_tag_ids: np.ndarray
    grandparent_tag_ids: np.ndarray
    previous_tag_ids: np.ndarray
    next_tag_ids: np.ndarray
    semantic_token_ids: np.ndarray
    numeric: np.ndarray


def _depth(element: Tag) -> int:
    depth = 0
    parent = element.parent
    while isinstance(parent, Tag):
        depth += 1
        parent = parent.parent
    return depth


def _paragraph_count(element: Tag, descendants: Sequence[Tag] | None = None) -> int:
    values = element.find_all(True) if descendants is None else descendants
    return (1 if element.name.lower() == "p" else 0) + sum(
        item.name.lower() == "p" for item in values
    )


def _link_text_fraction(
    element: Tag,
    text_length: int,
    links: Sequence[Tag] | None = None,
) -> float:
    if text_length == 0:
        return 0.0
    link_length = 0
    if element.name.lower() == "a":
        link_length += len(element.get_text(" ", strip=True))
    descendants = element.find_all("a") if links is None else links
    link_length += sum(len(link.get_text(" ", strip=True)) for link in descendants)
    return min(1.0, link_length / text_length)


def _normalize_token_source(value: str) -> list[str]:
    value = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return [
        token
        for match in TOKEN_RE.finditer(value)
        if (token := match.group(0).lower()).strip("_")
        and (len(token) > 1 or token == "by")
    ]


def _attribute_values(element: Tag) -> Iterator[tuple[str, str]]:
    for name in ("class", "id", "itemprop", "rel", "aria-label"):
        value = element.get(name)
        values = value if isinstance(value, list) else [value]
        for item in values:
            if item:
                yield name, str(item)


def semantic_tokens(element: Tag, text: str | None = None) -> tuple[str, ...]:
    tokens: list[str] = []
    for name, value in _attribute_values(element):
        tokens.extend(f"{name}:{token}" for token in _normalize_token_source(value))
    candidate_text = element.get_text(" ", strip=True) if text is None else text
    if len(candidate_text) <= 400:
        words = _normalize_token_source(candidate_text)
        if len(words) <= 40:
            tokens.extend(f"text:{token}" for token in words[:8])
    return tuple(tokens)


def _semantic_attribute_text(
    element: Tag, direct_children: Sequence[Tag] | None = None
) -> str:
    # Direct descendants capture a selected wrapper's links/spans without
    # making every broad article ancestor inherit all metadata on the page.
    elements = [
        element,
        *(
            element.find_all(True, recursive=False)
            if direct_children is None
            else direct_children
        ),
    ]
    return " ".join(value for item in elements for _, value in _attribute_values(item))


def _inside(element: Tag, name: str) -> bool:
    current = element.parent
    while isinstance(current, Tag):
        if current.name and current.name.lower() == name:
            return True
        current = current.parent
    return False


def _direct_text_length(element: Tag) -> int:
    return sum(
        len(str(child).strip())
        for child in element.children
        if not isinstance(child, Tag)
    )


def _tree_distance(left: Tag, right: Tag) -> int:
    left_ancestors: dict[int, int] = {}
    current: Tag | None = left
    distance = 0
    while isinstance(current, Tag):
        left_ancestors[id(current)] = distance
        current = current.parent if isinstance(current.parent, Tag) else None
        distance += 1
    current = right
    distance = 0
    while isinstance(current, Tag):
        if id(current) in left_ancestors:
            return distance + left_ancestors[id(current)]
        current = current.parent if isinstance(current.parent, Tag) else None
        distance += 1
    return distance + len(left_ancestors)


def _title_anchor_index(page: ParsedPage) -> int | None:
    for index, candidate in enumerate(page.candidates):
        itemprop = str(candidate.element.get("itemprop", "")).lower()
        if candidate.element.name.lower() == "h1" or "headline" in itemprop:
            return index
    return None


def _date_anchor_indices(page: ParsedPage, texts: Sequence[str]) -> tuple[int, ...]:
    values: list[int] = []
    for index, candidate in enumerate(page.candidates):
        element = candidate.element
        text = texts[index]
        itemprop = str(element.get("itemprop", "")).lower()
        word_count = len(text.split())
        if (
            element.name.lower() == "time"
            or element.has_attr("datetime")
            or itemprop in {"datepublished", "datemodified"}
            or (
                word_count <= 20
                and not READING_TIME_ONLY_RE.fullmatch(text)
                and bool(DATE_LIKE_RE.search(text) or RELATIVE_DATE_RE.search(text))
            )
        ):
            values.append(index)
    return tuple(values)


def _nearest_anchor(index: int, anchors: Sequence[int]) -> int | None:
    return (
        min(anchors, key=lambda anchor: (abs(anchor - index), anchor))
        if anchors
        else None
    )


def _tag_id_for_sibling(
    candidate: Candidate, tag_lookup: dict[str, int], *, next_: bool
) -> int:
    sibling = (
        candidate.element.find_next_sibling()
        if next_
        else candidate.element.find_previous_sibling()
    )
    return (
        tag_lookup.get(sibling.name.lower(), 1)
        if isinstance(sibling, Tag) and sibling.name
        else 1
    )


def raw_numeric_features(
    candidate: Candidate,
    candidate_count: int,
    candidate_index: int = 0,
    *,
    title_anchor: Candidate | None = None,
    title_anchor_index: int | None = None,
    date_anchor: Candidate | None = None,
    date_anchor_index: int | None = None,
    text: str | None = None,
) -> np.ndarray:
    element = candidate.element
    candidate_text = element.get_text(" ", strip=True) if text is None else text
    text_length = len(candidate_text)
    words = candidate_text.split()
    position = candidate_index / max(1, candidate_count - 1)
    descendants = element.find_all(True)
    direct_children = [item for item in descendants if item.parent is element]
    links = [item for item in descendants if item.name.lower() == "a"]
    semantics = _semantic_attribute_text(element, direct_children)
    own_itemprop = str(element.get("itemprop", "")).lower()
    capitalized = sum(
        bool(word) and word[0].isalpha() and word[0].isupper() for word in words
    )
    person_name_shape = bool(
        re.fullmatch(r"@[\w.-]+", candidate_text.strip(), re.UNICODE)
        or (
            1 <= len(words) <= 8
            and capitalized / max(1, len(words)) >= 0.5
            and not DATE_LIKE_RE.search(candidate_text)
        )
    )
    title_distance = (
        abs(candidate_index - title_anchor_index) / max(1, candidate_count - 1)
        if title_anchor_index is not None
        else 1.0
    )
    date_distance = (
        abs(candidate_index - date_anchor_index) / max(1, candidate_count - 1)
        if date_anchor_index is not None
        else 1.0
    )
    return np.asarray(
        [
            math.log1p(text_length),
            math.log1p(_paragraph_count(element, descendants)),
            _link_text_fraction(element, text_length, links),
            math.log1p(_depth(element)),
            position,
            float(element.has_attr("datetime")),
            float(bool(DATE_LIKE_RE.search(candidate_text))),
            float(bool(RELATIVE_DATE_RE.search(candidate_text))),
            math.log1p(len(words)),
            capitalized / max(1, len(words)),
            math.log1p(len(links)),
            float(bool(BY_PREFIX_RE.search(candidate_text))),
            float(bool(WRITTEN_BY_RE.search(candidate_text))),
            float(bool(AUTHOR_ATTRIBUTE_RE.search(semantics))),
            float(bool(BYLINE_ATTRIBUTE_RE.search(semantics))),
            float(
                any(PROFILE_LINK_RE.search(str(link.get("href", ""))) for link in links)
            ),
            float(person_name_shape),
            float(bool(MULTIPLE_NAME_RE.search(candidate_text))),
            float(0 < len(words) <= 20),
            float(_inside(element, "header")),
            float(bool(PUBLISHED_MARKER_RE.search(candidate_text))),
            float(bool(UPDATED_MARKER_RE.search(candidate_text))),
            float(bool(READING_TIME_RE.search(candidate_text))),
            float(element.name.lower() == "time"),
            float(own_itemprop == "datepublished"),
            float(own_itemprop == "datemodified"),
            math.log1p(len(direct_children)),
            math.log1p(sum(item.name.lower() == "span" for item in descendants)),
            float(not descendants),
            _direct_text_length(element) / max(1, text_length),
            float(_inside(element, "article")),
            float(_inside(element, "main")),
            title_distance,
            float(
                title_anchor_index is not None and candidate_index < title_anchor_index
            ),
            float(
                title_anchor is not None
                and element.parent is title_anchor.element.parent
            ),
            math.log1p(_tree_distance(element, title_anchor.element))
            if title_anchor is not None
            else 0.0,
            date_distance,
            float(
                date_anchor_index is not None and candidate_index < date_anchor_index
            ),
            float(
                date_anchor is not None and element.parent is date_anchor.element.parent
            ),
            math.log1p(_tree_distance(element, date_anchor.element))
            if date_anchor is not None
            else 0.0,
        ],
        dtype=np.float32,
    )


def featurize_page(
    page: ParsedPage,
    vocabulary: TagVocabulary,
    semantic_vocabulary: SemanticVocabulary,
    normalizer: FeatureNormalizer | None = None,
) -> PageFeatures:
    tag_lookup = vocabulary.lookup
    semantic_lookup = semantic_vocabulary.lookup

    def encode_tag(name: str | None) -> int:
        return tag_lookup.get(name.lower(), 1) if name else 1

    def encode_semantics(values: Sequence[str]) -> np.ndarray:
        encoded = [
            semantic_lookup.get(value, 1) for value in values[:MAX_SEMANTIC_TOKENS]
        ]
        encoded.extend([0] * (MAX_SEMANTIC_TOKENS - len(encoded)))
        return np.asarray(encoded, dtype=np.int64)

    texts = [
        candidate.element.get_text(" ", strip=True) for candidate in page.candidates
    ]
    title_index = _title_anchor_index(page)
    date_indices = _date_anchor_indices(page, texts)
    rows: list[np.ndarray] = []
    for index, candidate in enumerate(page.candidates):
        nearest_date = _nearest_anchor(index, date_indices)
        rows.append(
            raw_numeric_features(
                candidate,
                len(page.candidates),
                index,
                title_anchor=(
                    page.candidates[title_index] if title_index is not None else None
                ),
                title_anchor_index=title_index,
                date_anchor=(
                    page.candidates[nearest_date] if nearest_date is not None else None
                ),
                date_anchor_index=nearest_date,
                text=texts[index],
            )
        )
    raw = (
        np.stack(rows)
        if rows
        else np.empty((0, len(NUMERIC_FEATURE_NAMES)), dtype=np.float32)
    )
    numeric = normalizer.transform(raw) if normalizer is not None else raw
    return PageFeatures(
        node_ids=np.asarray(
            [candidate.node_id for candidate in page.candidates], dtype=np.int64
        ),
        tag_ids=np.asarray(
            [encode_tag(candidate.element.name) for candidate in page.candidates],
            dtype=np.int64,
        ),
        parent_tag_ids=np.asarray(
            [
                encode_tag(
                    candidate.element.parent.name
                    if isinstance(candidate.element.parent, Tag)
                    else None
                )
                for candidate in page.candidates
            ],
            dtype=np.int64,
        ),
        grandparent_tag_ids=np.asarray(
            [
                encode_tag(
                    candidate.element.parent.parent.name
                    if isinstance(candidate.element.parent, Tag)
                    and isinstance(candidate.element.parent.parent, Tag)
                    else None
                )
                for candidate in page.candidates
            ],
            dtype=np.int64,
        ),
        previous_tag_ids=np.asarray(
            [
                _tag_id_for_sibling(candidate, tag_lookup, next_=False)
                for candidate in page.candidates
            ],
            dtype=np.int64,
        ),
        next_tag_ids=np.asarray(
            [
                _tag_id_for_sibling(candidate, tag_lookup, next_=True)
                for candidate in page.candidates
            ],
            dtype=np.int64,
        ),
        semantic_token_ids=(
            np.stack(
                [
                    encode_semantics(semantic_tokens(candidate.element, texts[index]))
                    for index, candidate in enumerate(page.candidates)
                ]
            )
            if page.candidates
            else np.empty((0, MAX_SEMANTIC_TOKENS), dtype=np.int64)
        ),
        numeric=numeric.astype(np.float32),
    )
