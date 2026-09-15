from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis.aioredis as fakeredis

from eng_universe.ingest.contracts import JsonValue, StageIdentity, StageRequest
from eng_universe.ingest.fetch_boundary import (
    FetchPathChecker,
    FetchPathDecision,
    make_fetch_handler,
)
from eng_universe.ingest.queue_models import (
    FETCH_RAW_STAGE,
    Origin,
    StageQueueKeys,
)
from eng_universe.ingest.robots import RobotsRules
from eng_universe.ingest.stage_queue import StageQueue
from eng_universe.ingest.worker import StageWorkerPool


@dataclass(frozen=True)
class FetchInput:
    """Provides a URL for fetch boundary tests."""

    url: str

    def idempotency_payload(self) -> Mapping[str, JsonValue]:
        return {"url": self.url}


def fetch_request(url: str) -> StageRequest[FetchInput]:
    return StageRequest(
        identity=StageIdentity(name=FETCH_RAW_STAGE, version="1.0.0"),
        stage_input=FetchInput(url),
        config_version="sources-1",
    )


class DenyingChecker:
    """Denies each URL used by a test."""

    async def check(self, url: str) -> FetchPathDecision:
        return FetchPathDecision(
            url=url,
            origin=Origin.from_url(url),
            allowed=False,
            request_interval_ms=2_000,
            robots_fetched_at=1,
        )


class FetchBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Tests robots checks at the fetch boundary."""

    async def asyncSetUp(self) -> None:
        self.redis = fakeredis.FakeRedis()
        self.queue = StageQueue(
            self.redis,  # type: ignore[arg-type]
            keys=StageQueueKeys(namespace="eu:fetch-test:v1"),
            retry_base_ms=0,
            retry_max_ms=0,
        )

    async def asyncTearDown(self) -> None:
        await self.redis.aclose()

    async def test_checker_applies_robots_to_exact_path(self) -> None:
        rules = RobotsRules(
            domain="example.com",
            crawl_delay_s=2,
            request_rate_s=0,
            allowed=True,
            fetched_at=123,
            text=(
                "User-agent: *\nAllow: /\nDisallow: /private\nAllow: /private/public\n"
            ),
        )
        session = object()
        checker = FetchPathChecker(
            self.redis,  # type: ignore[arg-type]
            session,  # type: ignore[arg-type]
            user_agent="EngUniverseBot/0.1",
        )

        with patch(
            "eng_universe.ingest.fetch_boundary.get_or_fetch_robots",
            new=AsyncMock(return_value=rules),
        ) as fetch_rules:
            denied = await checker.check("https://example.com/private/article")
            allowed = await checker.check("https://example.com/private/public/article")

        self.assertFalse(denied.allowed)
        self.assertTrue(allowed.allowed)
        self.assertEqual(allowed.request_interval_ms, 2_000)
        fetch_rules.assert_awaited_with(self.redis, session, "example.com", "https")

    async def test_checker_keeps_http_and_https_robots_origins_separate(self) -> None:
        rules = RobotsRules(
            domain="example.com",
            crawl_delay_s=1,
            request_rate_s=0,
            allowed=True,
            fetched_at=123,
            text="User-agent: *\nAllow: /\n",
        )
        session = object()
        checker = FetchPathChecker(
            self.redis,  # type: ignore[arg-type]
            session,  # type: ignore[arg-type]
        )

        with patch(
            "eng_universe.ingest.fetch_boundary.get_or_fetch_robots",
            new=AsyncMock(return_value=rules),
        ) as fetch_rules:
            await checker.check("http://example.com/article")

        fetch_rules.assert_awaited_once_with(self.redis, session, "example.com", "http")

    async def test_denied_path_is_blocked_and_releases_origin_slot(self) -> None:
        url = "https://example.com/private/article"
        result = await self.queue.enqueue(fetch_request(url))
        session = MagicMock()
        session.get = MagicMock(
            side_effect=AssertionError("denied URLs must not be fetched")
        )
        handler = make_fetch_handler(
            self.queue,
            session,
            DenyingChecker(),  # type: ignore[arg-type]
            uploads=object(),
        )
        pool = StageWorkerPool(
            self.queue,
            {FETCH_RAW_STAGE: handler},
            concurrency=1,
            lease_ms=1_000,
            heartbeat_interval_ms=200,
        )

        before = await self.queue.server_time_ms()
        self.assertTrue(await pool.run_one(worker_id="worker-1"))
        run = await self.queue.get_run(result.run.run_id)
        self.assertIsNotNone(run)
        assert run is not None
        self.assertEqual(run.state.value, "blocked")
        session.get.assert_not_called()
        state = await self.queue.origin_state(Origin.from_url(url))
        self.assertEqual(int(state["inflight"]), 0)
        self.assertEqual(int(state["request_interval_ms"]), 2_000)
        self.assertGreaterEqual(int(state["next_allowed_ms"]), before + 2_000)


if __name__ == "__main__":
    unittest.main()
