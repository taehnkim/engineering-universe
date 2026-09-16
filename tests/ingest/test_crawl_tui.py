"""Tests Redis → stage-monitor view-model mapping."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr

import fakeredis.aioredis as fakeredis

from eng_universe.cli import _should_open_tui, build_parser
from eng_universe.ingest.queue_models import (
    CRAWL_STAGE,
    INDEX_RAW_STAGE,
    StageQueueKeys,
)
from eng_universe.ingest.tui.snapshot import (
    STATUS_DELAYED,
    STATUS_FAILED,
    STATUS_IN_FLIGHT,
    STATUS_QUEUED,
    build_snapshot,
    collect_snapshot,
    format_flow_boxes,
    row_fields_for_run,
)
from eng_universe.ingest.tui.stages import resolve_stage, stage_title


def _run_fields(
    *,
    run_id: str,
    state: str,
    url: str = "",
    doc_id: str = "",
    stage: str = CRAWL_STAGE,
    origin: str | None = None,
) -> dict[str, str]:
    if stage == INDEX_RAW_STAGE:
        payload = {"doc_id": doc_id or "doc-1"}
    else:
        payload = {"url": url, "source": "seed", "depth": 0}
    fields = {
        "run_id": run_id,
        "stage": stage,
        "state": state,
        "input_json": json.dumps(payload),
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
            queued=1,
            delayed=1,
            in_flight=1,
            failed=1,
            blocked=1,
            ready_sample=[
                ("r_ready", float(now_ms - 10)),
                ("r_delayed", float(now_ms + 5_000)),
            ],
            leased=["r_leased"],
            dead=["r_dead"],
            blocked_ids=["r_blocked"],
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
            },
        )

        self.assertEqual(snap.queued, 1)
        self.assertEqual(snap.delayed, 1)
        self.assertEqual(snap.in_flight, 1)
        self.assertEqual(snap.failed, 1)
        self.assertEqual(snap.blocked, 1)
        self.assertEqual(snap.ready_run_ids, ("r_ready", "r_delayed"))
        self.assertEqual(snap.inflight_run_ids, ("r_leased",))

        by_id = {row.run_id: row for row in snap.rows}
        self.assertEqual(by_id["r_ready"].status, STATUS_QUEUED)
        self.assertEqual(by_id["r_delayed"].status, STATUS_DELAYED)
        self.assertEqual(by_id["r_leased"].status, STATUS_IN_FLIGHT)
        self.assertEqual(by_id["r_leased"].subject, "netflixtechblog.com")
        self.assertEqual(by_id["r_dead"].status, STATUS_FAILED)
        self.assertNotIn("r_ok", by_id)

    def test_index_raw_rows_show_doc_id_and_clean_path(self) -> None:
        now_ms = 1_000_000
        snap = build_snapshot(
            stage=INDEX_RAW_STAGE,
            now_ms=now_ms,
            queued=1,
            delayed=0,
            in_flight=1,
            failed=0,
            blocked=0,
            ready_sample=[("r_q", float(now_ms))],
            leased=["r_l"],
            dead=[],
            blocked_ids=[],
            runs={
                "r_q": _run_fields(
                    run_id="r_q",
                    state="queued",
                    doc_id="42",
                    stage=INDEX_RAW_STAGE,
                ),
                "r_l": _run_fields(
                    run_id="r_l",
                    state="running",
                    doc_id="99",
                    stage=INDEX_RAW_STAGE,
                ),
            },
        )
        self.assertEqual(snap.queued, 1)
        self.assertEqual(snap.in_flight, 1)
        by_id = {row.run_id: row for row in snap.rows}
        self.assertEqual(by_id["r_l"].subject, "99")
        self.assertEqual(by_id["r_l"].detail, "clean/99.txt")

    def test_row_fields_for_clean_alias_stage(self) -> None:
        subject, detail = row_fields_for_run(
            stage=INDEX_RAW_STAGE,
            input_json=json.dumps({"doc_id": "abc"}),
        )
        self.assertEqual(subject, "abc")
        self.assertEqual(detail, "clean/abc.txt")

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

    def test_should_open_tui_respects_no_tui(self) -> None:
        self.assertFalse(_should_open_tui(no_tui=True))

    def test_stage_aliases_map_clean_and_index_to_index_raw(self) -> None:
        self.assertEqual(resolve_stage("clean"), INDEX_RAW_STAGE)
        self.assertEqual(resolve_stage("index"), INDEX_RAW_STAGE)
        self.assertEqual(resolve_stage("crawl"), CRAWL_STAGE)
        self.assertEqual(stage_title(INDEX_RAW_STAGE), "Index · Clean Monitor")
        with self.assertRaises(ValueError):
            resolve_stage("fetch_raw")
        with self.assertRaises(ValueError):
            resolve_stage("unknown-stage")


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

        await self.redis.zadd(
            ready_key, {"r_q1": now_ms - 1, "r_d1": now_ms + 10_000}
        )
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
        self.assertEqual(snap.rows[0].run_id, "r_l1")
        self.assertEqual(snap.rows[0].status, STATUS_IN_FLIGHT)
        self.assertEqual(snap.rows[0].subject, "b.example")
        # Succeeded runs are not scanned from the keyspace.
        self.assertTrue(all(row.run_id != "r_s1" for row in snap.rows))

    async def test_collect_index_raw_snapshot(self) -> None:
        now_ms = 3_000_000
        await self.redis.zadd(
            self.keys.ready(INDEX_RAW_STAGE), {"r_i1": now_ms}
        )
        await self.redis.zadd(
            self.keys.leased(INDEX_RAW_STAGE), {"r_i2": now_ms + 1}
        )
        await self.redis.hset(
            self.keys.run("r_i1"),
            mapping=_run_fields(
                run_id="r_i1",
                state="queued",
                doc_id="11",
                stage=INDEX_RAW_STAGE,
            ),
        )
        await self.redis.hset(
            self.keys.run("r_i2"),
            mapping=_run_fields(
                run_id="r_i2",
                state="leased",
                doc_id="22",
                stage=INDEX_RAW_STAGE,
            ),
        )
        snap = await collect_snapshot(
            self.redis,
            keys=self.keys,
            stage=INDEX_RAW_STAGE,
            now_ms=now_ms,
        )
        self.assertEqual(snap.queued, 1)
        self.assertEqual(snap.in_flight, 1)
        self.assertEqual(snap.rows[0].subject, "22")
        self.assertEqual(snap.rows[0].detail, "clean/22.txt")

    async def test_leased_rows_keep_detail_when_ready_sample_is_large(self) -> None:
        """Ready IDs must not crowd leased hashes out of the hydrate budget."""

        now_ms = 4_000_000
        ready_key = self.keys.ready(CRAWL_STAGE)
        leased_key = self.keys.leased(CRAWL_STAGE)
        ready_mapping = {
            f"r_ready_{index:02d}": float(now_ms - index) for index in range(40)
        }
        ready_mapping.update(
            {
                f"r_delay_{index:02d}": float(now_ms + 10_000 + index)
                for index in range(40)
            }
        )
        await self.redis.zadd(ready_key, ready_mapping)
        await self.redis.zadd(leased_key, {"r_leased": now_ms + 30_000})

        for run_id in ready_mapping:
            await self.redis.hset(
                self.keys.run(run_id),
                mapping=_run_fields(
                    run_id=run_id,
                    state="queued",
                    url=f"https://ready.example/{run_id}",
                ),
            )
        await self.redis.hset(
            self.keys.run("r_leased"),
            mapping=_run_fields(
                run_id="r_leased",
                state="leased",
                url="https://inflight.example/item",
            ),
        )

        snap = await collect_snapshot(
            self.redis,
            keys=self.keys,
            stage=CRAWL_STAGE,
            now_ms=now_ms,
            row_limit=40,
        )

        self.assertEqual(snap.rows[0].run_id, "r_leased")
        self.assertEqual(snap.rows[0].subject, "inflight.example")
        self.assertEqual(snap.rows[0].detail, "https://inflight.example/item")


class CliParserTuiTests(unittest.TestCase):
    """Ensures crawl/index expose --no-tui and monitor stage choices."""

    def test_crawl_and_index_parsers_have_no_tui_flag(self) -> None:
        parser = build_parser()
        crawl = parser.parse_args(["crawl", "--no-tui", "--max-docs", "3"])
        self.assertTrue(crawl.no_tui)
        self.assertEqual(crawl.max_docs, 3)

        index = parser.parse_args(["index", "--no-tui"])
        self.assertTrue(index.no_tui)

        reindex = parser.parse_args(["reindex", "--no-tui"])
        self.assertTrue(reindex.no_tui)

        monitor = parser.parse_args(["monitor", "--stage", "clean"])
        self.assertEqual(monitor.command, "monitor")
        self.assertEqual(monitor.stage, "clean")

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["monitor", "--stage", "fetch_raw"])


if __name__ == "__main__":
    unittest.main()
