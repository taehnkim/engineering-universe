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
        seed_urls=(
            "https://cursor.com/blog/topic/product",
            "https://cursor.com/blog/topic/research",
        ),
        listing_paths=("/blog", "/blog/topic/product", "/blog/topic/research"),
        article_path_patterns=(r"^/blog/[^/]+$",),
        sitemap_paths=(),
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
        article_path_patterns=(r"^/engineering/(?!page/\d+$)[^/]+/[^/]+$",),
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
    SourceConfig(
        id="thinking-machines-blog",
        company="Thinking Machines",
        host="thinkingmachines.ai",
        seed_urls=("https://thinkingmachines.ai/blog/",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="clickhouse-blog",
        company="ClickHouse",
        host="clickhouse.com",
        seed_urls=("https://clickhouse.com/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="fly-blog",
        company="Fly.io",
        host="fly.io",
        seed_urls=("https://fly.io/blog/",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="oxide-blog",
        company="Oxide Computer Company",
        host="oxide.computer",
        seed_urls=("https://oxide.computer/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="brain-engineering",
        company="Brain Co.",
        host="brain.co",
        seed_urls=("https://brain.co/news/engineering",),
        listing_paths=("/news/engineering",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="nvidia-developer-blog",
        company="NVIDIA",
        host="developer.nvidia.com",
        seed_urls=("https://developer.nvidia.com/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(
            r"^/blog/(?!category/|tag/|recent-posts$)[^/]+$",
        ),
        follow_path_patterns=(
            r"^/blog/recent-posts$",
            r"^/blog/category/[^/]+$",
        ),
    ),
    SourceConfig(
        id="typesafe-blog",
        company="Typesafe AI",
        host="typesafe.ai",
        seed_urls=("https://typesafe.ai/",),
        listing_paths=("/",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="vercel-engineering",
        company="Vercel",
        host="vercel.com",
        seed_urls=("https://vercel.com/blog/category/engineering",),
        listing_paths=("/blog/category/engineering",),
        article_path_patterns=(r"^/blog/(?!category/)[^/]+$",),
        sitemap_paths=(),
    ),
    SourceConfig(
        id="turbopuffer-blog",
        company="Turbopuffer",
        host="turbopuffer.com",
        seed_urls=("https://turbopuffer.com/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/[^/]+$",),
    ),
    SourceConfig(
        id="planetscale-blog",
        company="PlanetScale",
        host="planetscale.com",
        seed_urls=("https://planetscale.com/blog",),
        listing_paths=("/blog",),
        article_path_patterns=(r"^/blog/(?!category/|author/)[^/]+$",),
        follow_path_patterns=(r"^/blog/category/[^/]+$",),
    ),
    SourceConfig(
        id="perplexity-blog",
        company="Perplexity",
        host="www.perplexity.ai",
        seed_urls=(
            "https://www.perplexity.ai/hub/blog/category/developers",
            "https://www.perplexity.ai/hub/blog/category/research",
        ),
        listing_paths=(
            "/hub/blog/category/developers",
            "/hub/blog/category/research",
        ),
        article_path_patterns=(r"^/hub/blog/(?!category/)[^/]+$",),
        sitemap_paths=(),
    ),
)
