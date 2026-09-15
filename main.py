import argparse
import asyncio
import sys

from eng_universe.config import Settings
from eng_universe.ingest.crawler import run_crawlers, seed_catalog, seed_queue
from eng_universe.ingest.sources import all_seed_urls
from eng_universe.index.indexer import create_search_index
from eng_universe.monitoring.logging_utils import get_event_logger
from eng_universe.monitoring.metrics_server import run_metrics_server
from eng_universe.index.pipeline import index_worker


log_event = get_event_logger("main")


def main() -> None:
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

    args = parser.parse_args()

    if args.command == "seed":
        if args.list_catalog:
            for url in all_seed_urls():
                print(url)
            return
        try:
            if args.catalog or (not args.urls and not Settings.seed_start_urls.strip()):
                seeded = asyncio.run(seed_catalog())
            elif args.urls:
                async def _seed_urls() -> list[str]:
                    out: list[str] = []
                    for url in args.urls:
                        await seed_queue(url)
                        out.append(url)
                    return out

                seeded = asyncio.run(_seed_urls())
            else:
                async def _seed_env() -> list[str]:
                    out: list[str] = []
                    for url in Settings.seed_start_urls.split(","):
                        url = url.strip()
                        if not url:
                            continue
                        await seed_queue(url)
                        out.append(url)
                    return out

                seeded = asyncio.run(_seed_env())
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
        import redis.asyncio as redis

        redis_client = redis.from_url(Settings.redis_url)
        asyncio.run(create_search_index(redis_client, "idx:blogs"))
        return
    if args.command == "reindex":
        import redis.asyncio as redis

        async def _reindex() -> None:
            redis_client = redis.from_url(Settings.redis_url)
            await create_search_index(redis_client, "idx:blogs")
            await index_worker()

        asyncio.run(_reindex())
        return
    if args.command == "metrics":
        run_metrics_server()
        return


if __name__ == "__main__":
    main()
