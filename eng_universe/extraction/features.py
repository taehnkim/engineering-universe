"""Structural candidate features shared by training and inference."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from typing import Iterable, Sequence

import numpy as np
from bs4 import Tag

from eng_universe.extraction.dom import Candidate, ParsedPage


DATE_LIKE_RE = re.compile(
    r"(?:\b(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])\b)"
    r"|(?:\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}\b)",
    re.IGNORECASE,
)

NUMERIC_FEATURE_NAMES = (
    "log_descendant_text_length",
    "log_descendant_paragraph_count",
    "descendant_link_text_fraction",
    "log_dom_depth",
    "relative_document_position",
    "has_datetime_attribute",
    "contains_date_like_text",
)
CONTINUOUS_FEATURE_INDICES = (0, 1, 2, 3, 4)


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
        return cls(tuple(float(item) for item in mean), tuple(float(item) for item in std))

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
    numeric: np.ndarray


def _depth(element: Tag) -> int:
    depth = 0
    parent = element.parent
    while isinstance(parent, Tag):
        depth += 1
        parent = parent.parent
    return depth


def _paragraph_count(element: Tag) -> int:
    return (1 if element.name.lower() == "p" else 0) + len(element.find_all("p"))


def _link_text_fraction(element: Tag, text_length: int) -> float:
    if text_length == 0:
        return 0.0
    link_length = 0
    if element.name.lower() == "a":
        link_length += len(element.get_text(" ", strip=True))
    link_length += sum(
        len(link.get_text(" ", strip=True)) for link in element.find_all("a")
    )
    return min(1.0, link_length / text_length)


def raw_numeric_features(candidate: Candidate, candidate_count: int) -> np.ndarray:
    element = candidate.element
    text = element.get_text(" ", strip=True)
    text_length = len(text)
    position = candidate.node_id / max(1, candidate_count - 1)
    return np.asarray(
        [
            math.log1p(text_length),
            math.log1p(_paragraph_count(element)),
            _link_text_fraction(element, text_length),
            math.log1p(_depth(element)),
            position,
            float(element.has_attr("datetime")),
            float(bool(DATE_LIKE_RE.search(text))),
        ],
        dtype=np.float32,
    )


def featurize_page(
    page: ParsedPage,
    vocabulary: TagVocabulary,
    normalizer: FeatureNormalizer | None = None,
) -> PageFeatures:
    raw = np.stack(
        [raw_numeric_features(candidate, len(page.candidates)) for candidate in page.candidates]
    ) if page.candidates else np.empty((0, len(NUMERIC_FEATURE_NAMES)), dtype=np.float32)
    numeric = normalizer.transform(raw) if normalizer is not None else raw
    return PageFeatures(
        node_ids=np.asarray([candidate.node_id for candidate in page.candidates], dtype=np.int64),
        tag_ids=np.asarray(
            [vocabulary.encode(candidate.element.name) for candidate in page.candidates],
            dtype=np.int64,
        ),
        parent_tag_ids=np.asarray(
            [
                vocabulary.encode(
                    candidate.element.parent.name
                    if isinstance(candidate.element.parent, Tag)
                    else None
                )
                for candidate in page.candidates
            ],
            dtype=np.int64,
        ),
        numeric=numeric.astype(np.float32),
    )
