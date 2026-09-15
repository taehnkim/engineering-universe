"""Curated engineering-blog source catalog and URL classification.

Seeding is human-curated. The system does not invent company roots.
Each SourceConfig names:

- seed_urls: listing roots you hand the crawler (example: https://stripe.dev/blog)
- listing_paths: paths that are indexes / hubs (follow links, do not store)
- follow_path_patterns: optional extra hubs such as pagination
- article_path_patterns: paths that are posts (store as documents)

A single article URL is valid only when it matches an article pattern for
a known host. Unknown hosts and marketing pages are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from urllib.parse import urlparse


class UrlKind(str, Enum):
    """How the crawler must treat a normalized URL."""

    LISTING = "listing"
    ARTICLE = "article"
    SITEMAP = "sitemap"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """One company engineering blog the crawler may touch."""

    id: str
    company: str
    host: str
    seed_urls: tuple[str, ...]
    listing_paths: tuple[str, ...]
    article_path_patterns: tuple[str, ...]
    follow_path_patterns: tuple[str, ...] = ()
    sitemap_paths: tuple[str, ...] = ("/sitemap.xml", "/sitemap_index.xml")

    def compiled_article_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(pattern) for pattern in self.article_path_patterns)

    def compiled_follow_patterns(self) -> tuple[re.Pattern[str], ...]:
        return tuple(re.compile(pattern) for pattern in self.follow_path_patterns)


# Default catalog. Add a company by editing this list, not by guessing from one post.
SOURCES: tuple[SourceConfig, ...] = (
    SourceConfig(
        id="meta-engineering",
        company="Meta",
        host="engineering.fb.com",
        seed_urls=("https://engineering.fb.com/",),
        listing_paths=("/",),
        article_path_patterns=(r"^/\d{4}/\d{2}/\d{2}/[^/]+/[^/]+$",),
    ),
    SourceConfig(
        id="ramp-builders",
        company="Ramp",
        host="builders.ramp.com",
        seed_urls=("https://builders.ramp.com/",),
        listing_paths=("/",),
        article_path_patterns=(r"^/post/[^/]+$",),
    ),
    SourceConfig(
        id="airbnb-tech",
        company="Airbnb",
        host="airbnb.tech",
        seed_urls=("https://airbnb.tech/",),
        listing_paths=("/",),
        article_path_patterns=(r"^/[^/]+/[^/]+$",),
    ),
    SourceConfig(
        id="airbnb-engineering-medium",
        company="Airbnb",
        host="medium.com",
        seed_urls=("https://medium.com/airbnb-engineering",),
        listing_paths=("/airbnb-engineering",),
        # Medium publication posts: /airbnb-engineering/<slug>
        # Reject /about, /followers, tagged pages, and other non-post paths.
        article_path_patterns=(r"^/airbnb-engineering/[a-z0-9][a-z0-9-]{8,}$",),
        follow_path_patterns=(
            r"^/airbnb-engineering$",
            r"^/airbnb-engineering/archive(?:/.*)?$",
        ),
        sitemap_paths=(),
    ),
    SourceConfig(
        id="anthropic-engineering",
        company="Anthropic",
        host="www.anthropic.com",
        seed_urls=("https://www.anthropic.com/engineering",),
        listing_paths=("/engineering",),
        article_path_patterns=(r"^/engineering/[^/]+$",),
    ),
    SourceConfig(
        id="openai-developers-blog",
        company="OpenAI",
        host="developers.openai.com",
        seed_urls=("https://developers.openai.com/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="cloudflare-blog",
        company="Cloudflare",
        host="blog.cloudflare.com",
        seed_urls=("https://blog.cloudflare.com/",),
        listing_paths=("/",),
        article_path_patterns=(r"^/[^/]+$",),
    ),
    SourceConfig(
        id="google-developers-blog",
        company="Google",
        host="developers.googleblog.com",
        seed_urls=("https://developers.googleblog.com/",),
        listing_paths=("/",),
        article_path_patterns=(r"^/[^/]+$",),
    ),
    SourceConfig(
        id="notion-blog",
        company="Notion",
        host="www.notion.com",
        seed_urls=("https://www.notion.com/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="cursor-blog",
        company="Cursor",
        host="cursor.com",
        seed_urls=("https://cursor.com/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="shopify-engineering",
        company="Shopify",
        host="shopify.engineering",
        seed_urls=("https://shopify.engineering/",),
        listing_paths=("/",),
        article_path_patterns=(r"^/[^/]+$",),
    ),
    SourceConfig(
        id="netflix-techblog",
        company="Netflix",
        host="netflixtechblog.com",
        seed_urls=("https://netflixtechblog.com/",),
        listing_paths=("/",),
        article_path_patterns=(r"^/[^/]+-[0-9a-f]{8,}$",),
        sitemap_paths=("/sitemap/sitemap.xml", "/sitemap.xml"),
    ),
    SourceConfig(
        id="github-engineering",
        company="GitHub",
        host="github.blog",
        seed_urls=("https://github.blog/engineering",),
        listing_paths=("/engineering",),
        article_path_patterns=(r"^/engineering/[^/]+/[^/]+$",),
    ),
    SourceConfig(
        id="spotify-engineering",
        company="Spotify",
        host="engineering.atspotify.com",
        seed_urls=("https://engineering.atspotify.com/",),
        listing_paths=("/",),
        article_path_patterns=(r"^/\d{4}/\d{1,2}/[^/]+$",),
    ),
    SourceConfig(
        id="slack-engineering",
        company="Slack",
        host="slack.engineering",
        seed_urls=("https://slack.engineering/",),
        listing_paths=("/",),
        article_path_patterns=(r"^/[^/]+$",),
    ),
    SourceConfig(
        id="stripe-dev",
        company="Stripe",
        host="stripe.dev",
        seed_urls=("https://stripe.dev/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
        follow_path_patterns=(r"^/blog/page/\d+$",),
    ),
    SourceConfig(
        id="stripe-com-blog",
        company="Stripe",
        host="stripe.com",
        seed_urls=("https://stripe.com/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="uber-blog",
        company="Uber",
        host="www.uber.com",
        seed_urls=("https://www.uber.com/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
)


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
                "Add a SourceConfig with listing_paths and article_path_patterns, "
                "or pass a known listing root / matching article URL."
            ),
        )
    return kind, None
