from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from collections.abc import Sequence

import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.index.indexer import create_search_index
from eng_universe.index.pipeline import index_worker
from eng_universe.ingest.application import (
    enqueue_fetch,
    make_stage_queue,
    run_fetch_application,
)
from eng_universe.ingest.crawler import run_crawlers, seed_catalog, seed_queue
from eng_universe.ingest.sources import all_seed_urls
from eng_universe.monitoring.logging_utils import get_event_logger
from eng_universe.monitoring.metrics_server import run_metrics_server

log_event = get_event_logger("main")


def build_parser() -> argparse.ArgumentParser:
    """Builds the Engineering Universe command-line parser."""

    parser = argparse.ArgumentParser(description="Eng Universe CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    seed_parser = sub.add_parser(
        "seed",
        help="Seed the crawl queue from curated listing roots or explicit URLs",
    )
    seed_parser.add_argument(
        "--url",
        action="append",
        dest="urls",
        default=[],
        help="Listing root or known article URL (repeatable)",
    )
    seed_parser.add_argument(
        "--catalog",
        action="store_true",
        help="Seed every listing root in the source catalog",
    )
    seed_parser.add_argument(
        "--list",
        action="store_true",
        dest="list_catalog",
        help="Print curated seed URLs and exit",
    )
    crawl_parser = sub.add_parser("crawl", help="Run crawler workers")
    crawl_parser.add_argument(
        "--max-docs",
        type=int,
        default=None,
        help="Max docs to store before stopping (default: no limit)",
    )
    crawl_parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="Number of crawler workers to run (default: MAX_WORKERS)",
    )
    sub.add_parser("index", help="Run indexer workers")
    sub.add_parser("init-index", help="Initialize search index")
    sub.add_parser("reindex", help="Initialize search index and run indexer")
    sub.add_parser("metrics", help="Run Prometheus metrics server")

    ingest_parser = sub.add_parser("ingest", help="Use the Redis ingestion queue")
    ingest_sub = ingest_parser.add_subparsers(dest="ingest_command", required=True)
    enqueue_parser = ingest_sub.add_parser(
        "enqueue-fetch",
        help="Enqueue one URL for fetch_raw",
    )
    enqueue_parser.add_argument("url", help="Absolute HTTP or HTTPS URL")
    enqueue_parser.add_argument(
        "--config-version",
        required=True,
        help="Version of the source configuration",
    )
    enqueue_parser.add_argument(
        "--due-at-ms",
        type=int,
        default=0,
        help="Earliest Unix time in milliseconds for the first claim",
    )
    enqueue_parser.add_argument(
        "--max-attempts",
        type=int,
        default=5,
        help="Maximum number of claim attempts",
    )
    ingest_sub.add_parser(
        "run-fetch-worker",
        help="Run the existing fetch_raw worker until stopped",
    )
    return parser


async def _seed_urls(urls: Sequence[str]) -> list[str]:
    seeded: list[str] = []
    for url in urls:
        selected_url = url.strip()
        if not selected_url:
            continue
        await seed_queue(selected_url)
        seeded.append(selected_url)
    return seeded


async def _enqueue_fetch(args: argparse.Namespace) -> None:
    redis_client = redis.from_url(Settings.redis_url)
    try:
        result = await enqueue_fetch(
            make_stage_queue(redis_client),
            args.url,
            config_version=args.config_version,
            due_at_ms=args.due_at_ms,
            max_attempts=args.max_attempts,
        )
        print(
            json.dumps(
                {
                    "created": result.created,
                    "due_at_ms": result.run.due_at_ms,
                    "run_id": result.run.run_id,
                    "state": result.run.state.value,
                },
                sort_keys=True,
            )
        )
    finally:
        await redis_client.aclose()


async def _run_fetch_worker() -> None:
    redis_client = redis.from_url(Settings.redis_url)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for interrupt in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(interrupt, stop.set)
    try:
        await run_fetch_application(make_stage_queue(redis_client), stop)
    finally:
        for interrupt in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(interrupt)
        await redis_client.aclose()


async def _init_index() -> None:
    redis_client = redis.from_url(Settings.redis_url)
    try:
        await create_search_index(redis_client, "idx:blogs")
    finally:
        await redis_client.aclose()


async def _reindex() -> None:
    redis_client = redis.from_url(Settings.redis_url)
    try:
        await create_search_index(redis_client, "idx:blogs")
        await index_worker()
    finally:
        await redis_client.aclose()


def main(argv: Sequence[str] | None = None) -> None:
    """Runs one Engineering Universe command."""

    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "seed":
        if args.list_catalog:
            for url in all_seed_urls():
                print(url)
            return
        try:
            if args.catalog or (not args.urls and not Settings.seed_start_urls.strip()):
                seeded = asyncio.run(seed_catalog())
            elif args.urls:
                seeded = asyncio.run(_seed_urls(args.urls))
            else:
                seeded = asyncio.run(_seed_urls(Settings.seed_start_urls.split(",")))
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            raise SystemExit(1) from exc
        for url in seeded:
            log_event("cmd:seed", url=url)
        return
    if args.command == "crawl":
        if args.concurrency is not None:
            Settings.max_workers = max(1, args.concurrency)
        asyncio.run(run_crawlers(max_docs=args.max_docs))
        return
    if args.command == "index":
        asyncio.run(index_worker())
        return
    if args.command == "init-index":
        asyncio.run(_init_index())
        return
    if args.command == "reindex":
        asyncio.run(_reindex())
        return
    if args.command == "metrics":
        run_metrics_server()
        return
    if args.command == "ingest":
        if args.ingest_command == "enqueue-fetch":
            asyncio.run(_enqueue_fetch(args))
            return
        if args.ingest_command == "run-fetch-worker":
            asyncio.run(_run_fetch_worker())
            return
    parser.error("unknown command")
