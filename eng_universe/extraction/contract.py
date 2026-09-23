"""Shared extraction and annotation contracts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Literal


class Field(str, Enum):
    ARTICLE = "article"
    TITLE = "title"
    AUTHORS = "authors"
    DATE = "date"


FIELDS: tuple[Field, ...] = tuple(Field)
ReviewStatus = Literal["draft", "reviewed", "legacy"]


@dataclass(frozen=True, slots=True)
class Annotation:
    page_id: str
    html_hash: str
    labels: dict[Field, int | None]
    needs_review: bool = False
    review_status: ReviewStatus = "legacy"
    legacy_labels: dict[str, int | None] | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Annotation:
        raw_labels = value.get("labels", {})
        labels = {
            field: (
                raw_labels.get("author")
                if field == Field.AUTHORS and field.value not in raw_labels
                else raw_labels.get(field.value)
            )
            for field in FIELDS
        }
        for field, node_id in labels.items():
            if node_id is not None and (
                isinstance(node_id, bool) or not isinstance(node_id, int) or node_id < 0
            ):
                raise ValueError(f"{field.value} must be a non-negative node ID or null")
        return cls(
            page_id=str(value["page_id"]),
            html_hash=str(value["html_hash"]),
            labels=labels,
            needs_review=bool(value.get("needs_review", False)),
            review_status=_review_status(value.get("review_status", "legacy")),
            legacy_labels={
                key: node_id for key, node_id in raw_labels.items()
                if key not in {field.value for field in FIELDS} and key != "author"
            } or None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_id": self.page_id,
            "html_hash": self.html_hash,
            "needs_review": self.needs_review,
            "review_status": self.review_status,
            "labels": {
                **(self.legacy_labels or {}),
                **{field.value: self.labels[field] for field in FIELDS},
            },
        }


def _review_status(value: object) -> ReviewStatus:
    if value not in {"draft", "reviewed", "legacy"}:
        raise ValueError("review_status must be draft, reviewed, or legacy")
    return value  # type: ignore[return-value]


def load_annotation(path: Path) -> Annotation:
    return Annotation.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save_annotation(path: Path, annotation: Annotation) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(annotation.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
