import time
import uuid
from dataclasses import dataclass

import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.ingest.contracts import JsonValue, StageIdentity, StageRequest
from eng_universe.ingest.queue_models import (
    CRAWL_STAGE,
    INDEX_RAW_STAGE,
    StageLease,
    StageQueueKeys,
)
from eng_universe.ingest.stage_queue import StageQueue
from eng_universe.monitoring.logging_utils import get_event_logger

log_event = get_event_logger("queue")


@dataclass(frozen=True, slots=True)
class CrawlItem:
    url: str
    source: str
    depth: int = 0

    def idempotency_payload(self) -> dict[str, JsonValue]:
        return {"url": self.url, "source": self.source, "depth": self.depth}


@dataclass(frozen=True, slots=True)
class _CrawlQueueInput:
    """Adds a nonce without changing the crawler-visible item."""

    item: CrawlItem
    nonce: str | None = None

    def idempotency_payload(self) -> dict[str, JsonValue]:
        payload = self.item.idempotency_payload()
        if self.nonce is not None:
            payload["enqueue_nonce"] = self.nonce
        return payload


@dataclass(frozen=True, slots=True)
class RawIndexItem:
    """Identifies one stored crawl document that is ready for indexing."""

    doc_id: str

    def idempotency_payload(self) -> dict[str, JsonValue]:
        return {"doc_id": self.doc_id}


CRAWL_IDENTITY = StageIdentity(name=CRAWL_STAGE, version="1.0.0")
INDEX_RAW_IDENTITY = StageIdentity(name=INDEX_RAW_STAGE, version="1.0.0")
LIVE_QUEUE_CONFIG_VERSION = "legacy-ingestion-1"


def stage_queue(redis_client: redis.Redis) -> StageQueue:
    """Builds the configured durable queue for live ingestion workers."""
    return StageQueue(
        redis_client,
        keys=StageQueueKeys(namespace=Settings.stage_queue_namespace),
        default_lease_ms=Settings.stage_lease_ms,
    )


def _serialize(item: CrawlItem) -> str:
    return f"{item.url}\t{item.source}\t{item.depth}"


def _deserialize(raw: bytes) -> CrawlItem | None:
    """Deserializes redis item from bytes to CrawlItem"""
    parts = raw.decode().split("\t")
    if len(parts) < 2:
        return None
    url = parts[0]
    source = parts[1]
    depth = 0
    if len(parts) > 2:
        try:
            depth = int(parts[2])
        except ValueError:
            depth = 0
    return CrawlItem(url=url, source=source, depth=depth)


async def enqueue(
    redis_client: redis.Redis, item: CrawlItem, *, dedupe: bool = True
) -> None:
    log_event("enqueue", item=item)
    if dedupe:
        # Try adding url to redis set
        added = await redis_client.sadd(Settings.crawl_seen_key, item.url)
        if not added:
            return
    request_item = _CrawlQueueInput(
        item,
        nonce=None if dedupe else uuid.uuid4().hex,
    )
    try:
        # Append url to queue
        await stage_queue(redis_client).enqueue(
            StageRequest(
                identity=CRAWL_IDENTITY,
                stage_input=request_item,
                config_version=LIVE_QUEUE_CONFIG_VERSION,
            )
        )
    except Exception:
        if dedupe:
            await redis_client.srem(Settings.crawl_seen_key, item.url)
        raise


async def dequeue(
    redis_client: redis.Redis,
    *,
    worker_id: str = "crawler",
) -> tuple[CrawlItem, StageLease] | None:
    lease = await stage_queue(redis_client).claim(CRAWL_STAGE, worker_id=worker_id)
    if lease is None:
        return None
    payload = lease.run.input_payload
    url = payload.get("url")
    source = payload.get("source")
    depth = payload.get("depth", 0)
    if not isinstance(url, str) or not isinstance(source, str) or not isinstance(depth, int):
        raise TypeError("crawl queue payload is invalid")
    return CrawlItem(url=url, source=source, depth=depth), lease


async def acknowledge(redis_client: redis.Redis, lease: StageLease) -> None:
    await stage_queue(redis_client).complete(lease)


async def delay(redis_client: redis.Redis, lease: StageLease, when_ts: int) -> None:
    await stage_queue(redis_client).defer(
        lease,
        due_at_ms=when_ts * 1000,
    )


async def requeue_delayed_items(redis_client: redis.Redis, max_items: int = 100) -> int:
    """
    Moves items from the delay queue back to main crawl queue when their
    scheduled time arrives. It queries for items with timestamps up to current time,
    removes them from delay queue, and pushes them back to the main queue for processing.
    """
    now = time.time()

    # Fetch earliest items until now
    script = """
local items = redis.call("ZRANGEBYSCORE", KEYS[1], "-inf", ARGV[1], "LIMIT", 0, ARGV[2])
for _, item in ipairs(items) do
    if redis.call("ZREM", KEYS[1], item) == 1 then
        redis.call("RPUSH", KEYS[2], item)
    end
end
return #items
"""
    return int(
        await redis_client.eval(
            script,
            2,
            Settings.crawl_delay_key,
            Settings.crawl_queue_key,
            now,
            max_items,
        )
    )


async def enqueue_raw_index(redis_client: redis.Redis, doc_id: str | int) -> None:
    await stage_queue(redis_client).enqueue(
        StageRequest(
            identity=INDEX_RAW_IDENTITY,
            stage_input=RawIndexItem(str(doc_id)),
            config_version=LIVE_QUEUE_CONFIG_VERSION,
        )
    )
