"""Textual stage-queue monitor (htop-style bordered panes)."""

from __future__ import annotations

from typing import Any, ClassVar

import redis.asyncio as redis
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Static

from eng_universe.config import Settings
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
from eng_universe.ingest.tui.stages import stage_table_headers, stage_title

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
    StatBox.accent-ok { border: solid #34d399; color: #6ee7b7; }
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

    snapshot: reactive[CrawlMonitorSnapshot | None] = reactive(None)

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        namespace: str | None = None,
        stage: str = CRAWL_STAGE,
        refresh_hz: float = REFRESH_HZ,
        redis_client: redis.Redis | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._redis_url = redis_url or Settings.redis_url
        self._namespace = namespace or Settings.stage_queue_namespace
        self._stage = stage
        self._refresh_hz = refresh_hz
        self._redis = redis_client
        self._owns_redis = redis_client is None
        self._keys = StageQueueKeys(namespace=self._namespace)
        self.title = f"Eng Universe · {stage_title(stage)}"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="stats-row"):
            yield StatBox("QUEUED", accent="accent-queued", id="stat-queued")
            yield StatBox("IN-FLIGHT", accent="accent-flight", id="stat-flight")
            yield StatBox("SUCCEEDED", accent="accent-ok", id="stat-ok")
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
        if self._redis is None:
            self._redis = redis.from_url(self._redis_url)
        self.set_interval(1.0 / self._refresh_hz, self._tick)
        await self._tick()

    async def on_unmount(self) -> None:
        if self._owns_redis and self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def action_refresh(self) -> None:
        await self._tick()

    async def _tick(self) -> None:
        if self._redis is None:
            return
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
        self.snapshot = snap
        self._render_snapshot(snap)

    def _render_snapshot(self, snap: CrawlMonitorSnapshot) -> None:
        self.query_one("#stat-queued", StatBox).set_value(snap.queued)
        self.query_one("#stat-flight", StatBox).set_value(snap.in_flight)
        self.query_one("#stat-ok", StatBox).set_value(snap.succeeded)
        self.query_one("#stat-fail", StatBox).set_value(snap.failed)
        self.query_one("#stat-block", StatBox).set_value(snap.blocked)
        self.query_one("#stat-delay", StatBox).set_value(snap.delayed)

        flow = self.query_one("#flow-pane", FlowPane)
        subtitle = ""
        if snap.stage == INDEX_RAW_STAGE:
            subtitle = "  [dim](clean + index via index_raw)[/dim]"
        flow.update(
            f"[b]READY → IN-FLIGHT[/b]{subtitle}\n"
            + format_flow_boxes(snap.ready_run_ids, snap.inflight_run_ids)
        )

        table = self.query_one("#runs-table", DataTable)
        table.clear()
        for row in snap.rows:
            table.add_row(row.run_id, row.subject, row.status, row.detail)
