from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis as fakeredis

from eng_universe.config import Settings
from eng_universe.index.pipeline import index_worker
from eng_universe.ingest.queue import enqueue_raw_index, stage_queue
from eng_universe.ingest.queue_models import INDEX_RAW_STAGE

RAW_HTML = """
<html>
  <head>
    <title>Demo Post</title>
    <meta property="og:title" content="Demo Post" />
  </head>
  <body>
    <nav>Skip</nav>
    <main><p>Clean body text.</p></main>
  </body>
</html>
"""


class IndexPipelineCleanTests(unittest.IsolatedAsyncioTestCase):
    """Index worker: raw R2 → parse/clean → clean/{id}.txt → Redis index."""

    async def asyncSetUp(self) -> None:
        self.redis = fakeredis.FakeRedis()
        self.doc_id = "7"
        self.prefix = Settings.crawl_doc_key_prefix
        namespace = f"eu:clean-test:{uuid.uuid4().hex}"
        self.patches = [
            patch.object(Settings, "stage_queue_namespace", namespace),
            patch.object(Settings, "indexer_exit_on_idle", True),
            patch.object(Settings, "indexer_idle_grace_s", 0),
            patch.object(Settings, "keyword_only", True),
            patch("eng_universe.index.pipeline.redis.from_url", return_value=self.redis),
        ]
        for item in self.patches:
            item.start()
        await self.redis.hset(
            f"{self.prefix}{self.doc_id}",
            mapping={
                "url": "https://example.com/blog/demo",
                "domain": "example.com",
                "source": "seed",
                "depth": "1",
                "raw_key": f"raw/{self.doc_id}.html",
                "clean_key": f"clean/{self.doc_id}.txt",
                "fetched_at": "1700000000",
                "status": "200",
            },
        )
        await enqueue_raw_index(self.redis, self.doc_id)

    async def asyncTearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        await self.redis.aclose()

    async def _failed_run(self):
        queue = stage_queue(self.redis)
        counts = await queue.counts(INDEX_RAW_STAGE)
        if counts.dead:
            run_id = (
                await self.redis.zrange(queue.keys.dead(INDEX_RAW_STAGE), 0, 0)
            )[0].decode()
        else:
            run_id = (
                await self.redis.zrange(queue.keys.ready(INDEX_RAW_STAGE), 0, 0)
            )[0].decode()
        run = await queue.get_run(run_id)
        self.assertIsNotNone(run)
        assert run is not None
        return queue, run, counts

    async def test_reads_raw_writes_clean_and_indexes(self) -> None:
        uploads: dict[str, object] = {}

        def fake_download(key: str) -> str | None:
            self.assertEqual(key, f"raw/{self.doc_id}.html")
            return RAW_HTML

        def fake_upload_text(text: str, key: str, **kwargs: object) -> bool:
            uploads[key] = text
            return True

        def fake_upload_json(payload: object, key: str) -> bool:
            uploads[key] = payload
            return True

        with (
            patch("eng_universe.index.pipeline.r2_enabled", return_value=True),
            patch(
                "eng_universe.index.pipeline.download_text",
                side_effect=fake_download,
            ),
            patch(
                "eng_universe.index.pipeline.upload_text",
                side_effect=fake_upload_text,
            ),
            patch(
                "eng_universe.index.pipeline.upload_json",
                side_effect=fake_upload_json,
            ),
            patch(
                "eng_universe.index.pipeline.index_document",
                new_callable=AsyncMock,
            ) as index_document,
        ):
            await index_worker()

        self.assertEqual(uploads[f"clean/{self.doc_id}.txt"], "Clean body text.")
        index_payload = uploads[f"index/{self.doc_id}.json"]
        assert isinstance(index_payload, dict)
        self.assertNotIn("content", index_payload)
        self.assertEqual(index_payload["clean_key"], f"clean/{self.doc_id}.txt")
        self.assertEqual(index_payload["raw_key"], f"raw/{self.doc_id}.html")
        self.assertEqual(index_payload["title"], "Demo Post")
        index_document.assert_awaited_once()
        parsed = index_document.await_args.args[1]
        self.assertEqual(parsed.content, "Clean body text.")
        self.assertEqual(parsed.title, "Demo Post")

        queue = stage_queue(self.redis)
        counts = await queue.counts(INDEX_RAW_STAGE)
        self.assertEqual(counts.ready, 0)
        self.assertEqual(counts.leased, 0)

    async def test_r2_miss_is_permanent_without_clean_upload(self) -> None:
        uploads: list[str] = []

        with (
            patch("eng_universe.index.pipeline.r2_enabled", return_value=True),
            patch("eng_universe.index.pipeline.download_text", return_value=None),
            patch(
                "eng_universe.index.pipeline.upload_text",
                side_effect=lambda *a, **k: uploads.append("text") or True,
            ),
            patch(
                "eng_universe.index.pipeline.upload_json",
                side_effect=lambda *a, **k: uploads.append("json") or True,
            ),
            patch(
                "eng_universe.index.pipeline.index_document",
                new_callable=AsyncMock,
            ) as index_document,
        ):
            await index_worker()

        self.assertEqual(uploads, [])
        index_document.assert_not_awaited()
        _, run, counts = await self._failed_run()
        self.assertEqual(counts.ready, 0)
        self.assertEqual(counts.dead, 1)
        self.assertEqual(run.state.value, "failed")
        self.assertEqual(run.error_code, "r2_miss")
        self.assertEqual(run.error_class, "permanent")

    async def test_r2_download_exception_is_retried(self) -> None:
        with (
            patch("eng_universe.index.pipeline.r2_enabled", return_value=True),
            patch(
                "eng_universe.index.pipeline.download_text",
                side_effect=RuntimeError("timeout"),
            ),
            patch(
                "eng_universe.index.pipeline.index_document",
                new_callable=AsyncMock,
            ) as index_document,
        ):
            await index_worker()

        index_document.assert_not_awaited()
        _, run, counts = await self._failed_run()
        self.assertEqual(counts.ready, 1)
        self.assertEqual(counts.dead, 0)
        self.assertEqual(run.state.value, "queued")
        self.assertEqual(run.error_code, "r2_download_failed")
        self.assertEqual(run.error_class, "retryable")

    async def test_missing_url_is_permanent(self) -> None:
        await self.redis.hset(f"{self.prefix}{self.doc_id}", mapping={"url": ""})
        with (
            patch("eng_universe.index.pipeline.r2_enabled", return_value=True),
            patch("eng_universe.index.pipeline.download_text", return_value=RAW_HTML),
            patch(
                "eng_universe.index.pipeline.index_document",
                new_callable=AsyncMock,
            ) as index_document,
        ):
            await index_worker()

        index_document.assert_not_awaited()
        _, run, counts = await self._failed_run()
        self.assertEqual(counts.ready, 0)
        self.assertEqual(counts.dead, 1)
        self.assertEqual(run.state.value, "failed")
        self.assertEqual(run.error_code, "missing_url")
        self.assertEqual(run.error_class, "permanent")

    async def test_clean_upload_false_is_retried_before_index(self) -> None:
        with (
            patch("eng_universe.index.pipeline.r2_enabled", return_value=True),
            patch("eng_universe.index.pipeline.download_text", return_value=RAW_HTML),
            patch("eng_universe.index.pipeline.upload_text", return_value=False),
            patch("eng_universe.index.pipeline.upload_json", return_value=True),
            patch(
                "eng_universe.index.pipeline.index_document",
                new_callable=AsyncMock,
            ) as index_document,
        ):
            await index_worker()

        index_document.assert_not_awaited()
        _, run, counts = await self._failed_run()
        self.assertEqual(counts.ready, 1)
        self.assertEqual(run.state.value, "queued")
        self.assertEqual(run.error_code, "r2_upload_failed")

    async def test_clean_upload_exception_is_retried_before_index(self) -> None:
        with (
            patch("eng_universe.index.pipeline.r2_enabled", return_value=True),
            patch("eng_universe.index.pipeline.download_text", return_value=RAW_HTML),
            patch(
                "eng_universe.index.pipeline.upload_text",
                side_effect=RuntimeError("put failed"),
            ),
            patch(
                "eng_universe.index.pipeline.index_document",
                new_callable=AsyncMock,
            ) as index_document,
        ):
            await index_worker()

        index_document.assert_not_awaited()
        _, run, counts = await self._failed_run()
        self.assertEqual(counts.ready, 1)
        self.assertEqual(run.state.value, "queued")
        self.assertEqual(run.error_code, "r2_upload_failed")


if __name__ == "__main__":
    unittest.main()
