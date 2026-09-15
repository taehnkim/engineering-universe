import asyncio
import os
import unittest
import uuid

import redis.asyncio as redis

from eng_universe.ingest.application import enqueue_fetch
from eng_universe.ingest.contracts import StageStatus
from eng_universe.ingest.queue_models import (
    FETCH_RAW_STAGE,
    FailureKind,
    StageQueueKeys,
)
from eng_universe.ingest.stage_queue import StageQueue

LIVE_REDIS_URL = os.getenv("LIVE_REDIS_URL")


@unittest.skipUnless(LIVE_REDIS_URL, "LIVE_REDIS_URL is not set")
class LiveRedisQueueSmokeTests(unittest.IsolatedAsyncioTestCase):
    """Checks queue Lua behavior against a live Redis server."""

    async def asyncSetUp(self) -> None:
        assert LIVE_REDIS_URL is not None
        self.redis = redis.from_url(LIVE_REDIS_URL)
        self.namespace = f"eu:smoke:{uuid.uuid4().hex}"
        self.queue = StageQueue(
            self.redis,
            keys=StageQueueKeys(namespace=self.namespace),
        )
        self.assertTrue(await self.redis.ping())

    async def asyncTearDown(self) -> None:
        keys = [key async for key in self.redis.scan_iter(match=f"{self.namespace}:*")]
        if keys:
            await self.redis.delete(*keys)
        await self.redis.aclose()

    async def test_queued_retry_is_claimable_only_when_due(self) -> None:
        await enqueue_fetch(
            self.queue,
            "https://example.com/article",
            config_version="smoke-1",
            max_attempts=2,
        )
        first = await self.queue.claim(FETCH_RAW_STAGE, worker_id="smoke-1")
        self.assertIsNotNone(first)
        assert first is not None

        failed = await self.queue.fail(
            first,
            kind=FailureKind.RETRYABLE,
            error_code="smoke_retry",
            error_message="retry evidence",
            retry_delay_ms=75,
        )
        self.assertEqual(failed.state, StageStatus.QUEUED)
        self.assertEqual(failed.attempt_count, 1)
        self.assertEqual(failed.error_code, "smoke_retry")
        self.assertEqual(failed.error_message, "retry evidence")
        self.assertIsNone(
            await self.queue.claim(FETCH_RAW_STAGE, worker_id="smoke-too-early")
        )

        await asyncio.sleep(0.1)
        second = await self.queue.claim(FETCH_RAW_STAGE, worker_id="smoke-2")
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.run.attempt_count, 2)
        completed = await self.queue.complete(second, output={"smoke": True})
        self.assertEqual(completed.state, StageStatus.SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
