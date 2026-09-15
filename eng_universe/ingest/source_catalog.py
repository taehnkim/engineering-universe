"""Curated engineering-blog source catalog.

Edit this file to add a company. The crawler does not invent roots.

Each SourceConfig names:

- seed_urls: listing roots you hand the crawler (example: https://stripe.dev/blog)
- listing_paths: paths that are indexes / hubs (follow links, do not store)
- follow_path_patterns: optional extra hubs such as pagination
- article_path_patterns: paths that are posts (store as documents)
"""

from __future__ import annotations

from dataclasses import dataclass
import re


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


# Add a company by editing this list, not by guessing from one post.
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
