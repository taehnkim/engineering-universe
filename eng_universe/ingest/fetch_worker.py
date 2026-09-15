from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, TypeVar

import aiohttp
import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.ingest.fetch_boundary import (
    AuthorizedFetchHandler,
    FetchPathChecker,
    RobotsAwareFetchHandler,
)
from eng_universe.ingest.queue_models import FETCH_RAW_STAGE
from eng_universe.ingest.stage_queue import StageQueue
from eng_universe.ingest.worker import StageWorkerPool

_COMPARE_EXPIRE = r"""
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
return redis.call("PEXPIRE", KEYS[1], ARGV[2])
"""

_COMPARE_DELETE = r"""
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
return redis.call("DEL", KEYS[1])
"""

ResultT = TypeVar("ResultT")


class FetchWorkerAlreadyRunning(RuntimeError):
    """Reports that another fetch process owns the lease."""


class FetchWorkerLeaseLost(RuntimeError):
    """Reports that the fetch process lost its lease."""


@dataclass(frozen=True, slots=True)
class FetchWorkerConfig:
    """Defines fetch process concurrency limits."""

    process_count: int = 1
    concurrency: int = 100
    global_connection_limit: int = 100
    origin_max_inflight: int = 1
    r2_upload_concurrency: int = 8
    process_lease_ms: int = 30_000
    process_heartbeat_ms: int = 10_000
    idle_sleep_ms: int = 100

    def __post_init__(self) -> None:
        if self.process_count != 1:
            raise ValueError(
                "fetch worker process_count must remain 1; "
                "the global connection limit is process-local"
            )
        if self.concurrency < 1:
            raise ValueError("fetch concurrency must be positive")
        if self.global_connection_limit < 1:
            raise ValueError("global connection limit must be positive")
        if self.origin_max_inflight < 1:
            raise ValueError("origin max-inflight must be positive")
        if self.r2_upload_concurrency < 1:
            raise ValueError("R2 upload concurrency must be positive")
        if self.process_lease_ms < 1:
            raise ValueError("process lease must be positive")
        if (
            self.process_heartbeat_ms < 1
            or self.process_heartbeat_ms >= self.process_lease_ms
        ):
            raise ValueError(
                "process heartbeat must be positive and shorter than lease"
            )

    @classmethod
    def from_settings(cls) -> FetchWorkerConfig:
        return cls(
            process_count=Settings.fetch_worker_processes,
            concurrency=Settings.fetch_worker_concurrency,
            global_connection_limit=Settings.fetch_global_connection_limit,
            origin_max_inflight=Settings.fetch_origin_max_inflight,
            r2_upload_concurrency=Settings.fetch_r2_upload_concurrency,
            process_lease_ms=Settings.fetch_process_lease_ms,
            process_heartbeat_ms=Settings.fetch_process_heartbeat_ms,
        )


class R2UploadLimiter:
    """Limits concurrent blocking R2 uploads."""

    def __init__(self, concurrency: int) -> None:
        if concurrency < 1:
            raise ValueError("R2 upload concurrency must be positive")
        self.concurrency = concurrency
        self._semaphore = asyncio.BoundedSemaphore(concurrency)

    async def run(
        self,
        operation: Callable[..., ResultT],
        *args: object,
        **kwargs: object,
    ) -> ResultT:
        async with self._semaphore:
            return await asyncio.to_thread(operation, *args, **kwargs)


class FetchHandlerFactory(Protocol):
    """Builds a handler for the shared HTTP session."""

    def __call__(
        self,
        session: aiohttp.ClientSession,
        uploads: R2UploadLimiter,
    ) -> AuthorizedFetchHandler:
        """
        Build one raw-fetch handler for the shared session.

        The handler must return raw fetch metadata and artifact references.
        CPU parsing belongs in a downstream stage worker.
        """


class FetchProcessLease:
    """Prevents more than one active fetch process."""

    def __init__(
        self,
        redis_client: redis.Redis,
        key: str,
        *,
        lease_ms: int,
    ) -> None:
        self.redis = redis_client
        self.key = key
        self.lease_ms = lease_ms
        self.token = uuid.uuid4().hex
        self.acquired = False

    async def acquire(self) -> None:
        acquired = await self.redis.set(
            self.key,
            self.token,
            nx=True,
            px=self.lease_ms,
        )
        if not acquired:
            raise FetchWorkerAlreadyRunning(
                "another fetch-worker process owns the global concurrency lease"
            )
        self.acquired = True

    async def renew(self) -> bool:
        if not self.acquired:
            return False
        renewed = await self.redis.eval(
            _COMPARE_EXPIRE,
            1,
            self.key,
            self.token,
            self.lease_ms,
        )
        return bool(renewed)

    async def release(self) -> bool:
        if not self.acquired:
            return False
        released = await self.redis.eval(
            _COMPARE_DELETE,
            1,
            self.key,
            self.token,
        )
        self.acquired = False
        return bool(released)


class FetchWorkerRuntime:
    """Runs the single async fetch process."""

    def __init__(
        self,
        queue: StageQueue,
        handler_factory: FetchHandlerFactory,
        *,
        config: FetchWorkerConfig | None = None,
        checker_factory: Callable[
            [redis.Redis, aiohttp.ClientSession], FetchPathChecker
        ]
        | None = None,
    ) -> None:
        self.queue = queue
        self.handler_factory = handler_factory
        self.config = config or FetchWorkerConfig.from_settings()
        self.checker_factory = checker_factory or self._default_checker
        self.process_lock_key = f"{self.queue.keys.namespace}:lock:fetch-worker"

    @staticmethod
    def _default_checker(
        redis_client: redis.Redis,
        session: aiohttp.ClientSession,
    ) -> FetchPathChecker:
        return FetchPathChecker(redis_client, session)

    async def run(self, stop_event: asyncio.Event) -> None:
        if Settings.stage_lease_ms <= Settings.request_timeout_s * 1000:
            raise ValueError(
                "fetch stage lease must be longer than the HTTP request timeout"
            )
        process_lease = FetchProcessLease(
            self.queue.redis,
            self.process_lock_key,
            lease_ms=self.config.process_lease_ms,
        )
        await process_lease.acquire()
        await self.queue.configure_fetch_global_limit(
            self.config.global_connection_limit
        )
        heartbeat_stop = asyncio.Event()
        connector = aiohttp.TCPConnector(
            limit=self.config.global_connection_limit,
            limit_per_host=0,
        )
        timeout = aiohttp.ClientTimeout(total=Settings.request_timeout_s)
        try:
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
                headers={"User-Agent": Settings.user_agent},
            ) as session:
                uploads = R2UploadLimiter(self.config.r2_upload_concurrency)
                checker = self.checker_factory(self.queue.redis, session)
                fetch_handler = RobotsAwareFetchHandler(
                    self.queue,
                    checker,
                    self.handler_factory(session, uploads),
                    max_inflight=self.config.origin_max_inflight,
                )
                pool = StageWorkerPool(
                    self.queue,
                    {FETCH_RAW_STAGE: fetch_handler},
                    concurrency=self.config.concurrency,
                    lease_ms=Settings.stage_lease_ms,
                    heartbeat_interval_ms=Settings.stage_heartbeat_ms,
                    idle_sleep_ms=self.config.idle_sleep_ms,
                    reclaim_interval_ms=Settings.stage_reclaim_interval_ms,
                    pool_id="fetch",
                )
                pool_task = asyncio.create_task(pool.run(stop_event))
                heartbeat_task = asyncio.create_task(
                    self._heartbeat_process_lease(
                        process_lease,
                        heartbeat_stop,
                        stop_event,
                    )
                )
                try:
                    done, _ = await asyncio.wait(
                        {pool_task, heartbeat_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if heartbeat_task in done:
                        exception = heartbeat_task.exception()
                        if exception is not None:
                            stop_event.set()
                            pool_task.cancel()
                            await asyncio.gather(pool_task, return_exceptions=True)
                            raise exception
                    await pool_task
                finally:
                    heartbeat_stop.set()
                    if not heartbeat_task.done():
                        heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)
        finally:
            await process_lease.release()

    async def _heartbeat_process_lease(
        self,
        process_lease: FetchProcessLease,
        heartbeat_stop: asyncio.Event,
        worker_stop: asyncio.Event,
    ) -> None:
        while not heartbeat_stop.is_set() and not worker_stop.is_set():
            try:
                await asyncio.wait_for(
                    heartbeat_stop.wait(),
                    timeout=self.config.process_heartbeat_ms / 1000,
                )
            except TimeoutError:
                if not await process_lease.renew():
                    worker_stop.set()
                    raise FetchWorkerLeaseLost(
                        "fetch-worker process lost the global concurrency lease"
                    )
