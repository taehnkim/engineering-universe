from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from typing import TypeVar

import aiohttp

from eng_universe.config import Settings
from eng_universe.ingest.fetch_boundary import FetchPathChecker, make_fetch_handler
from eng_universe.ingest.queue_models import FETCH_RAW_STAGE
from eng_universe.ingest.stage_queue import StageQueue
from eng_universe.ingest.worker import StageHandler, StageWorkerPool

# _COMPARE_EXPIRE — renew a process lease only when the token matches.
_COMPARE_EXPIRE = r"""
-- Renew key TTL only when the holder token still matches.
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
return redis.call("PEXPIRE", KEYS[1], ARGV[2])
"""

# _COMPARE_DELETE — release a process lease only when the token matches.
_COMPARE_DELETE = r"""
-- Delete key only when the holder token still matches.
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
return redis.call("DEL", KEYS[1])
"""

ResultT = TypeVar("ResultT")

IDLE_SLEEP_MS = 100


class FetchWorkerAlreadyRunning(RuntimeError):
    """Reports that another fetch process owns the lease."""


class FetchWorkerLeaseLost(RuntimeError):
    """Reports that the fetch process lost its lease."""


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


class ProcessLease:
    """Prevents more than one active fetch process."""

    def __init__(
        self,
        redis_client: object,
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


def fetch_process_lock_key(queue: StageQueue) -> str:
    return f"{queue.keys.namespace}:lock:fetch-worker"


def _validate_fetch_settings() -> None:
    if Settings.fetch_worker_concurrency < 1:
        raise ValueError("fetch concurrency must be positive")
    if Settings.fetch_global_connection_limit < 1:
        raise ValueError("global connection limit must be positive")
    if Settings.fetch_origin_max_inflight < 1:
        raise ValueError("origin max-inflight must be positive")
    if Settings.fetch_r2_upload_concurrency < 1:
        raise ValueError("R2 upload concurrency must be positive")
    if Settings.fetch_process_lease_ms < 1:
        raise ValueError("process lease must be positive")
    if (
        Settings.fetch_process_heartbeat_ms < 1
        or Settings.fetch_process_heartbeat_ms >= Settings.fetch_process_lease_ms
    ):
        raise ValueError(
            "process heartbeat must be positive and shorter than lease"
        )
    if Settings.stage_lease_ms <= Settings.request_timeout_s * 1000:
        raise ValueError(
            "fetch stage lease must be longer than the HTTP request timeout"
        )


async def _heartbeat_process_lease(
    process_lease: ProcessLease,
    heartbeat_stop: asyncio.Event,
    worker_stop: asyncio.Event,
) -> None:
    while not heartbeat_stop.is_set() and not worker_stop.is_set():
        try:
            await asyncio.wait_for(
                heartbeat_stop.wait(),
                timeout=Settings.fetch_process_heartbeat_ms / 1000,
            )
        except TimeoutError:
            if not await process_lease.renew():
                worker_stop.set()
                raise FetchWorkerLeaseLost(
                    "fetch-worker process lost the global concurrency lease"
                )


async def run_fetch_worker(
    queue: StageQueue,
    stop: asyncio.Event,
    *,
    handler: StageHandler | None = None,
    checker: FetchPathChecker | None = None,
) -> None:
    _validate_fetch_settings()
    process_lease = ProcessLease(
        queue.redis,
        fetch_process_lock_key(queue),
        lease_ms=Settings.fetch_process_lease_ms,
    )
    await process_lease.acquire()
    await queue.configure_fetch_global_limit(Settings.fetch_global_connection_limit)
    heartbeat_stop = asyncio.Event()
    connector = aiohttp.TCPConnector(
        limit=Settings.fetch_global_connection_limit,
        limit_per_host=0,
    )
    timeout = aiohttp.ClientTimeout(total=Settings.request_timeout_s)
    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"User-Agent": Settings.user_agent},
        ) as session:
            uploads = R2UploadLimiter(Settings.fetch_r2_upload_concurrency)
            selected: StageHandler
            if handler is not None:
                selected = handler
            else:
                selected = make_fetch_handler(queue, session, checker, uploads)
            pool = StageWorkerPool(
                queue,
                {FETCH_RAW_STAGE: selected},
                concurrency=Settings.fetch_worker_concurrency,
                lease_ms=Settings.stage_lease_ms,
                heartbeat_interval_ms=Settings.stage_heartbeat_ms,
                idle_sleep_ms=IDLE_SLEEP_MS,
                reclaim_interval_ms=Settings.stage_reclaim_interval_ms,
                pool_id="fetch",
            )
            pool_task = asyncio.create_task(pool.run(stop))
            heartbeat_task = asyncio.create_task(
                _heartbeat_process_lease(process_lease, heartbeat_stop, stop)
            )
            try:
                done, _ = await asyncio.wait(
                    {pool_task, heartbeat_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if heartbeat_task in done:
                    exception = heartbeat_task.exception()
                    if exception is not None:
                        stop.set()
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
