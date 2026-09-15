from __future__ import annotations

import asyncio
import threading
import time
import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from unittest.mock import patch

import fakeredis.aioredis as fakeredis

from eng_universe.config import Settings
from eng_universe.ingest.contracts import JsonValue, StageIdentity, StageRequest
from eng_universe.ingest.fetch_boundary import FetchPathDecision
from eng_universe.ingest.fetch_worker import (
    ProcessLease,
    FetchWorkerAlreadyRunning,
    FetchWorkerLeaseLost,
    R2UploadLimiter,
    _validate_fetch_settings,
    fetch_process_lock_key,
    run_fetch_worker,
)
from eng_universe.ingest.queue_models import (
    FETCH_RAW_STAGE,
    Origin,
    StageLease,
    StageQueueKeys,
)
from eng_universe.ingest.stage_queue import StageQueue


@dataclass(frozen=True)
class RuntimeFetchInput:
    """Provides a URL for fetch runtime tests."""

    url: str

    def idempotency_payload(self) -> Mapping[str, JsonValue]:
        return {"url": self.url}


def runtime_request(url: str) -> StageRequest[RuntimeFetchInput]:
    return StageRequest(
        identity=StageIdentity(name=FETCH_RAW_STAGE, version="1.0.0"),
        stage_input=RuntimeFetchInput(url),
        config_version="sources-1",
    )


class AllowingChecker:
    """Allows each URL used by a test."""

    async def check(self, url: str) -> FetchPathDecision:
        return FetchPathDecision(
            url=url,
            origin=Origin.from_url(url),
            allowed=True,
            request_interval_ms=0,
            robots_fetched_at=1,
        )


class RecordingHandler:
    """Records leased runs handled by the fetch worker."""

    def __init__(self, stop_event: asyncio.Event, expected: int) -> None:
        self.stop_event = stop_event
        self.expected = expected
        self.handled: set[str] = set()
        self.started = asyncio.Event()

    async def __call__(self, lease: StageLease) -> JsonValue:
        self.handled.add(lease.run.run_id)
        self.started.set()
        if len(self.handled) >= self.expected:
            self.stop_event.set()
        return {"url": lease.run.input_payload.get("url")}


class FetchWorkerTests(unittest.IsolatedAsyncioTestCase):
    """Tests fetch worker concurrency boundaries."""

    async def asyncSetUp(self) -> None:
        self.redis = fakeredis.FakeRedis()
        self.queue = StageQueue(
            self.redis,  # type: ignore[arg-type]
            keys=StageQueueKeys(namespace="eu:fetch-runtime-test:v1"),
            retry_base_ms=0,
            retry_max_ms=0,
        )

    async def asyncTearDown(self) -> None:
        await self.redis.aclose()

    def test_settings_validation_rejects_invalid_limits(self) -> None:
        _validate_fetch_settings()
        with patch.object(Settings, "fetch_worker_concurrency", 0):
            with self.assertRaisesRegex(ValueError, "fetch concurrency"):
                _validate_fetch_settings()
        with patch.object(Settings, "stage_lease_ms", 1):
            with patch.object(Settings, "request_timeout_s", 20):
                with self.assertRaisesRegex(ValueError, "longer than the HTTP"):
                    _validate_fetch_settings()

    async def test_redis_process_lease_prevents_limit_multiplication(self) -> None:
        key = fetch_process_lock_key(self.queue)
        first = ProcessLease(self.redis, key, lease_ms=1_000)
        second = ProcessLease(self.redis, key, lease_ms=1_000)

        await first.acquire()
        with self.assertRaises(FetchWorkerAlreadyRunning):
            await second.acquire()
        await self.redis.set(key, "replacement-token", px=1_000)
        self.assertFalse(await first.release())
        self.assertEqual(await self.redis.get(key), b"replacement-token")

    async def test_run_fetch_worker_uses_injected_handler(self) -> None:
        total = 6
        await asyncio.gather(
            *(
                self.queue.enqueue(
                    runtime_request(f"https://example-{index}.com/articles/1"),
                )
                for index in range(total)
            )
        )
        stop_event = asyncio.Event()
        handler = RecordingHandler(stop_event, total)
        with patch.object(Settings, "fetch_worker_concurrency", total):
            with patch.object(Settings, "fetch_process_lease_ms", 1_000):
                with patch.object(Settings, "fetch_process_heartbeat_ms", 200):
                    await asyncio.wait_for(
                        run_fetch_worker(
                            self.queue,
                            stop_event,
                            handler=handler,
                            checker=AllowingChecker(),  # type: ignore[arg-type]
                        ),
                        timeout=2,
                    )

        self.assertEqual(len(handler.handled), total)

    async def test_r2_uploads_use_a_separate_bounded_semaphore(self) -> None:
        limiter = R2UploadLimiter(2)
        lock = threading.Lock()
        active = 0
        maximum = 0

        def upload(value: int) -> int:
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            time.sleep(0.01)
            with lock:
                active -= 1
            return value

        results = await asyncio.gather(
            *(limiter.run(upload, index) for index in range(8))
        )

        self.assertEqual(results, list(range(8)))
        self.assertEqual(maximum, 2)

    async def test_run_fetch_worker_stops_if_process_lease_is_lost(self) -> None:
        await self.queue.enqueue(runtime_request("https://example.com/article"))
        stop_event = asyncio.Event()
        handler = RecordingHandler(stop_event, expected=2)
        with patch.object(Settings, "fetch_worker_concurrency", 1):
            with patch.object(Settings, "fetch_process_lease_ms", 100):
                with patch.object(Settings, "fetch_process_heartbeat_ms", 20):
                    running = asyncio.create_task(
                        run_fetch_worker(
                            self.queue,
                            stop_event,
                            handler=handler,
                            checker=AllowingChecker(),  # type: ignore[arg-type]
                        )
                    )
                    await asyncio.wait_for(handler.started.wait(), timeout=1)
                    await self.redis.set(
                        fetch_process_lock_key(self.queue),
                        "replacement-token",
                        px=1_000,
                    )

                    with self.assertRaises(FetchWorkerLeaseLost):
                        await asyncio.wait_for(running, timeout=1)


if __name__ == "__main__":
    unittest.main()
