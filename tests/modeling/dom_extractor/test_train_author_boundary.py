from __future__ import annotations

import json
from pathlib import Path

import pytest

from modeling.dom_extractor.commands.train_author_boundary import (
    load_author_overrides,
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
