import asyncio
import unittest
import uuid
from unittest.mock import AsyncMock, call, patch

import fakeredis.aioredis as fakeredis
import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.index.pipeline import index_worker
from eng_universe.ingest.crawler import crawl_worker
from eng_universe.ingest.queue import (
    CrawlItem,
    acknowledge,
    delay,
    dequeue,
    enqueue,
    enqueue_raw_index,
    reclaim_leases,
    stage_queue,
)
from eng_universe.ingest.queue import (
    fail as fail_lease,
)
from eng_universe.ingest.queue_models import (
    CRAWL_STAGE,
    INDEX_RAW_STAGE,
    FailureKind,
    StageLease,
)
from eng_universe.ingest.stage_queue import LeaseLostError


class DurableLiveQueueTests(unittest.IsolatedAsyncioTestCase):
    """Tests lease safety at the live crawler and index queue boundary."""

    async def asyncSetUp(self) -> None:
        self.redis = fakeredis.FakeRedis()
        namespace = f"eu:live-test:{uuid.uuid4().hex}"
        self.namespace_patch = patch.object(
            Settings,
            "stage_queue_namespace",
            namespace,
        )
        self.namespace_patch.start()

    async def asyncTearDown(self) -> None:
        self.namespace_patch.stop()
        await self.redis.aclose()

    async def test_crawl_claim_returns_after_crash_and_rejects_stale_ack(self) -> None:
        item = CrawlItem("https://example.com/article", "seed")
        await enqueue(self.redis, item)
        claimed = await dequeue(self.redis, worker_id="crashed")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        _, stale = claimed
        queue = stage_queue(self.redis)
        await self.redis.hset(
            queue.keys.run(stale.run.run_id),
            "lease_until_ms",
            0,
        )
        await self.redis.zadd(queue.keys.leased(CRAWL_STAGE), {stale.run.run_id: 0})

        self.assertEqual(await queue.reclaim_expired(CRAWL_STAGE), 1)
        recovered_claim = await dequeue(self.redis, worker_id="recovered")
        self.assertIsNotNone(recovered_claim)
        assert recovered_claim is not None
        recovered_item, recovered = recovered_claim
        self.assertEqual(recovered_item, item)
        with self.assertRaises(LeaseLostError):
            await acknowledge(self.redis, stale)
        await acknowledge(self.redis, recovered)

    async def test_delayed_crawl_becomes_ready_atomically_at_due_time(self) -> None:
        await enqueue(
            self.redis,
            CrawlItem("https://example.com/delayed", "seed"),
        )
        claimed = await dequeue(self.redis, worker_id="worker-1")
        self.assertIsNotNone(claimed)
        assert claimed is not None
        _, lease = claimed
        due_seconds = int((await stage_queue(self.redis).server_time_ms()) / 1000) + 1

        await delay(self.redis, lease, due_seconds)

        self.assertIsNone(await dequeue(self.redis, worker_id="too-early"))
        await asyncio.sleep(1.05)
        self.assertIsNotNone(await dequeue(self.redis, worker_id="ready"))

    async def test_raw_index_claim_is_idempotent_and_crash_reclaimable(self) -> None:
        await enqueue_raw_index(self.redis, "42")
        await enqueue_raw_index(self.redis, "42")
        queue = stage_queue(self.redis)

        first = await queue.claim(INDEX_RAW_STAGE, worker_id="crashed", lease_ms=10)
        self.assertIsNotNone(first)
        assert first is not None
        self.assertEqual(first.run.input_payload, {"doc_id": "42"})
        self.assertIsNone(
            await queue.claim(INDEX_RAW_STAGE, worker_id="duplicate-worker")
        )
        await asyncio.sleep(0.02)

        self.assertEqual(await queue.reclaim_expired(INDEX_RAW_STAGE), 1)
        recovered = await queue.claim(INDEX_RAW_STAGE, worker_id="recovered")
        self.assertIsNotNone(recovered)
        assert recovered is not None
        with self.assertRaises(LeaseLostError):
            await queue.complete(first)
        await queue.complete(recovered)

    async def test_crawler_transport_failure_is_retried_not_acknowledged(self) -> None:
        item = CrawlItem("https://example.com/failure", "seed")
        await enqueue(self.redis, item)
        queue = stage_queue(self.redis)
        run_id = (await self.redis.zrange(queue.keys.ready(CRAWL_STAGE), 0, 0))[
            0
        ].decode()
        stop_event = asyncio.Event()

        async def record_failure(
            redis_client: redis.Redis,
            lease: StageLease,
            *,
            kind: FailureKind,
            error_code: str,
            error_message: str,
        ) -> None:
            await fail_lease(
                redis_client,
                lease,
                kind=kind,
                error_code=error_code,
                error_message=error_message,
            )
            stop_event.set()

        with (
            patch(
                "eng_universe.ingest.crawler.check_robots_txt",
                new=AsyncMock(return_value="example.com"),
            ),
            patch(
                "eng_universe.ingest.crawler.fetch_html",
                new=AsyncMock(return_value=(None, TimeoutError("timed out"))),
            ),
            patch("eng_universe.ingest.crawler.fail", new=record_failure),
        ):
            await crawl_worker(
                self.redis,
                AsyncMock(),
                Settings.crawl_doc_key_prefix,
                stop_event=stop_event,
            )

        run = await queue.get_run(run_id)
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.state.value, "queued")
        self.assertEqual(run.error_class, FailureKind.RETRYABLE.value)
        self.assertEqual(run.error_code, "TimeoutError")

    async def test_indexer_missing_metadata_is_retried(self) -> None:
        await enqueue_raw_index(self.redis, "missing")
        queue = stage_queue(self.redis)
        run_id = (await self.redis.zrange(queue.keys.ready(INDEX_RAW_STAGE), 0, 0))[
            0
        ].decode()

        with (
            patch("eng_universe.index.pipeline.redis.from_url", return_value=self.redis),
            patch.object(Settings, "indexer_exit_on_idle", True),
            patch.object(Settings, "indexer_idle_grace_s", 0),
        ):
            await index_worker()

        run = await queue.get_run(run_id)
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.state.value, "queued")
        self.assertEqual(run.error_code, "missing_meta")

    async def test_one_reclaimer_loop_handles_each_live_stage(self) -> None:
        stop_event = asyncio.Event()
        queue = AsyncMock()

        async def reclaim(stage_name: str) -> int:
            if stage_name == INDEX_RAW_STAGE:
                stop_event.set()
            return 0

        queue.reclaim_expired.side_effect = reclaim
        with patch("eng_universe.ingest.queue.stage_queue", return_value=queue):
            await reclaim_leases(
                self.redis,
                (CRAWL_STAGE, INDEX_RAW_STAGE),
                stop_event,
            )

        self.assertEqual(
            queue.reclaim_expired.await_args_list,
            [
                call(CRAWL_STAGE),
                call(INDEX_RAW_STAGE),
            ],
        )

    async def test_clear_stage_removes_only_selected_run_lifecycle(self) -> None:
        await enqueue(
            self.redis,
            CrawlItem("https://example.com/clear", "seed"),
        )
        await enqueue_raw_index(self.redis, "keep")
        queue = stage_queue(self.redis)

        self.assertEqual(await queue.clear_stage(CRAWL_STAGE), 1)

        self.assertEqual((await queue.counts(CRAWL_STAGE)).ready, 0)
        self.assertEqual((await queue.counts(INDEX_RAW_STAGE)).ready, 1)


if __name__ == "__main__":
    unittest.main()
