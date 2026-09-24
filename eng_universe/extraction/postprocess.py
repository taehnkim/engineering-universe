"""Ordinary-code cleanup for extracted publication timestamps."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, datetime, timedelta

NUMBER_WORDS = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
RELATIVE_PUBLICATION_RE = re.compile(
    r"\b(?P<count>an?|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s+"
    r"(?P<unit>minute|hour|day|week|month|year)s?\s+ago\b",
    re.IGNORECASE,
)
READING_TIME_RE = re.compile(
    r"^\s*(?:an?|one|two|three|four|five|six|seven|eight|nine|ten|\d+)\s*"
    r"(?:min(?:ute)?s?|hours?)\s+(?:read|reading)\s*$",
    re.IGNORECASE,
)


def normalize_scraped_at(value: str | datetime) -> str:
    parsed = _parse_datetime(value)
    return parsed.isoformat().replace("+00:00", "Z")


def _parse_datetime(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.strip())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def extract_relative_publication_date(text: str | None) -> str | None:
    """Return the relative-date phrase, never a reading-time duration."""

    if not text or READING_TIME_RE.fullmatch(text):
        return None
    match = RELATIVE_PUBLICATION_RE.search(text)
    return match.group(0) if match else None


def is_reading_time(text: str | None) -> bool:
    return bool(text and READING_TIME_RE.fullmatch(text))


def _subtract_months(value: datetime, months: int) -> datetime:
    month_index = value.year * 12 + (value.month - 1) - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def resolve_relative_date(relative_date: str, scraped_at: str | datetime) -> str:
    phrase = extract_relative_publication_date(relative_date)
    if phrase is None:
        raise ValueError(f"not a relative publication date: {relative_date!r}")
    match = RELATIVE_PUBLICATION_RE.search(phrase)
    assert match is not None
    raw_count = match.group("count").lower()
    count = int(raw_count) if raw_count.isdigit() else NUMBER_WORDS[raw_count]
    unit = match.group("unit").lower()
    scraped = _parse_datetime(scraped_at)
    if unit == "minute":
        published = scraped - timedelta(minutes=count)
    elif unit == "hour":
        published = scraped - timedelta(hours=count)
    elif unit == "day":
        published = scraped - timedelta(days=count)
    elif unit == "week":
        published = scraped - timedelta(weeks=count)
    elif unit == "month":
        published = _subtract_months(scraped, count)
    else:
        published = _subtract_months(scraped, count * 12)
    return published.isoformat().replace("+00:00", "Z")


def derive_published_at(
    absolute_date: str | None,
    relative_date: str | None = None,
    scraped_at: str | datetime | None = None,
) -> str | None:
    """Resolve legacy ingest dates; four-field extraction passes only absolute_date."""

    if absolute_date and not extract_relative_publication_date(absolute_date):
        return absolute_date.strip() or None
    if scraped_at is not None:
        phrase = extract_relative_publication_date(relative_date or absolute_date)
        return resolve_relative_date(phrase, scraped_at) if phrase else None
    return None
