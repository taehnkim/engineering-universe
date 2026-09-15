"""CLI and module exports for curated crawl seeding."""

from __future__ import annotations

import argparse
import asyncio
import sys

from eng_universe.config import Settings
from eng_universe.ingest.crawler import normalize_url, seed_catalog, seed_queue
from eng_universe.ingest.sources import UrlKind, all_seed_urls, resolve_seed_url
from eng_universe.monitoring.logging_utils import get_event_logger


log_event = get_event_logger("seed")


async def seed_from_env() -> list[str]:
    """Seed URLs from SEED_START_URLS when set; else seed the full catalog."""
    raw = Settings.seed_start_urls.strip()
    if not raw:
        return await seed_catalog()
    seeded: list[str] = []
    for url in raw.split(","):
        url = url.strip()
        if not url:
            continue
        await seed_queue(url)
        seeded.append(url)
    return seeded


async def seed_urls(urls: list[str] | None = None, use_catalog: bool = False) -> list[str]:
    if use_catalog or not urls:
        if use_catalog:
            return await seed_catalog()
        return await seed_from_env()
    seeded: list[str] = []
    for url in urls:
        await seed_queue(url)
        seeded.append(url)
    return seeded


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Seed the crawl queue from curated listing roots or one known article URL."
        )
    )
    parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        default=[],
        help=(
            "Listing root or article URL already covered by the source catalog. "
            "Repeat for multiple URLs."
        ),
    )
    parser.add_argument(
        "--catalog",
        action="store_true",
        help="Seed every listing root in eng_universe.ingest.sources.SOURCES",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_catalog",
        help="Print curated seed URLs and exit",
    )
    parser.add_argument(
        "--classify",
        metavar="URL",
        help="Classify one URL (listing|article|sitemap|reject) and exit",
    )
    args = parser.parse_args(argv)

    if args.list_catalog:
        for url in all_seed_urls():
            print(url)
        return 0

    if args.classify:
        normalized = normalize_url(args.classify)
        if not normalized:
            print("invalid URL", file=sys.stderr)
            return 2
        kind, error = resolve_seed_url(normalized)
        print(f"{kind.value}\t{normalized}")
        if error:
            print(error, file=sys.stderr)
            return 1
        return 0

    try:
        seeded = asyncio.run(
            seed_urls(urls=args.urls or None, use_catalog=args.catalog)
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for url in seeded:
        log_event("cmd:seed", url=url)
        print(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
