import unittest
from unittest.mock import patch

import fakeredis.aioredis as fakeredis

from eng_universe.cli import build_parser
from eng_universe.config import Settings
from eng_universe.ingest.application import enqueue_fetch, make_stage_queue
from eng_universe.ingest.contracts import StageStatus


class IngestionApplicationTests(unittest.IsolatedAsyncioTestCase):
    """Tests the application boundary for the Redis ingestion queue."""

    async def asyncSetUp(self) -> None:
        self.redis = fakeredis.FakeRedis()

    async def asyncTearDown(self) -> None:
        await self.redis.aclose()

    async def test_enqueue_fetch_uses_configured_queue(self) -> None:
        with patch.object(Settings, "stage_queue_namespace", "eu:test:application"):
            queue = make_stage_queue(self.redis)  # type: ignore[arg-type]
            result = await enqueue_fetch(
                queue,
                "https://example.com/article",
                config_version="sources-1",
            )

        self.assertTrue(result.created)
        self.assertEqual(result.run.state, StageStatus.QUEUED)
        self.assertEqual(result.run.input_payload["url"], "https://example.com/article")
        self.assertEqual(queue.keys.namespace, "eu:test:application")

    def test_ingest_cli_requires_explicit_configuration_version(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "ingest",
                "enqueue-fetch",
                "https://example.com/article",
                "--config-version",
                "sources-1",
            ]
        )

        self.assertEqual(args.command, "ingest")
        self.assertEqual(args.ingest_command, "enqueue-fetch")
        self.assertEqual(args.config_version, "sources-1")

    def test_cli_preserves_curated_seed_options(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            [
                "seed",
                "--url",
                "https://stripe.dev/blog",
                "--catalog",
            ]
        )

        self.assertEqual(args.urls, ["https://stripe.dev/blog"])
        self.assertTrue(args.catalog)
        self.assertFalse(args.list_catalog)


if __name__ == "__main__":
    unittest.main()
