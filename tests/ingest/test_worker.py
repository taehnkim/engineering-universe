from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from unittest.mock import AsyncMock

import fakeredis.aioredis as fakeredis

from eng_universe.ingest.contracts import (
    JsonValue,
    StageIdentity,
    StageRequest,
    StageStatus,
)
from eng_universe.ingest.queue_models import FailureKind, StageLease, StageQueueKeys
from eng_universe.ingest.stage_queue import StageQueue
from eng_universe.ingest.worker import StageError, StageWorkerPool


@dataclass(frozen=True)
class WorkerInput:
    """Provides semantic input for worker tests."""

    identifier: str

    def idempotency_payload(self) -> Mapping[str, JsonValue]:
        return {"id": self.identifier}


def worker_request(identifier: str) -> StageRequest[WorkerInput]:
    return StageRequest(
        identity=StageIdentity(name="normalize_article", version="1.0.0"),
        stage_input=WorkerInput(identifier),
        config_version="sources-1",
    )


class RecordingHandler:
    """Records each stage run handled by a test."""

    def __init__(self) -> None:
        self.run_ids: set[str] = set()
        self._lock = asyncio.Lock()

    async def __call__(self, lease: StageLease) -> JsonValue:
        run_id = lease.run.run_id
        async with self._lock:
            if run_id in self.run_ids:
                raise AssertionError("run executed more than once")
            self.run_ids.add(run_id)
        await asyncio.sleep(0)
        return {"normalized": True}


class RetryOnceHandler:
    """Fails once before it returns a result."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, lease: StageLease) -> JsonValue:
        self.calls += 1
        if self.calls == 1:
            raise StageError(
                kind=FailureKind.RETRYABLE,
                error_code="temporary",
                message="temporary stage failure",
                retry_delay_ms=0,
            )
        return {"attempt": lease.run.attempt_count}


class SlowHandler:
    """Runs long enough to require a heartbeat."""

    async def __call__(self, lease: StageLease) -> JsonValue:
        await asyncio.sleep(0.08)
        return {"heartbeat": True}


class BlockingHandler:
    """Waits until the worker cancels the task."""

    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def __call__(self, lease: StageLease) -> JsonValue:
        self.started.set()
        await asyncio.Event().wait()
        return {"unreachable": True}


class StageWorkerPoolTests(unittest.IsolatedAsyncioTestCase):
    """Tests async stage worker behavior."""

    async def asyncSetUp(self) -> None:
        self.redis = fakeredis.FakeRedis()
        self.queue = StageQueue(
            self.redis,  # type: ignore[arg-type]
            keys=StageQueueKeys(namespace="eu:worker-test:v1"),
            retry_base_ms=0,
            retry_max_ms=0,
        )

    async def asyncTearDown(self) -> None:
        await self.redis.aclose()

    async def test_concurrent_worker_pool_processes_each_run_once(self) -> None:
        total = 50
        results = await asyncio.gather(
            *(self.queue.enqueue(worker_request(str(index))) for index in range(total))
        )
        handler = RecordingHandler()
        pool = StageWorkerPool(
            self.queue,
            {"normalize_article": handler},
            concurrency=10,
            lease_ms=1_000,
            heartbeat_interval_ms=200,
        )

        worked = await asyncio.gather(
            *(pool.run_one(worker_id=f"worker-{index}") for index in range(total))
        )

        self.assertTrue(all(worked))
        self.assertEqual(handler.run_ids, {result.run.run_id for result in results})
        states = await asyncio.gather(
            *(self.queue.get_run(result.run.run_id) for result in results)
        )
        self.assertTrue(
            all(
                run is not None and run.state == StageStatus.SUCCEEDED for run in states
            )
        )

    async def test_worker_retries_typed_failure(self) -> None:
        result = await self.queue.enqueue(worker_request("retry"), max_attempts=3)
        handler = RetryOnceHandler()
        pool = StageWorkerPool(
            self.queue,
            {"normalize_article": handler},
            concurrency=1,
            lease_ms=1_000,
            heartbeat_interval_ms=200,
        )

        self.assertTrue(await pool.run_one(worker_id="worker-1"))
        retried = await self.queue.get_run(result.run.run_id)
        self.assertIsNotNone(retried)
        assert retried is not None
        self.assertEqual(retried.state, StageStatus.QUEUED)

        self.assertTrue(await pool.run_one(worker_id="worker-1"))
        completed = await self.queue.get_run(result.run.run_id)
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.state, StageStatus.SUCCEEDED)
        self.assertEqual(completed.attempt_count, 2)

    async def test_worker_heartbeats_during_slow_execution(self) -> None:
        result = await self.queue.enqueue(worker_request("slow"))
        pool = StageWorkerPool(
            self.queue,
            {"normalize_article": SlowHandler()},
            concurrency=1,
            lease_ms=40,
            heartbeat_interval_ms=10,
        )

        self.assertTrue(await pool.run_one(worker_id="worker-1"))
        completed = await self.queue.get_run(result.run.run_id)
        self.assertIsNotNone(completed)
        assert completed is not None
        self.assertEqual(completed.state, StageStatus.SUCCEEDED)

    async def test_lost_heartbeat_cancels_only_the_current_execution(self) -> None:
        result = await self.queue.enqueue(worker_request("lost"), max_attempts=2)
        handler = BlockingHandler()
        pool = StageWorkerPool(
            self.queue,
            {"normalize_article": handler},
            concurrency=1,
            lease_ms=40,
            heartbeat_interval_ms=10,
        )
        work = asyncio.create_task(pool.run_one(worker_id="worker-1"))
        await asyncio.wait_for(handler.started.wait(), timeout=1)
        await self.redis.hset(
            self.queue.keys.run(result.run.run_id),
            "lease_token",
            "replacement-token",
        )

        self.assertTrue(await asyncio.wait_for(work, timeout=1))
        await asyncio.sleep(0.05)
        self.assertEqual(
            await self.queue.reclaim_expired(
                "normalize_article",
                retry_delay_ms=0,
            ),
            1,
        )

    async def test_heartbeat_error_cancels_current_execution(self) -> None:
        await self.queue.enqueue(worker_request("redis-error"), max_attempts=2)
        handler = BlockingHandler()
        pool = StageWorkerPool(
            self.queue,
            {"normalize_article": handler},
            concurrency=1,
            lease_ms=40,
            heartbeat_interval_ms=10,
        )
        self.queue.heartbeat = AsyncMock(side_effect=ConnectionError("redis down"))

        self.assertTrue(
            await asyncio.wait_for(
                pool.run_one(worker_id="worker-1"),
                timeout=1,
            )
        )
        await asyncio.sleep(0.05)
        self.assertEqual(
            await self.queue.reclaim_expired(
                "normalize_article",
                retry_delay_ms=0,
            ),
            1,
        )


if __name__ == "__main__":
    unittest.main()
