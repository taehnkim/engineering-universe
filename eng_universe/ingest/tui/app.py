"""Textual stage-queue monitor and crawl/index TUI runners."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import redis.asyncio as redis
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Static

from eng_universe.config import Settings
from eng_universe.index.pipeline import index_worker
from eng_universe.ingest.crawler import run_crawlers
from eng_universe.ingest.queue_models import (
    CRAWL_STAGE,
    INDEX_RAW_STAGE,
    StageQueueKeys,
)
from eng_universe.ingest.tui.snapshot import (
    CrawlMonitorSnapshot,
    collect_snapshot,
    format_flow_boxes,
)
from eng_universe.ingest.tui.stages import (
    resolve_stage,
    stage_table_headers,
    stage_title,
)

REFRESH_HZ = 2.0


class StatBox(Static):
    """One bordered counter tile in the stats row."""

    DEFAULT_CSS = """
    StatBox {
        width: 1fr;
        height: 5;
        border: solid #4a5568;
        background: #0f1419;
        color: #e2e8f0;
        content-align: center middle;
        text-align: center;
        margin: 0 1;
        padding: 0 1;
    }
    StatBox.accent-queued { border: solid #38bdf8; color: #7dd3fc; }
    StatBox.accent-flight { border: solid #fbbf24; color: #fcd34d; }
    StatBox.accent-fail { border: solid #f87171; color: #fca5a5; }
    StatBox.accent-block { border: solid #c084fc; color: #d8b4fe; }
    StatBox.accent-delay { border: solid #94a3b8; color: #cbd5e1; }
    """

    def __init__(self, label: str, *, accent: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._label = label
        self.add_class(accent)

    def set_value(self, value: int) -> None:
        self.update(f"{self._label}\n[b]{value}[/b]")


class FlowPane(Static):
    """Middle pane: ready queue flowing into in-flight."""

    DEFAULT_CSS = """
    FlowPane {
        height: 9;
        border: solid #4a5568;
        background: #0b1220;
        color: #e2e8f0;
        padding: 1 2;
        margin: 1 1 0 1;
    }
    """


class CrawlMonitorApp(App[None]):
    """Live stage-queue monitor backed by Redis eu:v1 keys."""

    TITLE = "Eng Universe · Queue Monitor"
    CSS = """
    Screen {
        background: #020617;
    }
    #stats-row {
        height: 7;
        padding: 1 0 0 0;
    }
    #table-pane {
        border: solid #4a5568;
        background: #0b1220;
        margin: 1 1 0 1;
        height: 1fr;
    }
    DataTable {
        height: 1fr;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        namespace: str | None = None,
        stage: str = CRAWL_STAGE,
        refresh_hz: float = REFRESH_HZ,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._redis_url = redis_url or Settings.redis_url
        self._namespace = namespace or Settings.stage_queue_namespace
        self._stage = stage
        self._refresh_hz = refresh_hz
        self._redis: redis.Redis | None = None
        self._keys = StageQueueKeys(namespace=self._namespace)
        self._tick_lock = asyncio.Lock()
        self.title = f"Eng Universe · {stage_title(stage)}"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="stats-row"):
            yield StatBox("QUEUED", accent="accent-queued", id="stat-queued")
            yield StatBox("IN-FLIGHT", accent="accent-flight", id="stat-flight")
            yield StatBox("FAILED", accent="accent-fail", id="stat-fail")
            yield StatBox("BLOCKED", accent="accent-block", id="stat-block")
            yield StatBox("DELAYED", accent="accent-delay", id="stat-delay")
        yield FlowPane(id="flow-pane")
        with Vertical(id="table-pane"):
            yield DataTable(id="runs-table", zebra_stripes=True, cursor_type="row")
        yield Footer()

    async def on_mount(self) -> None:
        table = self.query_one("#runs-table", DataTable)
        table.add_columns(*stage_table_headers(self._stage))
        self._redis = redis.from_url(self._redis_url)
        self.set_interval(1.0 / self._refresh_hz, self._tick)
        await self._tick()

    async def on_unmount(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def action_refresh(self) -> None:
        await self._tick()

    def report_worker_failure(self, exc: BaseException) -> None:
        """Shows a worker exception in the flow pane, then exits the app."""

        with contextlib.suppress(Exception):
            self.query_one("#flow-pane", FlowPane).update(
                f"[red]Worker failed:[/red] {type(exc).__name__}: {exc}"
            )
        self.exit()

    async def _tick(self) -> None:
        if self._redis is None or self._tick_lock.locked():
            return
        async with self._tick_lock:
            try:
                snap = await collect_snapshot(
                    self._redis,
                    keys=self._keys,
                    stage=self._stage,
                )
            except Exception as exc:  # noqa: BLE001 - show connection errors in the UI
                flow = self.query_one("#flow-pane", FlowPane)
                flow.update(f"[red]Redis error:[/red] {exc}")
                return
            self._render_snapshot(snap)

    def _render_snapshot(self, snap: CrawlMonitorSnapshot) -> None:
        self.query_one("#stat-queued", StatBox).set_value(snap.queued)
        self.query_one("#stat-flight", StatBox).set_value(snap.in_flight)
        self.query_one("#stat-fail", StatBox).set_value(snap.failed)
        self.query_one("#stat-block", StatBox).set_value(snap.blocked)
        self.query_one("#stat-delay", StatBox).set_value(snap.delayed)

        flow = self.query_one("#flow-pane", FlowPane)
        subtitle = ""
        if self._stage == INDEX_RAW_STAGE:
            subtitle = "  [dim](clean + index via index_raw)[/dim]"
        flow.update(
            f"[b]READY → IN-FLIGHT[/b]{subtitle}\n"
            + format_flow_boxes(snap.ready_run_ids, snap.inflight_run_ids)
        )

        table = self.query_one("#runs-table", DataTable)
        table.clear()
        for row in snap.rows:
            table.add_row(row.run_id, row.subject, row.status, row.detail)


@contextlib.contextmanager
def _silence_worker_logging():
    """
    Detaches root logging handlers and disables crawl_log while the TUI runs.

    get_event_logger binds StreamHandlers to the real stderr before Textual
    starts; those handlers would otherwise paint over the dashboard.
    """

    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    for handler in saved_handlers:
        root.removeHandler(handler)
    previous_crawl_log = Settings.crawl_log
    Settings.crawl_log = False
    try:
        yield
    finally:
        Settings.crawl_log = previous_crawl_log
        for handler in saved_handlers:
            root.addHandler(handler)


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

    Quitting the TUI cancels the worker. Worker failure surfaces in the flow
    pane and exits the app. Worker completion leaves the TUI open.
    """

    resolved = resolve_stage(stage)
    app = CrawlMonitorApp(
        redis_url=redis_url or Settings.redis_url,
        namespace=namespace or Settings.stage_queue_namespace,
        stage=resolved,
    )
    worker_error: list[BaseException] = []

    def _on_worker_done(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is None:
            return
        worker_error.append(exc)
        app.call_later(app.report_worker_failure, exc)

    with _silence_worker_logging():
        worker_task = asyncio.create_task(worker(), name=worker_name)
        worker_task.add_done_callback(_on_worker_done)
        try:
            await app.run_async()
        finally:
            if not worker_task.done():
                worker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker_task
    if worker_error:
        raise worker_error[0]


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
    """Starts the index worker (clean + index) and opens the index_raw monitor."""

    async def _worker() -> None:
        await index_worker(doc_key_prefix=doc_key_prefix)

    await run_with_tui(
        _worker,
        stage=INDEX_RAW_STAGE,
        redis_url=redis_url,
        namespace=namespace,
        worker_name="index-workers",
    )


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
    with _silence_worker_logging():
        await app.run_async()
