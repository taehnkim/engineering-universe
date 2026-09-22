from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pytest
import torch

from eng_universe.extraction.author_boundary import (
    BOUNDARY_VERSION,
    AuthorBoundaryRanker,
    AuthorBoundaryRefiner,
    BoundaryFeatures,
    boundary_features,
    select_boundary,
)
from eng_universe.extraction.dom import parse_html
from eng_universe.extraction.features import NUMERIC_FEATURE_NAMES


def test_local_search_can_restore_a_complete_multi_author_wrapper() -> None:
    page = parse_html(
        '<article><p class="byline">By: '
        '<a href="/authors/ada">Ada</a> and '
        '<a href="/authors/grace">Grace</a></p>'
        "<time>Sep 20, 2026</time></article>",
        strip_chrome=True,
    )
    seed = next(
        candidate.node_id
        for candidate in page.candidates
        if candidate.element.name == "a"
        and candidate.element.get_text(strip=True) == "Ada"
    )
    byline = next(
        candidate.node_id
        for candidate in page.candidates
        if candidate.element.name == "p"
    )
    candidates = boundary_features(
        page,
        seed,
        np.zeros(len(page.candidates), dtype=np.float32),
        np.zeros((len(page.candidates), len(NUMERIC_FEATURE_NAMES)), dtype=np.float32),
    )

    assert seed in candidates.node_ids
    assert byline in candidates.node_ids
    assert candidates.values[candidates.node_ids.index(byline), -13] == 2


def test_refiner_changes_node_only_when_best_score_clears_margin() -> None:
    candidates = BoundaryFeatures(
        (1, 2),
        np.asarray([[0.0], [1.0]], dtype=np.float32),
    )

    class ScoreFeature(torch.nn.Module):
        def forward(self, values: torch.Tensor) -> torch.Tensor:
            return values[:, 0]

    ranker = ScoreFeature()
    mean = np.zeros(1, dtype=np.float32)
    std = np.ones(1, dtype=np.float32)

    assert select_boundary(candidates, 1, ranker, mean, std, 0.5) == 2
    assert select_boundary(candidates, 1, ranker, mean, std, 1.0) == 1


def test_refiner_rejects_artifact_from_another_base_checkpoint(tmp_path: Path) -> None:
    base = tmp_path / "base.pt"
    base.write_bytes(b"base checkpoint")
    artifact = tmp_path / "boundary.pt"
    torch.save(
        {
            "version": BOUNDARY_VERSION,
            "base_sha256": hashlib.sha256(b"different checkpoint").hexdigest(),
            "feature_count": 1,
            "mean": np.zeros(1, dtype=np.float32),
            "std": np.ones(1, dtype=np.float32),
            "change_margin": 0.0,
            "model_state": AuthorBoundaryRanker(1).state_dict(),
        },
        artifact,
    )

    with pytest.raises(ValueError, match="another base checkpoint"):
        AuthorBoundaryRefiner(artifact, base)
