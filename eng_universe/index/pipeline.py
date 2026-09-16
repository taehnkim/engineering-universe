import asyncio
import time
from dataclasses import replace
from pathlib import Path
import uuid

import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.ingest.etl import parse_html
from eng_universe.ingest.queue import acknowledge, stage_queue
from eng_universe.ingest.queue_models import INDEX_RAW_STAGE
from eng_universe.index.entities import extract_topics
from eng_universe.index.indexer import index_document, log_event
from eng_universe.storage.r2 import download_text, r2_enabled, upload_json, upload_text


def _read_text(path: str) -> str:
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8")


def _decode_bytes(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return value.decode()
    return str(value)


def _decode_int(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode()
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def index_worker(doc_key_prefix: str | None = None) -> None:
    redis_client = redis.from_url(Settings.redis_url)
    queue = stage_queue(redis_client)
    worker_id = f"indexer:{uuid.uuid4().hex}"
    prefix = doc_key_prefix or Settings.crawl_doc_key_prefix
    last_idle_log = 0.0
    idle_since: float | None = None
    while True:
        await queue.reclaim_expired(INDEX_RAW_STAGE)
        lease = await queue.claim(INDEX_RAW_STAGE, worker_id=worker_id)
        if lease is None:
            now = time.time()
            if idle_since is None:
                idle_since = now
            if now - last_idle_log > 10:
                log_event("idle", queue=Settings.raw_queue_key)
                last_idle_log = now
            if (
                Settings.indexer_exit_on_idle
                and now - idle_since >= Settings.indexer_idle_grace_s
            ):
                log_event(
                    "done",
                    reason="idle",
                    queue=Settings.raw_queue_key,
                    idle_s=round(now - idle_since, 1),
                )
                break
            await asyncio.sleep(0.2)
            continue
        idle_since = None
        raw_doc_id = lease.run.input_payload.get("doc_id")
        if not isinstance(raw_doc_id, str):
            raise TypeError("index_raw input must contain a string doc_id")
        crawl_meta = await redis_client.hgetall(f"{prefix}{raw_doc_id}")
        if not crawl_meta:
            log_event("skip", doc_id=raw_doc_id, reason="missing_meta")
            await acknowledge(redis_client, lease)
            continue
        url = _decode_bytes(crawl_meta.get(b"url"))
        source = _decode_bytes(crawl_meta.get(b"source"))
        raw_path = _decode_bytes(crawl_meta.get(b"raw_path"))
        cleaned_path = _decode_bytes(crawl_meta.get(b"cleaned_path"))
        raw_key = _decode_bytes(crawl_meta.get(b"raw_key"))
        clean_key = _decode_bytes(crawl_meta.get(b"clean_key"))
        if not raw_key:
            raw_key = f"raw/{raw_doc_id}.html"
        if not clean_key:
            clean_key = f"clean/{raw_doc_id}.txt"
        domain = _decode_bytes(crawl_meta.get(b"domain"))
        depth = _decode_int(crawl_meta.get(b"depth"))
        fetched_at = _decode_int(crawl_meta.get(b"fetched_at"))
        status = _decode_int(crawl_meta.get(b"status"))
        raw_html = ""
        if r2_enabled():
            try:
                raw_html = await asyncio.to_thread(download_text, raw_key)
            except Exception as exc:
                log_event("r2_fail", doc_id=raw_doc_id, error=type(exc).__name__)
                raw_html = ""
        if not raw_html:
            raw_html = _read_text(raw_path)
        cleaned_html = _read_text(cleaned_path)
        if not url or not (raw_html or cleaned_html):
            log_event("skip", doc_id=raw_doc_id, url=url, reason="missing_html")
            continue
        base_html = raw_html or cleaned_html
        parsed = parse_html(url, base_html)
        if cleaned_html:
            cleaned_parsed = parse_html(url, cleaned_html)
            parsed = replace(parsed, content=cleaned_parsed.content)
        if r2_enabled():
            index_payload = {
                "doc_id": int(raw_doc_id) if raw_doc_id.isdigit() else raw_doc_id,
                "url": parsed.url,
                "canonical_url": parsed.canonical_url,
                "title": parsed.title,
                "content": parsed.content,
                "authors": parsed.authors,
                "company": parsed.company,
                "published_at": parsed.published_at,
                "language": parsed.language,
                "source": source,
                "domain": domain,
                "depth": depth,
                "fetched_at": fetched_at,
                "status": status,
                "topics": extract_topics(parsed.content),
                "raw_key": raw_key,
                "clean_key": clean_key,
            }
            try:
                await asyncio.to_thread(
                    upload_text,
                    parsed.content,
                    clean_key,
                )
                await asyncio.to_thread(
                    upload_json,
                    index_payload,
                    f"index/{raw_doc_id}.json",
                )
            except Exception as exc:
                log_event("r2_fail", doc_id=raw_doc_id, error=type(exc).__name__)
        await index_document(redis_client, parsed, source=source)
        await acknowledge(redis_client, lease)
