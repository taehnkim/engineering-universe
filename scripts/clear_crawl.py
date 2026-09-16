import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.ingest.queue import stage_queue
from eng_universe.ingest.queue_models import CRAWL_STAGE, INDEX_RAW_STAGE


async def main() -> None:
    redis_client = redis.from_url(Settings.redis_url)
    base_keys = [
        Settings.crawl_seen_key,
        Settings.crawl_doc_seq_key,
    ]
    pipe = redis_client.pipeline()
    for key in base_keys:
        pipe.delete(key)
    await pipe.execute()
    queue = stage_queue(redis_client)
    await queue.clear_stage(CRAWL_STAGE)
    await queue.clear_stage(INDEX_RAW_STAGE)

    patterns = [
        f"{Settings.crawl_doc_key_prefix}*",
        f"{Settings.robots_key_prefix}*",
        f"{Settings.robots_next_allowed_prefix}*",
    ]
    deleted = 0
    for pattern in patterns:
        async for key in redis_client.scan_iter(match=pattern, count=1000):
            await redis_client.delete(key)
            deleted += 1
    print(f"Cleared crawl queues and metadata. Deleted {deleted} keys.")


if __name__ == "__main__":
    asyncio.run(main())
