"""Dataset manifest and website-level split helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit


Split = Literal["train", "validation", "test"]
CaptureKind = Literal["http", "browser"]


@dataclass(frozen=True, slots=True)
class PageRecord:
    page_id: str
    source_id: str
    company: str
    website: str
    url: str
    html_path: str
    split: Split
    capture_kind: CaptureKind
    html_hash: str
    scraped_at: str | None = None
    is_article: bool = True

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> PageRecord:
        return cls(**value)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    version: int
    pages: tuple[PageRecord, ...]

    @classmethod
    def load(cls, path: Path) -> DatasetManifest:
        value = json.loads(path.read_text(encoding="utf-8"))
        manifest = cls(
            version=int(value["version"]),
            pages=tuple(PageRecord.from_dict(page) for page in value["pages"]),
        )
        manifest.validate()
        return manifest

    def validate(self) -> None:
        page_ids: set[str] = set()
        urls: dict[str, str] = {}
        website_splits_seen: dict[str, Split] = {}
        for page in self.pages:
            if page.page_id in page_ids:
                raise ValueError(f"duplicate page_id: {page.page_id}")
            page_ids.add(page.page_id)
            normalized_url = canonical_url(page.url)
            if normalized_url in urls:
                raise ValueError(
                    f"duplicate URL: {page.url} ({urls[normalized_url]}, {page.page_id})"
                )
            urls[normalized_url] = page.page_id
            previous_split = website_splits_seen.setdefault(page.website, page.split)
            if previous_split != page.split:
                raise ValueError(
                    f"website {page.website} appears in both "
                    f"{previous_split} and {page.split}"
                )
            if self.version >= 2 and page.scraped_at is None:
                raise ValueError(f"{page.page_id}: scraped_at is required in manifest v2")
            if page.scraped_at is not None:
                try:
                    datetime.fromisoformat(page.scraped_at.replace("Z", "+00:00"))
                except ValueError as exc:
                    raise ValueError(
                        f"{page.page_id}: scraped_at must be an ISO 8601 timestamp"
                    ) from exc

    def save(self, path: Path) -> None:
        self.validate()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"version": self.version, "pages": [asdict(page) for page in self.pages]},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


def canonical_url(url: str) -> str:
    """Return a stable URL identity, ignoring fragments and tracking queries."""

    parsed = urlsplit(url)
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def website_splits(
    websites: list[str],
    *,
    validation_count: int | None = None,
    test_count: int | None = None,
) -> dict[str, Split]:
    """Assign whole websites to deterministic, roughly 70/15/15 splits."""

    unique = sorted(
        set(websites),
        key=lambda website: hashlib.sha256(website.encode("utf-8")).hexdigest(),
    )
    if len(unique) < 3:
        raise ValueError("at least three websites are required for website-level splits")
    test_count = test_count if test_count is not None else max(1, round(len(unique) * 0.15))
    validation_count = (
        validation_count
        if validation_count is not None
        else max(1, round(len(unique) * 0.15))
    )
    if test_count < 1 or validation_count < 1:
        raise ValueError("test_count and validation_count must be positive")
    if test_count + validation_count >= len(unique):
        raise ValueError("split counts must leave at least one training website")
    result: dict[str, Split] = {}
    for index, website in enumerate(unique):
        if index < test_count:
            result[website] = "test"
        elif index < test_count + validation_count:
            result[website] = "validation"
        else:
            result[website] = "train"
    return result
