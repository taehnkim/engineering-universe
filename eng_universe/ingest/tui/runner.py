"""Runs the crawl monitor alone or beside crawler workers."""

from __future__ import annotations

import asyncio
import contextlib
import sys

from eng_universe.config import Settings
from eng_universe.ingest.crawler import run_crawlers
from eng_universe.ingest.queue_models import CRAWL_STAGE
from eng_universe.ingest.tui.app import CrawlMonitorApp


def should_open_tui(*, no_tui: bool, stdout_isatty: bool | None = None) -> bool:
    """Returns True when the crawl command should open the monitor TUI."""

    if no_tui:
        return False
    is_tty = sys.stdout.isatty() if stdout_isatty is None else stdout_isatty
    return is_tty


async def run_monitor(
    *,
    redis_url: str | None = None,
    namespace: str | None = None,
    stage: str = CRAWL_STAGE,
) -> None:
    """Opens the crawl monitor against Redis without starting workers."""

    app = CrawlMonitorApp(
        redis_url=redis_url or Settings.redis_url,
        namespace=namespace or Settings.stage_queue_namespace,
        stage=stage,
    )
    await app.run_async()


async def run_crawl_with_tui(
    *,
    max_docs: int | None = None,
    doc_key_prefix: str | None = None,
    redis_url: str | None = None,
    namespace: str | None = None,
) -> None:
    """
    Starts crawler workers and opens the live crawl monitor TUI.

    Quitting the TUI cancels the crawl task. Crawl completion leaves the TUI
    open so the final queue state remains visible until the operator exits.
    """

    crawl_task = asyncio.create_task(
        run_crawlers(doc_key_prefix=doc_key_prefix, max_docs=max_docs),
        name="crawl-workers",
    )
    app = CrawlMonitorApp(
        redis_url=redis_url or Settings.redis_url,
        namespace=namespace or Settings.stage_queue_namespace,
        stage=CRAWL_STAGE,
    )
    try:
        await app.run_async()
    finally:
        if not crawl_task.done():
            crawl_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await crawl_task
        elif crawl_task.exception() is not None:
            # Surface worker failures after the UI closes.
            await crawl_task
