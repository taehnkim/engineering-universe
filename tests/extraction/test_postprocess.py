from datetime import datetime, timezone

import pytest

from eng_universe.extraction.postprocess import (
    derive_published_at,
    extract_relative_publication_date,
    resolve_relative_date,
)


def test_relative_publication_date_ignores_reading_time() -> None:
    assert extract_relative_publication_date("5 min read") is None
    assert extract_relative_publication_date("5 min read · 2 days ago") == "2 days ago"


def test_relative_publication_date_uses_scrape_timestamp() -> None:
    assert resolve_relative_date(
        "Published 2 days ago",
        datetime(2026, 9, 19, 12, 30, tzinfo=timezone.utc),
    ) == "2026-09-17T12:30:00Z"


def test_month_subtraction_clamps_day() -> None:
    assert resolve_relative_date("one month ago", "2024-03-31T10:00:00Z") == (
        "2024-02-29T10:00:00Z"
    )


def test_derive_published_at_prefers_absolute_date() -> None:
    assert derive_published_at(
        "September 10, 2026", "2 days ago", "2026-09-19T12:30:00Z"
    ) == "September 10, 2026"


def test_invalid_relative_date_is_rejected() -> None:
    with pytest.raises(ValueError, match="not a relative publication date"):
        resolve_relative_date("5 min read", "2026-09-19T12:30:00Z")
