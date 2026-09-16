import argparse
import asyncio
from pathlib import Path
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import aiohttp
import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.ingest.crawler import crawl_worker
from eng_universe.ingest.queue import reclaim_leases, stage_queue
from eng_universe.ingest.queue_models import CRAWL_STAGE
from eng_universe.index.pipeline import index_worker


async def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl, then run the indexer.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Number of crawler workers to run.",
    )
    parser.add_argument(
        "--idle-grace",
        type=float,
        default=5.0,
        help="Stop crawling after queues are empty for N seconds.",
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=0,
        help="Stop after storing this many docs (0 = no max).",
    )
    args = parser.parse_args()

    Settings.max_workers = max(1, args.concurrency)
    redis_client = redis.from_url(Settings.redis_url)
    prefix = Settings.crawl_doc_key_prefix
    stop_event = asyncio.Event()

    max_docs = args.max_docs if args.max_docs > 0 else None
    counter = [0] if max_docs is not None else None
    counter_lock = asyncio.Lock() if max_docs is not None else None

    async with aiohttp.ClientSession(
        headers={"User-Agent": Settings.user_agent}
    ) as session:
        workers = [
            asyncio.create_task(
                crawl_worker(
                    redis_client,
                    session,
                    prefix,
                    stop_event=stop_event,
                    max_docs=max_docs,
                    counter=counter,
                    counter_lock=counter_lock,
                )
            )
            for _ in range(Settings.max_workers)
        ]
        reclaimer = asyncio.create_task(
            reclaim_leases(redis_client, (CRAWL_STAGE,), stop_event),
            name="crawl-reclaimer",
        )
        queue = stage_queue(redis_client)
        last_active = time.time()
        try:
            while True:
                if stop_event.is_set():
                    break
                counts = await queue.counts(CRAWL_STAGE)
                if counts.ready == 0 and counts.leased == 0:
                    if time.time() - last_active >= max(0.0, args.idle_grace):
                        stop_event.set()
                        break
                else:
                    last_active = time.time()
                await asyncio.sleep(0.5)
            await asyncio.gather(*workers, return_exceptions=True)
        finally:
            stop_event.set()
            if not reclaimer.done():
                reclaimer.cancel()
            await asyncio.gather(reclaimer, return_exceptions=True)

    await index_worker()


if __name__ == "__main__":
    asyncio.run(main())
