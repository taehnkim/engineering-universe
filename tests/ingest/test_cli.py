import argparse
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis as fakeredis

from eng_universe.cli import _enqueue_fetch, _run_fetch_worker, build_parser
from eng_universe.config import Settings
from eng_universe.ingest.contracts import StageStatus
from eng_universe.ingest.fetch_worker import FetchWorkerAlreadyRunning
from eng_universe.ingest.queue_models import StageQueueKeys
from eng_universe.ingest.stage_queue import StageQueue


class IngestionCliTests(unittest.IsolatedAsyncioTestCase):
    """Tests the command boundary for the Redis ingestion queue."""

    async def asyncSetUp(self) -> None:
        self.redis = fakeredis.FakeRedis()

    async def asyncTearDown(self) -> None:
        await self.redis.aclose()

    async def test_enqueue_fetch_writes_to_configured_queue(self) -> None:
        output = io.StringIO()
        args = argparse.Namespace(
            url="https://example.com/article",
            config_version="sources-1",
            due_at_ms=0,
            max_attempts=5,
        )
        namespace = "eu:test:cli"
        with (
            patch.object(Settings, "stage_queue_namespace", namespace),
            patch("eng_universe.cli.redis.from_url", return_value=self.redis),
            redirect_stdout(output),
        ):
            await _enqueue_fetch(args)

        payload = json.loads(output.getvalue())
        queue = StageQueue(
            self.redis,  # type: ignore[arg-type]
            keys=StageQueueKeys(namespace=namespace),
        )
        run = await queue.get_run(payload["run_id"])
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.state, StageStatus.QUEUED)
        self.assertEqual(run.input_payload["url"], "https://example.com/article")

    def test_ingest_cli_requires_explicit_configuration_version(self) -> None:
        parser = build_parser()
        with (
            redirect_stderr(io.StringIO()),
            self.assertRaises(SystemExit),
        ):
            parser.parse_args(
                [
                    "ingest",
                    "enqueue-fetch",
                    "https://example.com/article",
                ]
            )

    async def test_run_fetch_worker_reports_existing_process_cleanly(self) -> None:
        error = "another fetch-worker process owns the global concurrency lease"
        stderr = io.StringIO()
        with (
            patch("eng_universe.cli.redis.from_url", return_value=self.redis),
            patch(
                "eng_universe.cli.run_fetch_worker",
                new=AsyncMock(side_effect=FetchWorkerAlreadyRunning(error)),
            ),
            redirect_stderr(stderr),
            self.assertRaisesRegex(SystemExit, "1"),
        ):
            await _run_fetch_worker()

        self.assertEqual(stderr.getvalue().strip(), error)


if __name__ == "__main__":
    unittest.main()
