"""URL classification for curated engineering-blog sources.

Seeding is human-curated. Company roots live in ``source_catalog.py``.
This module classifies normalized URLs as listing, article, sitemap, or reject.
"""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse

from eng_universe.ingest.source_catalog import SOURCES, SourceConfig


class UrlKind(str, Enum):
    """How the crawler must treat a normalized URL."""

    LISTING = "listing"
    ARTICLE = "article"
    SITEMAP = "sitemap"
    REJECT = "reject"


def _normalize_path(path: str) -> str:
    if not path:
        return "/"
    if path != "/" and path.endswith("/"):
        return path.rstrip("/")
    return path


def sources_by_host() -> dict[str, tuple[SourceConfig, ...]]:
    grouped: dict[str, list[SourceConfig]] = {}
    for source in SOURCES:
        grouped.setdefault(source.host, []).append(source)
    return {host: tuple(items) for host, items in grouped.items()}


def all_seed_urls() -> tuple[str, ...]:
    urls: list[str] = []
    for source in SOURCES:
        urls.extend(source.seed_urls)
    return tuple(urls)


def find_sources_for_host(host: str) -> tuple[SourceConfig, ...]:
    return sources_by_host().get(host.lower(), ())


def sitemap_urls_for_host(host: str) -> set[str]:
    urls: set[str] = set()
    for source in find_sources_for_host(host):
        for path in source.sitemap_paths:
            urls.add(f"https://{source.host}{path}")
    return urls


def classify_url(url: str) -> UrlKind:
    """Classify a URL as listing, article, sitemap, or reject."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = _normalize_path(parsed.path or "/")
    sources = find_sources_for_host(host)
    if not sources:
        return UrlKind.REJECT

    if path.endswith(".xml") or "sitemap" in path:
        configured_paths: set[str] = set()
        for source in sources:
            configured_paths.update(source.sitemap_paths)
        if not configured_paths:
            return UrlKind.REJECT
        if path in configured_paths or path.endswith(".xml") or "sitemap" in path:
            return UrlKind.SITEMAP
        return UrlKind.REJECT

    for source in sources:
        if path in source.listing_paths:
            return UrlKind.LISTING
        for pattern in source.compiled_follow_patterns():
            if pattern.match(path):
                return UrlKind.LISTING
        for pattern in source.compiled_article_patterns():
            if pattern.match(path):
                return UrlKind.ARTICLE
    return UrlKind.REJECT


def is_allowed_url(url: str) -> bool:
    return classify_url(url) != UrlKind.REJECT


def is_listing_url(url: str) -> bool:
    return classify_url(url) == UrlKind.LISTING


def is_article_url(url: str) -> bool:
    return classify_url(url) == UrlKind.ARTICLE


def is_sitemap_url(url: str) -> bool:
    return classify_url(url) == UrlKind.SITEMAP


def resolve_seed_url(url: str) -> tuple[UrlKind, str | None]:
    """Validate a seed input.

    Returns (kind, error). error is None when the URL may be enqueued.
    Accepted kinds: LISTING (preferred root), ARTICLE (one-off post), SITEMAP.
    """
    kind = classify_url(url)
    if kind == UrlKind.REJECT:
        host = urlparse(url).netloc.lower() or "<missing-host>"
        return (
            kind,
            (
                f"URL is not in the curated source catalog for host {host}. "
                "Add a SourceConfig in eng_universe/ingest/source_catalog.py "
                "with listing_paths and article_path_patterns, "
                "or pass a known listing root / matching article URL."
            ),
        )
    return kind, None


# Re-export catalog types so existing imports of sources.SOURCES keep working.
__all__ = [
    "SOURCES",
    "SourceConfig",
    "UrlKind",
    "all_seed_urls",
    "classify_url",
    "find_sources_for_host",
    "is_allowed_url",
    "is_article_url",
    "is_listing_url",
    "is_sitemap_url",
    "resolve_seed_url",
    "sitemap_urls_for_host",
    "sources_by_host",
]
