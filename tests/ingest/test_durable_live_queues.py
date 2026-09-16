import asyncio
import unittest
import uuid
from unittest.mock import patch

import fakeredis.aioredis as fakeredis

from eng_universe.config import Settings
from eng_universe.ingest.queue import (
    CrawlItem,
    acknowledge,
    delay,
    dequeue,
    enqueue,
    enqueue_raw_index,
    stage_queue,
)
from eng_universe.ingest.queue_models import CRAWL_STAGE, INDEX_RAW_STAGE
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


if __name__ == "__main__":
    unittest.main()
