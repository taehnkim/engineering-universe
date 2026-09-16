"""Ingest subpackage: data acquisition components."""

from eng_universe.ingest.crawler import (
    CrawlResult,
    clean_html,
    crawl_worker,
    extract_links,
    extract_text,
    is_allowed_url,
    normalize_url,
    run_crawlers,
    seed_catalog,
    seed_queue,
)
from eng_universe.ingest.etl import ParsedDocument, parse_html
from eng_universe.ingest.queue import (
    CrawlItem,
    delay,
    dequeue,
    enqueue,
)
from eng_universe.ingest.robots import (
    RobotsRules,
    get_or_fetch_robots,
    parse_domain,
    reserve_next_allowed,
)
from eng_universe.ingest.source_catalog import SOURCES, SourceConfig
from eng_universe.ingest.sources import (
    UrlKind,
    classify_url,
    is_article_url,
    is_listing_url,
    is_sitemap_url,
)

__all__ = [
    # crawler
    "CrawlResult",
    "clean_html",
    "crawl_worker",
    "extract_links",
    "extract_text",
    "is_allowed_url",
    "normalize_url",
    "run_crawlers",
    "seed_catalog",
    "seed_queue",
    # etl
    "ParsedDocument",
    "parse_html",
    # queue
    "CrawlItem",
    "delay",
    "dequeue",
    "enqueue",
    # robots
    "RobotsRules",
    "get_or_fetch_robots",
    "parse_domain",
    "reserve_next_allowed",
    # sources
    "SOURCES",
    "SourceConfig",
    "UrlKind",
    "classify_url",
    "is_article_url",
    "is_listing_url",
    "is_sitemap_url",
]
