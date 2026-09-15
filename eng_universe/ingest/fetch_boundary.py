from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import aiohttp
import redis.asyncio as redis

from eng_universe.config import Settings
from eng_universe.ingest.contracts import JsonValue
from eng_universe.ingest.queue_models import FETCH_RAW_STAGE, Origin, StageLease
from eng_universe.ingest.robots import can_fetch_path, get_or_fetch_robots, parse_domain
from eng_universe.ingest.stage_queue import StageQueue
from eng_universe.ingest.queue_models import FailureKind
from eng_universe.ingest.worker import StageError, StageHandler


@dataclass(frozen=True, slots=True)
class FetchPathDecision:
    """Reports the robots decision for one exact URL."""

    url: str
    origin: Origin
    allowed: bool
    request_interval_ms: int
    robots_fetched_at: int


class FetchPathChecker:
    """Checks robots rules for each requested URL."""

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


def make_fetch_handler(
    queue: StageQueue,
    session: aiohttp.ClientSession,
    checker: FetchPathChecker | None,
    uploads: object,
) -> StageHandler:
    """Builds the fetch_raw stage handler for one shared HTTP session."""
    selected_checker = checker or FetchPathChecker(queue.redis, session)
    max_inflight = Settings.fetch_origin_max_inflight
    if max_inflight < 1:
        raise ValueError("max_inflight must be at least 1")

    async def handle(lease: StageLease) -> JsonValue:
        if lease.run.stage_name != FETCH_RAW_STAGE:
            raise ValueError("fetch handler requires a fetch_raw lease")
        url = lease.run.input_payload.get("url")
        if not isinstance(url, str):
            raise TypeError("fetch_raw input must contain a string URL")
        decision = await selected_checker.check(url)
        await queue.configure_origin(
            decision.origin,
            max_inflight=max_inflight,
            request_interval_ms=decision.request_interval_ms,
            reserve_from_now=True,
        )
        if not decision.allowed:
            raise StageError(
                kind=FailureKind.BLOCKED,
                error_code="robots_denied",
                message=f"robots policy denied exact path {url}",
            )
        async with session.get(decision.url) as response:
            body = await response.read()
            status_code = response.status
        del uploads
        return {
            "url": decision.url,
            "status_code": status_code,
            "byte_size": len(body),
        }

    return handle
