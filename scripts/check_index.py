import asyncio
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.ingest.queue import stage_queue
from eng_universe.ingest.queue_models import INDEX_RAW_STAGE


def _decode(value: object) -> str:
    if isinstance(value, (bytes, bytearray)):
        return value.decode()
    return str(value)


def _info_to_dict(raw: list[object]) -> dict[str, str]:
    info: dict[str, str] = {}
    for i in range(0, len(raw), 2):
        info[_decode(raw[i])] = _decode(raw[i + 1])
    return info


async def _count_keys(redis_client: redis.Redis, pattern: str) -> int:
    count = 0
    async for _ in redis_client.scan_iter(match=pattern, count=1000):
        count += 1
    return count


async def main() -> None:
    redis_client = redis.from_url(Settings.redis_url)
    index_name = "idx:blogs"
    index_exists = False
    index_docs: str | None = None

    try:
        raw_info = await redis_client.execute_command("FT.INFO", index_name)
        info = _info_to_dict(list(raw_info))
        index_exists = True
        index_docs = info.get("num_docs")
    except redis.ResponseError as exc:
        if "Unknown Index name" not in str(exc):
            raise

    doc_keys = await _count_keys(redis_client, "doc:*")
    crawl_keys = await _count_keys(redis_client, f"{Settings.crawl_doc_key_prefix}*")
    raw_queue = await stage_queue(redis_client).counts(INDEX_RAW_STAGE)

    print(f"index: {'present' if index_exists else 'missing'} ({index_name})")
    if index_docs is not None:
        print(f"index docs: {index_docs}")
    print(f"doc:* keys: {doc_keys}")
    print(f"crawl docs: {crawl_keys}")
    print(f"index_raw ready: {raw_queue.ready}")
    print(f"index_raw leased: {raw_queue.leased}")


if __name__ == "__main__":
    asyncio.run(main())
