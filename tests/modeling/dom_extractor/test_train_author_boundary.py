from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from eng_universe.extraction.author_boundary import BoundaryFeatures
from eng_universe.extraction.dom import parse_html
from modeling.dom_extractor.commands.train_author_boundary import (
    hard_parent_ids,
    load_author_overrides,
    parent_margin_loss,
)


def test_author_overrides_are_read_without_changing_annotations(tmp_path: Path) -> None:
    path = tmp_path / "overrides.json"
    path.write_text(
        json.dumps(
            {
                "changes": [
                    {"page_id": "a", "old": 12, "new": 13},
                    {"page_id": "b", "old": None, "new": 7},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert load_author_overrides(path) == {"a": (12, 13), "b": (None, 7)}


@pytest.mark.parametrize(
    "changes, message",
    [
        ([{"page_id": "a", "old": 1, "new": 2}] * 2, "duplicate"),
        ([{"page_id": "a", "old": 1, "new": True}], "invalid"),
    ],
)
def test_author_overrides_reject_ambiguous_nodes(
    tmp_path: Path, changes: list[dict[str, object]], message: str
) -> None:
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"changes": changes}), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_author_overrides(path)


def test_hard_parent_mining_requires_extra_content() -> None:
    page = parse_html(
        '<main><div id="broad"><p id="byline">By Ada</p>'
        '<time>September 20, 2026</time></div>'
        '<div id="same"><p id="copy">By Ada</p></div></main>',
        strip_chrome=True,
    )
    node_ids = tuple(candidate.node_id for candidate in page.candidates)
    candidates = BoundaryFeatures(node_ids, np.zeros((len(node_ids), 1)))
    by_id = {
        element.get("id"): node_id
        for node_id, element in page.node_by_id.items()
        if element.get("id")
    }

    assert by_id["broad"] in hard_parent_ids(page, by_id["byline"], candidates)
    assert by_id["same"] not in hard_parent_ids(page, by_id["copy"], candidates)


def test_parent_margin_loss_penalizes_broad_winner() -> None:
    target = torch.tensor([0])
    parents = torch.tensor([[False, True]])
    good = parent_margin_loss(torch.tensor([[3.0, 1.0]]), target, parents, 0.25)
    bad = parent_margin_loss(torch.tensor([[1.0, 3.0]]), target, parents, 0.25)

    assert bad > good
    assert parent_margin_loss(
        torch.tensor([[1.0, 3.0]]), target, torch.zeros_like(parents), 0.25
    ) == 0
