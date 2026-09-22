"""Compact semantic and structural features shared by training and inference."""

from __future__ import annotations

import math
import re
from bisect import bisect_left
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import asdict, dataclass

import numpy as np
from bs4 import Tag

from eng_universe.extraction.dom import Candidate, ParsedPage

FEATURE_VERSION = "semantic-v3"
MAX_SEMANTIC_VOCABULARY = 64
MAX_ATTRIBUTE_TOKENS = 8
MAX_TEXT_SHAPE_TOKENS = 12
SEMANTIC_PRIORITY_TOKENS = (
    "role:author",
    "role:byline",
    "role:contributor",
    "role:profile",
    "role:person",
    "role:team",
    "role:date",
    "role:published",
    "role:updated",
    "role:modified",
    "role:headline",
    "role:title",
    "role:summary",
    "role:subtitle",
    "role:acknowledgements",
    "role:content",
    "schema:author",
    "schema:name",
    "schema:headline",
    "schema:date_published",
    "schema:date_modified",
    "relation:author",
    "accessible:author",
    "accessible:published",
    "accessible:updated",
    "phrase:by_prefix",
    "phrase:by_colon",
    "phrase:written_by",
    "phrase:article_written_by",
    "phrase:author_label",
    "phrase:acknowledgements",
    "phrase:contributors",
    "phrase:thanks_to",
    "phrase:published",
    "phrase:updated",
    "shape:single_name",
    "shape:multiple_names",
    "shape:comma_list",
    "shape:and_last_name",
    "shape:handle",
    "shape:organization",
    "shape:name_and_role",
    "shape:one_link",
    "shape:multiple_links",
    "shape:short_text",
    "shape:medium_text",
    "shape:long_text",
    "shape:very_long_text",
    "shape:absolute_date",
    "shape:relative_date",
    "shape:reading_time",
    "shape:date_plus_reading_time",
)

ROLE_WORDS = {
    "author": "author",
    "authors": "author",
    "byline": "byline",
    "contributor": "contributor",
    "contributors": "contributor",
    "profile": "profile",
    "person": "person",
    "people": "person",
    "team": "team",
    "date": "date",
    "published": "published",
    "publish": "published",
    "updated": "updated",
    "update": "updated",
    "modified": "modified",
    "headline": "headline",
    "title": "title",
    "summary": "summary",
    "subtitle": "subtitle",
    "subhead": "subtitle",
    "standfirst": "summary",
    "dek": "summary",
    "excerpt": "summary",
    "description": "summary",
    "lead": "summary",
    "acknowledgement": "acknowledgements",
    "acknowledgements": "acknowledgements",
    "acknowledgment": "acknowledgements",
    "acknowledgments": "acknowledgements",
    "content": "content",
}

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
BY_COLON_RE = re.compile(r"^\s*by\s*:", re.IGNORECASE)
WRITTEN_BY_RE = re.compile(r"^\s*written\s+by\b", re.IGNORECASE)
WRITTEN_BY_ANY_RE = re.compile(r"\bwritten\s+by\b", re.IGNORECASE)
ARTICLE_WRITTEN_BY_RE = re.compile(
    r"^\s*(?:this\s+)?article\s+(?:was\s+)?written\s+by\b", re.IGNORECASE
)
AUTHOR_LABEL_RE = re.compile(r"^\s*authors?\s*:", re.IGNORECASE)
ACKNOWLEDGEMENTS_RE = re.compile(r"\backnowledg(?:e)?ments?\b", re.IGNORECASE)
CONTRIBUTOR_RE = re.compile(r"\bcontributors?|contributions?\b", re.IGNORECASE)
THANKS_TO_RE = re.compile(r"\b(?:special\s+)?thanks\s+to\b", re.IGNORECASE)
AND_LAST_NAME_RE = re.compile(
    r"(?:\band\b|&)\s+[\w.'’\-]+(?:\s+[\w.'’\-]+){0,4}[.!]?\s*$",
    re.IGNORECASE | re.UNICODE,
)
ORGANIZATION_AUTHOR_RE = re.compile(
    r"\b(?:engineering|research|developer|platform|security|infrastructure|applied\s+ai)\s+team\b|\bteam\b",
    re.IGNORECASE,
)
AUTHOR_ROLE_RE = re.compile(
    r"\b(?:engineer|researcher|scientist|member|staff|director|lead|manager|editor)\b",
    re.IGNORECASE,
)
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
    "after_title",
    "near_title",
    "distance_from_article_start",
    "distance_from_article_end",
    "before_article",
    "after_article",
    "near_document_end",
    "contains_acknowledgements_marker",
    "contains_contributor_marker",
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
    42,
    43,
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
    """Bounded vocabulary for explicit role, phrase, and text-shape tokens."""

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
                counts.update(attribute_semantic_tokens(candidate.element))
                counts.update(text_shape_tokens(candidate.element))
        available = max(0, max_size - 2)
        priority = list(SEMANTIC_PRIORITY_TOKENS[:available])
        frequent = sorted(
            (token for token in counts if token not in priority),
            key=lambda token: (-counts[token], token),
        )
        selected = (priority + frequent)[:available]
        return cls((cls.PAD, cls.UNKNOWN, *selected))

    @property
    def lookup(self) -> dict[str, int]:
        return {token: index for index, token in enumerate(self.tokens)}

    def encode(self, values: Sequence[str], max_tokens: int) -> np.ndarray:
        lookup = self.lookup
        encoded = [lookup.get(value, 1) for value in values[:max_tokens]]
        encoded.extend([0] * (max_tokens - len(encoded)))
        return np.asarray(encoded, dtype=np.int64)

    def to_dict(self) -> dict[str, object]:
        return {
            "tokens": list(self.tokens),
            "max_attribute_tokens": MAX_ATTRIBUTE_TOKENS,
            "max_text_shape_tokens": MAX_TEXT_SHAPE_TOKENS,
        }

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
    attribute_token_ids: np.ndarray
    text_shape_token_ids: np.ndarray
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


def _deduplicate(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def attribute_semantic_tokens(element: Tag) -> tuple[str, ...]:
    """Return only extraction-role attributes; discard generic CSS utilities."""

    tokens: list[str] = []
    for name, value in _attribute_values(element):
        words = _normalize_token_source(value)
        word_set = set(words)
        if {"written", "by"} <= word_set:
            tokens.append("role:byline")
        tokens.extend(
            f"role:{role}" for word in words if (role := ROLE_WORDS.get(word))
        )
        if name == "itemprop":
            if "author" in word_set:
                tokens.append("schema:author")
            if "name" in word_set:
                tokens.append("schema:name")
            if "headline" in word_set:
                tokens.append("schema:headline")
            if {"date", "published"} <= word_set:
                tokens.append("schema:date_published")
            if {"date", "modified"} <= word_set:
                tokens.append("schema:date_modified")
        elif name == "rel" and "author" in word_set:
            tokens.append("relation:author")
        elif name == "aria-label":
            for marker in ("author", "published", "updated"):
                if marker in word_set:
                    tokens.append(f"accessible:{marker}")
    return _deduplicate(tokens)


def _person_name_shape(text: str, words: Sequence[str]) -> bool:
    capitalized = sum(
        bool(word) and word[0].isalpha() and word[0].isupper() for word in words
    )
    return bool(
        re.fullmatch(r"@[\w.-]+", text.strip(), re.UNICODE)
        or (
            1 <= len(words) <= 8
            and capitalized / max(1, len(words)) >= 0.5
            and not DATE_LIKE_RE.search(text)
        )
    )


def text_shape_tokens(
    element: Tag,
    text: str | None = None,
    *,
    link_count: int | None = None,
) -> tuple[str, ...]:
    """Describe phrases and shapes without learning individual names or prose."""

    candidate_text = element.get_text(" ", strip=True) if text is None else text
    words = candidate_text.split()
    tokens: list[str] = []
    if 0 < len(words) <= 20:
        tokens.append("shape:short_text")
    elif len(words) <= 40:
        tokens.append("shape:medium_text")
    elif len(words) <= 100:
        tokens.append("shape:long_text")
    else:
        tokens.append("shape:very_long_text")
    resolved_link_count = (
        len(element.find_all("a")) if link_count is None else link_count
    )
    if resolved_link_count == 1:
        tokens.append("shape:one_link")
    elif resolved_link_count > 1:
        tokens.append("shape:multiple_links")
    # Broad wrappers inherit every phrase in their descendants. Their length
    # and link shape are useful; repeated regex scans and inherited bylines are
    # not. This also keeps page-level candidates cheap.
    if len(candidate_text) > 500 or len(words) > 80:
        return _deduplicate(tokens)
    if BY_PREFIX_RE.search(candidate_text):
        tokens.append("phrase:by_prefix")
    if BY_COLON_RE.search(candidate_text):
        tokens.append("phrase:by_colon")
    if WRITTEN_BY_ANY_RE.search(candidate_text):
        tokens.append("phrase:written_by")
    if ARTICLE_WRITTEN_BY_RE.search(candidate_text):
        tokens.append("phrase:article_written_by")
    if AUTHOR_LABEL_RE.search(candidate_text):
        tokens.append("phrase:author_label")
    if ACKNOWLEDGEMENTS_RE.search(candidate_text):
        tokens.append("phrase:acknowledgements")
    if CONTRIBUTOR_RE.search(candidate_text):
        tokens.append("phrase:contributors")
    if THANKS_TO_RE.search(candidate_text):
        tokens.append("phrase:thanks_to")
    if PUBLISHED_MARKER_RE.search(candidate_text):
        tokens.append("phrase:published")
    if UPDATED_MARKER_RE.search(candidate_text):
        tokens.append("phrase:updated")
    if element.name.lower() not in {
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "title",
    } and _person_name_shape(candidate_text, words):
        tokens.append("shape:single_name")
    if MULTIPLE_NAME_RE.search(candidate_text):
        tokens.append("shape:multiple_names")
    if "," in candidate_text:
        tokens.append("shape:comma_list")
    if AND_LAST_NAME_RE.search(candidate_text):
        tokens.append("shape:and_last_name")
    if re.fullmatch(r"\s*@[\w.-]+\s*", candidate_text, re.UNICODE):
        tokens.append("shape:handle")
    if ORGANIZATION_AUTHOR_RE.search(candidate_text):
        tokens.append("shape:organization")
    if AUTHOR_ROLE_RE.search(candidate_text):
        tokens.append("shape:name_and_role")
    has_date = bool(DATE_LIKE_RE.search(candidate_text))
    has_reading_time = bool(READING_TIME_RE.search(candidate_text))
    if has_date:
        tokens.append("shape:absolute_date")
    if RELATIVE_DATE_RE.search(candidate_text):
        tokens.append("shape:relative_date")
    if has_reading_time:
        tokens.append("shape:reading_time")
    if has_date and has_reading_time:
        tokens.append("shape:date_plus_reading_time")
    return _deduplicate(tokens)


def semantic_tokens(element: Tag, text: str | None = None) -> tuple[str, ...]:
    """Compatibility view of both independent semantic channels."""

    return attribute_semantic_tokens(element) + text_shape_tokens(element, text)


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


def _article_anchor_range(
    page: ParsedPage, descendants: Sequence[Sequence[Tag]] | None = None
) -> tuple[int | None, int | None]:
    """Return the first article/main candidate and the end of its subtree."""

    for preferred_tag in ("article", "main"):
        for start, candidate in enumerate(page.candidates):
            if candidate.element.name.lower() != preferred_tag:
                continue
            values = (
                candidate.element.find_all(True)
                if descendants is None
                else descendants[start]
            )
            descendant_ids = {id(element) for element in values}
            end = max(
                (
                    index
                    for index, item in enumerate(page.candidates)
                    if id(item.element) in descendant_ids
                ),
                default=start,
            )
            return start, end
    return None, None


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
    if not anchors:
        return None
    insertion = bisect_left(anchors, index)
    choices = anchors[max(0, insertion - 1) : min(len(anchors), insertion + 1)]
    return min(choices, key=lambda anchor: (abs(anchor - index), anchor))


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
    article_start_index: int | None = None,
    article_end_index: int | None = None,
    descendants: Sequence[Tag] | None = None,
    text: str | None = None,
) -> np.ndarray:
    element = candidate.element
    candidate_text = element.get_text(" ", strip=True) if text is None else text
    text_length = len(candidate_text)
    words = candidate_text.split()
    is_local_text = text_length <= 500 and len(words) <= 80
    position = candidate_index / max(1, candidate_count - 1)
    descendant_elements = element.find_all(True) if descendants is None else descendants
    direct_children = [item for item in descendant_elements if item.parent is element]
    links = [item for item in descendant_elements if item.name.lower() == "a"]
    semantics = _semantic_attribute_text(element, direct_children)
    own_itemprop = str(element.get("itemprop", "")).lower()
    capitalized = sum(
        bool(word) and word[0].isalpha() and word[0].isupper() for word in words
    )
    person_name_shape = _person_name_shape(candidate_text, words)
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
    article_start_distance = (
        abs(candidate_index - article_start_index) / max(1, candidate_count - 1)
        if article_start_index is not None
        else 1.0
    )
    article_end_distance = (
        abs(candidate_index - article_end_index) / max(1, candidate_count - 1)
        if article_end_index is not None
        else 1.0
    )
    return np.asarray(
        [
            math.log1p(text_length),
            math.log1p(_paragraph_count(element, descendant_elements)),
            _link_text_fraction(element, text_length, links),
            math.log1p(_depth(element)),
            position,
            float(element.has_attr("datetime")),
            float(is_local_text and bool(DATE_LIKE_RE.search(candidate_text))),
            float(is_local_text and bool(RELATIVE_DATE_RE.search(candidate_text))),
            math.log1p(len(words)),
            capitalized / max(1, len(words)),
            math.log1p(len(links)),
            float(is_local_text and bool(BY_PREFIX_RE.search(candidate_text))),
            float(is_local_text and bool(WRITTEN_BY_RE.search(candidate_text))),
            float(bool(AUTHOR_ATTRIBUTE_RE.search(semantics))),
            float(bool(BYLINE_ATTRIBUTE_RE.search(semantics))),
            float(
                any(PROFILE_LINK_RE.search(str(link.get("href", ""))) for link in links)
            ),
            float(person_name_shape),
            float(is_local_text and bool(MULTIPLE_NAME_RE.search(candidate_text))),
            float(0 < len(words) <= 20),
            float(_inside(element, "header")),
            float(is_local_text and bool(PUBLISHED_MARKER_RE.search(candidate_text))),
            float(is_local_text and bool(UPDATED_MARKER_RE.search(candidate_text))),
            float(is_local_text and bool(READING_TIME_RE.search(candidate_text))),
            float(element.name.lower() == "time"),
            float(own_itemprop == "datepublished"),
            float(own_itemprop == "datemodified"),
            math.log1p(len(direct_children)),
            math.log1p(
                sum(item.name.lower() == "span" for item in descendant_elements)
            ),
            float(not descendant_elements),
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
            float(
                title_anchor_index is not None and candidate_index > title_anchor_index
            ),
            float(
                title_anchor_index is not None
                and abs(candidate_index - title_anchor_index)
                <= max(8, int(candidate_count * 0.03))
            ),
            article_start_distance,
            article_end_distance,
            float(
                article_start_index is not None
                and candidate_index < article_start_index
            ),
            float(
                article_end_index is not None and candidate_index > article_end_index
            ),
            float(position >= 0.9),
            float(is_local_text and bool(ACKNOWLEDGEMENTS_RE.search(candidate_text))),
            float(is_local_text and bool(CONTRIBUTOR_RE.search(candidate_text))),
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

    def encode_semantics(values: Sequence[str], max_tokens: int) -> np.ndarray:
        encoded = [semantic_lookup.get(value, 1) for value in values[:max_tokens]]
        encoded.extend([0] * (max_tokens - len(encoded)))
        return np.asarray(encoded, dtype=np.int64)

    texts = [
        candidate.element.get_text(" ", strip=True) for candidate in page.candidates
    ]
    descendants = [candidate.element.find_all(True) for candidate in page.candidates]
    link_counts = [
        sum(element.name.lower() == "a" for element in values) for values in descendants
    ]
    title_index = _title_anchor_index(page)
    article_start_index, article_end_index = _article_anchor_range(page, descendants)
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
                article_start_index=article_start_index,
                article_end_index=article_end_index,
                descendants=descendants[index],
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
        attribute_token_ids=(
            np.stack(
                [
                    encode_semantics(
                        attribute_semantic_tokens(candidate.element),
                        MAX_ATTRIBUTE_TOKENS,
                    )
                    for index, candidate in enumerate(page.candidates)
                ]
            )
            if page.candidates
            else np.empty((0, MAX_ATTRIBUTE_TOKENS), dtype=np.int64)
        ),
        text_shape_token_ids=(
            np.stack(
                [
                    encode_semantics(
                        text_shape_tokens(
                            candidate.element,
                            texts[index],
                            link_count=link_counts[index],
                        ),
                        MAX_TEXT_SHAPE_TOKENS,
                    )
                    for index, candidate in enumerate(page.candidates)
                ]
            )
            if page.candidates
            else np.empty((0, MAX_TEXT_SHAPE_TOKENS), dtype=np.int64)
        ),
        numeric=numeric.astype(np.float32),
    )
