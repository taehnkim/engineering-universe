"""Tests Redis → crawl-monitor view-model mapping."""

from __future__ import annotations

import json
import unittest

import fakeredis.aioredis as fakeredis

from eng_universe.ingest.queue_models import CRAWL_STAGE, StageQueueKeys
from eng_universe.ingest.tui.runner import should_open_tui
from eng_universe.ingest.tui.snapshot import (
    STATUS_DELAYED,
    STATUS_FAILED,
    STATUS_IN_FLIGHT,
    STATUS_QUEUED,
    STATUS_SUCCEEDED,
    build_snapshot,
    collect_snapshot,
    format_flow_boxes,
)


def _run_fields(
    *,
    run_id: str,
    state: str,
    url: str,
    stage: str = CRAWL_STAGE,
    origin: str | None = None,
) -> dict[str, str]:
    fields = {
        "run_id": run_id,
        "stage": stage,
        "state": state,
        "input_json": json.dumps({"url": url, "source": "seed", "depth": 0}),
    }
    if origin is not None:
        fields["origin"] = origin
    return fields


class BuildSnapshotTests(unittest.TestCase):
    """Covers pure mapping from decoded Redis membership to the TUI model."""

    def test_counts_ready_delayed_inflight_and_terminals(self) -> None:
        now_ms = 1_000_000
        snap = build_snapshot(
            stage=CRAWL_STAGE,
            now_ms=now_ms,
            ready=[
                ("r_ready", float(now_ms - 10)),
                ("r_delayed", float(now_ms + 5_000)),
            ],
            leased=["r_leased"],
            dead=["r_dead"],
            blocked=["r_blocked"],
            runs={
                "r_ready": _run_fields(
                    run_id="r_ready",
                    state="queued",
                    url="https://engineering.fb.com/a",
                ),
                "r_delayed": _run_fields(
                    run_id="r_delayed",
                    state="queued",
                    url="https://engineering.fb.com/b",
                ),
                "r_leased": _run_fields(
                    run_id="r_leased",
                    state="leased",
                    url="https://netflixtechblog.com/c",
                ),
                "r_dead": _run_fields(
                    run_id="r_dead",
                    state="failed",
                    url="https://example.com/x",
                ),
                "r_blocked": _run_fields(
                    run_id="r_blocked",
                    state="blocked",
                    url="https://example.com/y",
                ),
                "r_ok": _run_fields(
                    run_id="r_ok",
                    state="succeeded",
                    url="https://engineering.fb.com/done",
                ),
            },
        )

        self.assertEqual(snap.queued, 1)
        self.assertEqual(snap.delayed, 1)
        self.assertEqual(snap.in_flight, 1)
        self.assertEqual(snap.failed, 1)
        self.assertEqual(snap.blocked, 1)
        self.assertEqual(snap.succeeded, 1)
        self.assertEqual(snap.ready_run_ids, ("r_ready", "r_delayed"))
        self.assertEqual(snap.inflight_run_ids, ("r_leased",))

        by_id = {row.run_id: row for row in snap.rows}
        self.assertEqual(by_id["r_ready"].status, STATUS_QUEUED)
        self.assertEqual(by_id["r_delayed"].status, STATUS_DELAYED)
        self.assertEqual(by_id["r_leased"].status, STATUS_IN_FLIGHT)
        self.assertEqual(by_id["r_leased"].domain, "netflixtechblog.com")
        self.assertEqual(by_id["r_dead"].status, STATUS_FAILED)
        self.assertEqual(by_id["r_ok"].status, STATUS_SUCCEEDED)
        self.assertEqual(by_id["r_ok"].url, "https://engineering.fb.com/done")

    def test_flow_boxes_render_ready_then_inflight(self) -> None:
        text = format_flow_boxes(
            ["r_aaaaaaaaaa", "r_bbbbbbbbbb"],
            ["r_cccccccccc"],
            max_boxes=2,
            id_width=8,
        )
        self.assertIn("READY", text)
        self.assertIn("IN-FLIGHT", text)
        self.assertIn("──►", text)
        self.assertIn("r_ccccc", text)

    def test_should_open_tui_respects_no_tui_and_tty(self) -> None:
        self.assertFalse(should_open_tui(no_tui=True, stdout_isatty=True))
        self.assertFalse(should_open_tui(no_tui=False, stdout_isatty=False))
        self.assertTrue(should_open_tui(no_tui=False, stdout_isatty=True))


class CollectSnapshotTests(unittest.IsolatedAsyncioTestCase):
    """Reads FakeRedis eu:v1 keys the same way the live TUI does."""

    async def asyncSetUp(self) -> None:
        self.redis = fakeredis.FakeRedis()
        self.keys = StageQueueKeys(namespace="eu:test:tui")

    async def asyncTearDown(self) -> None:
        await self.redis.aclose()

    async def test_collect_snapshot_from_stage_queue_keys(self) -> None:
        now_ms = 2_000_000
        ready_key = self.keys.ready(CRAWL_STAGE)
        leased_key = self.keys.leased(CRAWL_STAGE)
        dead_key = self.keys.dead(CRAWL_STAGE)

        await self.redis.zadd(ready_key, {"r_q1": now_ms - 1, "r_d1": now_ms + 10_000})
        await self.redis.zadd(leased_key, {"r_l1": now_ms + 30_000})
        await self.redis.zadd(dead_key, {"r_f1": now_ms})

        for run_id, state, url in (
            ("r_q1", "queued", "https://a.example/1"),
            ("r_d1", "queued", "https://a.example/2"),
            ("r_l1", "running", "https://b.example/3"),
            ("r_f1", "failed", "https://c.example/4"),
            ("r_s1", "succeeded", "https://d.example/5"),
        ):
            await self.redis.hset(
                self.keys.run(run_id),
                mapping=_run_fields(run_id=run_id, state=state, url=url),
            )

        snap = await collect_snapshot(
            self.redis,
            keys=self.keys,
            stage=CRAWL_STAGE,
            now_ms=now_ms,
        )

        self.assertEqual(snap.queued, 1)
        self.assertEqual(snap.delayed, 1)
        self.assertEqual(snap.in_flight, 1)
        self.assertEqual(snap.failed, 1)
        self.assertEqual(snap.succeeded, 1)
        self.assertEqual(snap.rows[0].run_id, "r_l1")
        self.assertEqual(snap.rows[0].status, STATUS_IN_FLIGHT)
        self.assertEqual(snap.rows[0].domain, "b.example")


class CliParserTuiTests(unittest.TestCase):
    """Ensures crawl exposes --no-tui and crawl-monitor."""

    def test_crawl_parser_has_no_tui_flag(self) -> None:
        from eng_universe.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["crawl", "--no-tui", "--max-docs", "3"])
        self.assertTrue(args.no_tui)
        self.assertEqual(args.max_docs, 3)

        monitor = parser.parse_args(["crawl-monitor", "--stage", "crawl"])
        self.assertEqual(monitor.command, "crawl-monitor")
        self.assertEqual(monitor.stage, "crawl")


if __name__ == "__main__":
    unittest.main()
