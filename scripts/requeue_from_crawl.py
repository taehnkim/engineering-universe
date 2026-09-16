import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.ingest.queue import enqueue_raw_index, stage_queue
from eng_universe.ingest.queue_models import INDEX_RAW_STAGE


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Requeue crawl docs from crawl metadata."
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Clear index_raw runs before requeuing.",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=1000,
        help="Batch size for concurrent stage enqueue.",
    )
    args = parser.parse_args()

    redis_client = redis.from_url(Settings.redis_url)
    if args.clear:
        await stage_queue(redis_client).clear_stage(INDEX_RAW_STAGE)

    prefix = Settings.crawl_doc_key_prefix
    batch: list[str] = []
    total = 0

    async for key in redis_client.scan_iter(match=f"{prefix}*", count=1000):
        key_str = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
        if not key_str.startswith(prefix):
            continue
        doc_id = key_str[len(prefix) :]
        if not doc_id:
            continue
        batch.append(doc_id)
        if len(batch) >= args.batch:
            await asyncio.gather(
                *(enqueue_raw_index(redis_client, value) for value in batch)
            )
            total += len(batch)
            batch.clear()

    if batch:
        await asyncio.gather(
            *(enqueue_raw_index(redis_client, value) for value in batch)
        )
        total += len(batch)

    print(f"Requeued {total} docs into {INDEX_RAW_STAGE}.")


if __name__ == "__main__":
    asyncio.run(main())
