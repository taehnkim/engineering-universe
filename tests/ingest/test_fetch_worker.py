import asyncio
import threading
import time
import unittest
from collections.abc import Mapping
from dataclasses import dataclass

import aiohttp
import fakeredis.aioredis as fakeredis

from eng_universe.ingest.contracts import JsonValue, StageIdentity, StageRequest
from eng_universe.ingest.fetch_boundary import (
    AuthorizedFetchHandler,
    FetchPathChecker,
    FetchPathDecision,
)
from eng_universe.ingest.fetch_worker import (
    FetchProcessLease,
    FetchWorkerAlreadyRunning,
    FetchWorkerConfig,
    FetchWorkerLeaseLost,
    FetchWorkerRuntime,
    R2UploadLimiter,
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


class RuntimeHandlerFactory:
    """Records shared fetch runtime resources."""

    def __init__(self, stop_event: asyncio.Event, expected: int) -> None:
        self.stop_event = stop_event
        self.expected = expected
        self.session_ids: set[int] = set()
        self.connection_limits: set[int] = set()
        self.upload_limiters: set[int] = set()
        self.handled: set[str] = set()
        self.factory_calls = 0
        self.started = asyncio.Event()

    async def handle(
        self,
        lease: StageLease,
        decision: FetchPathDecision,
        checker: FetchPathChecker,
    ) -> JsonValue:
        self.handled.add(lease.run.run_id)
        self.started.set()
        if len(self.handled) == self.expected:
            self.stop_event.set()
        return {"url": decision.url}


class HandlerFactory:
    """Builds the recording fetch handler."""

    def __init__(self, runtime: RuntimeHandlerFactory) -> None:
        self.runtime = runtime

    def __call__(
        self,
        session: aiohttp.ClientSession,
        uploads: R2UploadLimiter,
    ) -> AuthorizedFetchHandler:
        self.runtime.factory_calls += 1
        self.runtime.session_ids.add(id(session))
        assert session.connector is not None
        self.runtime.connection_limits.add(session.connector.limit)
        self.runtime.upload_limiters.add(id(uploads))
        return self.runtime.handle


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

    def test_v1_defaults_and_process_boundary(self) -> None:
        config = FetchWorkerConfig()

        self.assertEqual(config.process_count, 1)
        self.assertEqual(config.concurrency, 100)
        self.assertEqual(config.global_connection_limit, 100)
        self.assertEqual(config.origin_max_inflight, 1)
        with self.assertRaisesRegex(ValueError, "process_count must remain 1"):
            FetchWorkerConfig(process_count=2)

    async def test_redis_process_lease_prevents_limit_multiplication(self) -> None:
        key = "eu:fetch-runtime-test:v1:lock:fetch-worker"
        first = FetchProcessLease(self.redis, key, lease_ms=1_000)  # type: ignore[arg-type]
        second = FetchProcessLease(self.redis, key, lease_ms=1_000)  # type: ignore[arg-type]

        await first.acquire()
        with self.assertRaises(FetchWorkerAlreadyRunning):
            await second.acquire()
        await self.redis.set(key, "replacement-token", px=1_000)
        self.assertFalse(await first.release())
        self.assertEqual(await self.redis.get(key), b"replacement-token")

    async def test_runtime_uses_one_shared_limited_session(self) -> None:
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
        observed = RuntimeHandlerFactory(stop_event, total)
        runtime = FetchWorkerRuntime(
            self.queue,
            HandlerFactory(observed),  # type: ignore[arg-type]
            config=FetchWorkerConfig(
                concurrency=total,
                global_connection_limit=4,
                r2_upload_concurrency=2,
                process_lease_ms=1_000,
                process_heartbeat_ms=200,
            ),
            checker_factory=lambda client, session: AllowingChecker(),  # type: ignore[arg-type]
        )

        await asyncio.wait_for(runtime.run(stop_event), timeout=2)

        self.assertEqual(observed.factory_calls, 1)
        self.assertEqual(len(observed.session_ids), 1)
        self.assertEqual(observed.connection_limits, {4})
        self.assertEqual(len(observed.upload_limiters), 1)
        self.assertEqual(len(observed.handled), total)

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

    async def test_runtime_stops_if_process_lease_is_lost(self) -> None:
        await self.queue.enqueue(runtime_request("https://example.com/article"))
        stop_event = asyncio.Event()
        observed = RuntimeHandlerFactory(stop_event, expected=2)
        runtime = FetchWorkerRuntime(
            self.queue,
            HandlerFactory(observed),  # type: ignore[arg-type]
            config=FetchWorkerConfig(
                concurrency=1,
                process_lease_ms=100,
                process_heartbeat_ms=20,
            ),
            checker_factory=lambda client, session: AllowingChecker(),  # type: ignore[arg-type]
        )
        running = asyncio.create_task(runtime.run(stop_event))
        await asyncio.wait_for(observed.started.wait(), timeout=1)
        await self.redis.set(
            runtime.process_lock_key,
            "replacement-token",
            px=1_000,
        )

        with self.assertRaises(FetchWorkerLeaseLost):
            await asyncio.wait_for(running, timeout=1)


if __name__ == "__main__":
    unittest.main()
