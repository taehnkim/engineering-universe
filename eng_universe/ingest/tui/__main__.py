"""CLI entry for the standalone crawl monitor TUI."""

from __future__ import annotations

import argparse
import asyncio

from eng_universe.config import Settings
from eng_universe.ingest.queue_models import CRAWL_STAGE
from eng_universe.ingest.tui.runner import run_monitor


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Crawl queue monitor TUI")
    parser.add_argument(
        "--redis-url",
        default=None,
        help="Redis URL (default: REDIS_URL / Settings.redis_url)",
    )
    parser.add_argument(
        "--namespace",
        default=None,
        help="Stage queue namespace (default: STAGE_QUEUE_NAMESPACE)",
    )
    parser.add_argument(
        "--stage",
        default=CRAWL_STAGE,
        help=f"Stage name to monitor (default: {CRAWL_STAGE})",
    )
    args = parser.parse_args(argv)
    asyncio.run(
        run_monitor(
            redis_url=args.redis_url or Settings.redis_url,
            namespace=args.namespace or Settings.stage_queue_namespace,
            stage=args.stage,
        )
    )


if __name__ == "__main__":
    main()
