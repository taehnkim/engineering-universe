from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

import aiohttp
import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.ingest.contracts import JsonValue
from eng_universe.ingest.queue_models import FETCH_RAW_STAGE, Origin, StageLease
from eng_universe.ingest.robots import can_fetch_path, get_or_fetch_robots, parse_domain
from eng_universe.ingest.stage_queue import StageQueue
from eng_universe.ingest.worker import BlockedStageError


@dataclass(frozen=True, slots=True)
class FetchPathDecision:
    url: str
    origin: Origin
    allowed: bool
    request_interval_ms: int
    robots_fetched_at: int


class FetchPathChecker:
    def __init__(
        self,
        redis_client: redis.Redis,
        session: aiohttp.ClientSession,
        *,
        user_agent: str | None = None,
    ) -> None:
        self.redis = redis_client
        self.session = session
        self.user_agent = user_agent or Settings.user_agent

    async def check(self, url: str) -> FetchPathDecision:
        origin = Origin.from_url(url)
        rules = await get_or_fetch_robots(
            self.redis,
            self.session,
            parse_domain(url),
            urlsplit(url).scheme.lower(),
        )
        allowed = can_fetch_path(rules.text, self.user_agent, url)
        request_interval_ms = 1000 * max(
            rules.crawl_delay_s,
            rules.request_rate_s,
        )
        return FetchPathDecision(
            url=url,
            origin=origin,
            allowed=allowed,
            request_interval_ms=request_interval_ms,
            robots_fetched_at=rules.fetched_at,
        )


class AuthorizedFetchHandler(Protocol):
    async def __call__(
        self,
        lease: StageLease,
        decision: FetchPathDecision,
        checker: FetchPathChecker,
    ) -> JsonValue:
        """
        Execute an authorized request.

        Call checker.check() for the exact URL of every redirect hop before
        sending that request.
        """


class RobotsAwareFetchHandler:
    def __init__(
        self,
        queue: StageQueue,
        checker: FetchPathChecker,
        handler: AuthorizedFetchHandler,
        *,
        max_inflight: int = 1,
    ) -> None:
        if max_inflight < 1:
            raise ValueError("max_inflight must be at least 1")
        self.queue = queue
        self.checker = checker
        self.handler = handler
        self.max_inflight = max_inflight

    async def __call__(self, lease: StageLease) -> JsonValue:
        if lease.run.stage_name != FETCH_RAW_STAGE:
            raise ValueError("robots-aware handler requires a fetch_raw lease")
        url = lease.run.input_payload.get("url")
        if not isinstance(url, str):
            raise TypeError("fetch_raw input must contain a string URL")
        decision = await self.checker.check(url)
        await self.queue.configure_origin(
            decision.origin,
            max_inflight=self.max_inflight,
            request_interval_ms=decision.request_interval_ms,
            reserve_from_now=True,
        )
        if not decision.allowed:
            raise BlockedStageError(
                error_code="robots_denied",
                message=f"robots policy denied exact path {url}",
            )
        return await self.handler(lease, decision, self.checker)
