from __future__ import annotations

import asyncio
from dataclasses import dataclass

import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.ingest.contracts import JsonValue, StageIdentity, StageRequest
from eng_universe.ingest.fetch_worker import run_fetch_worker
from eng_universe.ingest.queue_models import (
    FETCH_RAW_STAGE,
    EnqueueResult,
    StageQueueKeys,
)
from eng_universe.ingest.stage_queue import StageQueue

FETCH_RAW_IDENTITY = StageIdentity(name=FETCH_RAW_STAGE, version="1.0.0")


@dataclass(frozen=True, slots=True)
class FetchRawInput:
    """Provides the URL that identifies one fetch request."""

    url: str

    def idempotency_payload(self) -> dict[str, JsonValue]:
        return {"url": self.url}


def make_stage_queue(redis_client: redis.Redis) -> StageQueue:
    """Builds the configured Redis stage queue."""

    return StageQueue(
        redis_client,
        keys=StageQueueKeys(namespace=Settings.stage_queue_namespace),
        default_lease_ms=Settings.stage_lease_ms,
        fetch_global_limit=Settings.fetch_global_connection_limit,
    )


async def enqueue_fetch(
    queue: StageQueue,
    url: str,
    *,
    config_version: str,
    due_at_ms: int = 0,
    max_attempts: int = 5,
) -> EnqueueResult:
    """Enqueues one URL for the existing fetch_raw stage."""

    request = StageRequest(
        identity=FETCH_RAW_IDENTITY,
        stage_input=FetchRawInput(url=url),
        config_version=config_version,
    )
    return await queue.enqueue(
        request,
        due_at_ms=due_at_ms,
        max_attempts=max_attempts,
        origin_max_inflight=Settings.fetch_origin_max_inflight,
    )


async def run_fetch_application(queue: StageQueue, stop: asyncio.Event) -> None:
    """Runs the existing fetch worker with application configuration."""

    await run_fetch_worker(queue, stop)
