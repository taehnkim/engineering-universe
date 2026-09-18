from pathlib import Path

import pytest

from eng_universe.extraction.manifest import (
    DatasetManifest,
    PageRecord,
    canonical_url,
    website_splits,
)


def test_website_split_is_deterministic_and_keeps_website_together() -> None:
    websites = [f"site-{index}.test" for index in range(17)]
    first = website_splits(websites)
    second = website_splits(list(reversed(websites)))
    assert first == second
    assert set(first.values()) == {"train", "validation", "test"}
    assert len(first) == len(websites)


def test_target_split_counts() -> None:
    websites = [f"site-{index}.test" for index in range(29)]
    result = website_splits(websites, validation_count=8, test_count=1)
    assert list(result.values()).count("train") == 20
    assert list(result.values()).count("validation") == 8
    assert list(result.values()).count("test") == 1


def test_manifest_rejects_duplicate_canonical_urls(tmp_path: Path) -> None:
    def page(page_id: str, url: str) -> PageRecord:
        return PageRecord(
            page_id=page_id,
            source_id="source",
            company="Company",
            website="example.com",
            url=url,
            html_path=f"html/{page_id}.html",
            split="train",
            capture_kind="browser",
            html_hash="abc",
        )

    manifest = DatasetManifest(
        1,
        (
            page("one", "https://example.com/post/?utm_source=x"),
            page("two", "https://example.com/post#heading"),
        ),
    )
    with pytest.raises(ValueError, match="duplicate URL"):
        manifest.save(tmp_path / "manifest.json")
    assert canonical_url("HTTPS://EXAMPLE.COM/post/?source=x#top") == (
        "https://example.com/post"
    )
