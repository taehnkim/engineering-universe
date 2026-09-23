"""Optional local ranker for the boundary of an already detected author byline."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from bs4 import Tag
from torch import nn

from eng_universe.extraction.contract import FIELDS, Field
from eng_universe.extraction.dom import ParsedPage
from eng_universe.extraction.features import (
    AUTHOR_LABEL_RE,
    BY_PREFIX_RE,
    DATE_LIKE_RE,
    PROFILE_LINK_RE,
    READING_TIME_RE,
    RELATIVE_DATE_RE,
)

BOUNDARY_VERSION = "author-boundary-v1"
AUTHOR_FIELD_INDEX = tuple(FIELDS).index(Field.AUTHORS)
MAX_LOCAL_CANDIDATES = 192
WORD_RE = re.compile(r"\w+", re.UNICODE)
HANDLE_RE = re.compile(r"(?<!\w)@[\w.-]+", re.UNICODE)
TAGS = ("a", "p", "div", "span", "dl", "dd", "em", "strong", "section")


class AuthorBoundaryRanker(nn.Module):
    """Give each nearby node one score; a margin controls when to change nodes."""

    def __init__(self, feature_count: int, hidden_dim: int = 24) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(feature_count, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, 1),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.network(features).squeeze(-1)


@dataclass(frozen=True)
class BoundaryFeatures:
    node_ids: tuple[int, ...]
    values: np.ndarray


def local_candidate_ids(
    page: ParsedPage,
    seed_id: int,
    author_scores: np.ndarray,
) -> tuple[int, ...]:
    """Search the selected node, five ancestors, and descendants seven levels deep."""

    seed = page.candidate(seed_id)
    id_by_identity = page.id_by_element_identity
    node_to_index = {
        candidate.node_id: i for i, candidate in enumerate(page.candidates)
    }
    ancestors: list[int] = [seed_id]
    parent = seed.parent
    for _ in range(5):
        if not isinstance(parent, Tag):
            break
        node_id = id_by_identity.get(id(parent))
        if node_id is not None:
            ancestors.append(node_id)
        parent = parent.parent

    descendants: list[tuple[int, int]] = []
    frontier = [(seed, 0)]
    for element, depth in frontier:
        if depth >= 7:
            continue
        for child in element.find_all(True, recursive=False):
            frontier.append((child, depth + 1))
            node_id = id_by_identity.get(id(child))
            if node_id is not None and child.get_text(strip=True):
                descendants.append((node_id, depth + 1))

    if len(ancestors) + len(descendants) > MAX_LOCAL_CANDIDATES:
        descendants.sort(
            key=lambda value: (
                value[1] <= 3,
                float(author_scores[node_to_index[value[0]]]),
                -value[1],
            ),
            reverse=True,
        )
        descendants = descendants[: MAX_LOCAL_CANDIDATES - len(ancestors)]
    return tuple(dict.fromkeys((*ancestors, *(node_id for node_id, _ in descendants))))


def _word_coverage(left: Counter[str], right: Counter[str]) -> float:
    """Fraction of left-side words present in the right-side text."""

    return sum((left & right).values()) / max(1, sum(left.values()))


def _profile_links(element: Tag) -> int:
    links = [element] if element.name == "a" else element.find_all("a")
    return len(
        {
            str(link.get("href", "")).split("?")[0]
            for link in links
            if PROFILE_LINK_RE.search(str(link.get("href", "")))
        }
    )


def boundary_features(
    page: ParsedPage,
    seed_id: int,
    author_scores: np.ndarray,
    numeric_features: np.ndarray,
) -> BoundaryFeatures:
    """Describe how a local node differs from the checkpoint's selected node."""

    node_ids = local_candidate_ids(page, seed_id, author_scores)
    node_to_index = {
        candidate.node_id: i for i, candidate in enumerate(page.candidates)
    }
    seed = page.candidate(seed_id)
    seed_text = seed.get_text(" ", strip=True)
    seed_words = Counter(WORD_RE.findall(seed_text.casefold()))
    seed_links = _profile_links(seed)
    seed_score = float(author_scores[node_to_index[seed_id]])
    rows: list[np.ndarray] = []
    for node_id in node_ids:
        element = page.candidate(node_id)
        text = element.get_text(" ", strip=True)
        words = Counter(WORD_RE.findall(text.casefold()))
        index = node_to_index[node_id]
        ancestor = node_id != seed_id and seed in element.descendants
        descendant = node_id != seed_id and element in seed.descendants
        links = _profile_links(element)
        own_attrs = " ".join(
            str(element.get(name, ""))
            for name in ("class", "id", "itemprop", "aria-label", "rel")
        ).casefold()
        extra = np.asarray(
            [
                float(node_id == seed_id),
                float(ancestor),
                float(descendant),
                np.clip(float(author_scores[index] - seed_score), -20.0, 20.0),
                np.log1p(len(text)) - np.log1p(len(seed_text)),
                _word_coverage(seed_words, words),
                _word_coverage(words, seed_words),
                float(bool(DATE_LIKE_RE.search(text))),
                float(bool(RELATIVE_DATE_RE.search(text))),
                float(bool(READING_TIME_RE.search(text))),
                float(bool(BY_PREFIX_RE.search(text))),
                float(bool(AUTHOR_LABEL_RE.search(text))),
                float(bool(re.search(r"\bauthor\b|\bbyline\b", own_attrs))),
                float(len(HANDLE_RE.findall(text))),
                float(links),
                float(links - seed_links),
                float(len(element.find_all("a"))),
                float(len(element.find_all(True, recursive=False))),
                *(float(element.name == tag) for tag in TAGS),
            ],
            dtype=np.float32,
        )
        rows.append(np.concatenate((numeric_features[index], extra)))
    return BoundaryFeatures(node_ids, np.stack(rows))


def select_boundary(
    candidates: BoundaryFeatures,
    seed_id: int,
    ranker: AuthorBoundaryRanker,
    mean: np.ndarray,
    std: np.ndarray,
    change_margin: float,
) -> int:
    """Keep the original node unless the local ranker wins by a clear margin."""

    normalized = np.clip((candidates.values - mean) / std, -6.0, 6.0)
    with torch.inference_mode():
        scores = ranker(torch.from_numpy(normalized.astype(np.float32)))
    seed_index = candidates.node_ids.index(seed_id)
    best_index = int(scores.argmax())
    if float(scores[best_index] - scores[seed_index]) <= change_margin:
        return seed_id
    return candidates.node_ids[best_index]


class AuthorBoundaryRefiner:
    """Load a ranker trained against one exact base checkpoint."""

    def __init__(self, artifact_path: str | Path, base_checkpoint: str | Path) -> None:
        artifact = torch.load(
            Path(artifact_path), map_location="cpu", weights_only=False
        )
        if artifact.get("version") != BOUNDARY_VERSION:
            raise ValueError("author boundary artifact has an incompatible version")
        base_sha256 = hashlib.sha256(Path(base_checkpoint).read_bytes()).hexdigest()
        if artifact.get("base_sha256") != base_sha256:
            raise ValueError(
                "author boundary artifact was trained for another base checkpoint"
            )
        self.ranker = AuthorBoundaryRanker(int(artifact["feature_count"]))
        self.ranker.load_state_dict(artifact["model_state"])
        self.ranker.eval()
        self.mean = np.asarray(artifact["mean"], dtype=np.float32)
        self.std = np.asarray(artifact["std"], dtype=np.float32)
        self.change_margin = float(artifact["change_margin"])

    def refine(
        self,
        page: ParsedPage,
        seed_id: int,
        author_scores: np.ndarray,
        numeric_features: np.ndarray,
    ) -> int:
        candidates = boundary_features(page, seed_id, author_scores, numeric_features)
        return select_boundary(
            candidates,
            seed_id,
            self.ranker,
            self.mean,
            self.std,
            self.change_margin,
        )
