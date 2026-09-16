from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from collections.abc import Sequence
from dataclasses import dataclass

import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.index.indexer import create_search_index
from eng_universe.index.pipeline import index_worker
from eng_universe.ingest.contracts import JsonValue, StageIdentity, StageRequest
from eng_universe.ingest.crawler import run_crawlers, seed_catalog, seed_queue
from eng_universe.ingest.fetch_worker import (
    FetchWorkerAlreadyRunning,
    run_fetch_worker,
)
from eng_universe.ingest.queue_models import (
    DEFAULT_MAX_ATTEMPTS,
    FETCH_RAW_STAGE,
    StageQueueKeys,
)
from eng_universe.ingest.sources import all_seed_urls
from eng_universe.ingest.stage_queue import StageQueue
from eng_universe.ingest.tui.runner import (
    run_crawl_with_tui,
    run_monitor,
    should_open_tui,
)
from eng_universe.monitoring.logging_utils import get_event_logger
from eng_universe.monitoring.metrics_server import run_metrics_server

log_event = get_event_logger("main")
FETCH_RAW_IDENTITY = StageIdentity(name=FETCH_RAW_STAGE, version="1.0.0")


@dataclass(frozen=True, slots=True)
class FetchRawInput:
    """Provides the URL that identifies one fetch request."""

    url: str

    def idempotency_payload(self) -> dict[str, JsonValue]:
        return {"url": self.url}


def _make_stage_queue(redis_client: redis.Redis) -> StageQueue:
    return StageQueue(
        redis_client,
        keys=StageQueueKeys(namespace=Settings.stage_queue_namespace),
        default_lease_ms=Settings.stage_lease_ms,
        fetch_global_limit=Settings.fetch_global_connection_limit,
    )


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
    crawl_parser.add_argument(
        "--no-tui",
        action="store_true",
        help="Skip the live crawl monitor TUI (default: open TUI on a TTY)",
    )
    monitor_parser = sub.add_parser(
        "crawl-monitor",
        help="Open the crawl queue monitor TUI without starting workers",
    )
    monitor_parser.add_argument(
        "--stage",
        default="crawl",
        help="Stage name to monitor (default: crawl)",
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
        default=DEFAULT_MAX_ATTEMPTS,
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
        request = StageRequest(
            identity=FETCH_RAW_IDENTITY,
            stage_input=FetchRawInput(url=args.url),
            config_version=args.config_version,
        )
        result = await _make_stage_queue(redis_client).enqueue(
            request,
            due_at_ms=args.due_at_ms,
            max_attempts=args.max_attempts,
            origin_max_inflight=Settings.fetch_origin_max_inflight,
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
        await run_fetch_worker(_make_stage_queue(redis_client), stop)
    except FetchWorkerAlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
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
        if should_open_tui(no_tui=args.no_tui):
            asyncio.run(run_crawl_with_tui(max_docs=args.max_docs))
        else:
            asyncio.run(run_crawlers(max_docs=args.max_docs))
        return
    if args.command == "crawl-monitor":
        asyncio.run(run_monitor(stage=args.stage))
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
