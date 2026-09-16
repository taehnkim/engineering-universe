"""Runs the stage monitor alone or beside crawl/index workers."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import Awaitable, Callable

from eng_universe.config import Settings
from eng_universe.index.pipeline import index_worker
from eng_universe.ingest.crawler import run_crawlers
from eng_universe.ingest.queue_models import CRAWL_STAGE, INDEX_RAW_STAGE
from eng_universe.ingest.tui.app import CrawlMonitorApp
from eng_universe.ingest.tui.stages import resolve_stage


def should_open_tui(*, no_tui: bool, stdout_isatty: bool | None = None) -> bool:
    """Returns True when a worker command should open the monitor TUI."""

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
    """Opens the stage monitor against Redis without starting workers."""

    resolved = resolve_stage(stage)
    app = CrawlMonitorApp(
        redis_url=redis_url or Settings.redis_url,
        namespace=namespace or Settings.stage_queue_namespace,
        stage=resolved,
    )
    await app.run_async()


async def run_with_tui(
    worker: Callable[[], Awaitable[None]],
    *,
    stage: str,
    redis_url: str | None = None,
    namespace: str | None = None,
    worker_name: str = "stage-worker",
) -> None:
    """
    Starts one worker coroutine and opens the live stage monitor TUI.

    Quitting the TUI cancels the worker. Worker completion leaves the TUI open
    so the final queue state remains visible until the operator exits.
    """

    resolved = resolve_stage(stage)
    worker_task = asyncio.create_task(worker(), name=worker_name)
    app = CrawlMonitorApp(
        redis_url=redis_url or Settings.redis_url,
        namespace=namespace or Settings.stage_queue_namespace,
        stage=resolved,
    )
    try:
        await app.run_async()
    finally:
        if not worker_task.done():
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task
        elif worker_task.exception() is not None:
            # Surface worker failures after the UI closes.
            await worker_task


async def run_crawl_with_tui(
    *,
    max_docs: int | None = None,
    doc_key_prefix: str | None = None,
    redis_url: str | None = None,
    namespace: str | None = None,
) -> None:
    """Starts crawler workers and opens the crawl monitor TUI."""

    async def _worker() -> None:
        await run_crawlers(doc_key_prefix=doc_key_prefix, max_docs=max_docs)

    await run_with_tui(
        _worker,
        stage=CRAWL_STAGE,
        redis_url=redis_url,
        namespace=namespace,
        worker_name="crawl-workers",
    )


async def run_index_with_tui(
    *,
    doc_key_prefix: str | None = None,
    redis_url: str | None = None,
    namespace: str | None = None,
) -> None:
    """
    Starts the index worker (clean + index) and opens the index_raw monitor.

    Cleaning is not a separate Redis stage; it runs inside index_worker while
    claiming index_raw leases.
    """

    async def _worker() -> None:
        await index_worker(doc_key_prefix=doc_key_prefix)

    await run_with_tui(
        _worker,
        stage=INDEX_RAW_STAGE,
        redis_url=redis_url,
        namespace=namespace,
        worker_name="index-workers",
    )
