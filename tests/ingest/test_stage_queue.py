import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
import unittest

import fakeredis.aioredis as fakeredis

from eng_universe.ingest.contracts import (
    ExecutionPolicy,
    JsonValue,
    StageIdentity,
    StageRequest,
    StageStatus,
)
from eng_universe.ingest.queue_models import (
    FailureKind,
    FETCH_RAW_STAGE,
    Origin,
    StageQueueKeys,
)
from eng_universe.ingest.stage_queue import LeaseLostError, StageQueue


@dataclass(frozen=True)
class QueueInput:
    values: Mapping[str, JsonValue]

    def idempotency_payload(self) -> Mapping[str, JsonValue]:
        return self.values


def request_for(
    identifier: str,
    *,
    stage_name: str = "parse_article",
    url: str | None = None,
) -> StageRequest[QueueInput]:
    values: dict[str, JsonValue] = {"id": identifier}
    if url is not None:
        values["url"] = url
    return StageRequest(
        identity=StageIdentity(name=stage_name, version="1.0.0"),
        stage_input=QueueInput(values),
        config_version="sources-1",
    )


class StageQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.redis = fakeredis.FakeRedis()
        self.queue = StageQueue(
            self.redis,  # type: ignore[arg-type]
            keys=StageQueueKeys(namespace="eu:test:v1"),
            retry_base_ms=0,
            retry_max_ms=0,
        )

    async def asyncTearDown(self) -> None:
        await self.redis.aclose()

    async def test_versioned_keys_and_record_codec(self) -> None:
        result = await self.queue.enqueue(request_for("doc-1"))

        self.assertTrue(result.created)
        self.assertEqual(result.run.schema_version, "1")
        self.assertEqual(result.run.state, StageStatus.QUEUED)
        self.assertEqual(result.run.input_payload, {"id": "doc-1"})
        self.assertTrue(
            self.queue.keys.run(result.run.run_id).startswith("eu:test:v1:run:")
        )

    async def test_duplicate_suppression_is_atomic_under_concurrency(self) -> None:
        request = request_for("same")
        results = await asyncio.gather(
            *(self.queue.enqueue(request) for _ in range(100))
        )

        self.assertEqual(sum(result.created for result in results), 1)
        self.assertEqual(len({result.run.run_id for result in results}), 1)
        counts = await self.queue.counts("parse_article")
        self.assertEqual(counts.ready, 1)

    async def test_forced_execution_uses_separate_idempotency_identity(self) -> None:
        request = request_for("doc-1")
        normal = await self.queue.enqueue(request)
        forced = await self.queue.enqueue(
            request,
            policy=ExecutionPolicy.forced("manual-1"),
        )

        self.assertNotEqual(normal.run.run_id, forced.run.run_id)
        self.assertFalse(forced.run.promote)

    async def test_concurrent_claims_have_no_duplicates_or_loss(self) -> None:
        total = 250
        runs = await asyncio.gather(
            *(self.queue.enqueue(request_for(str(index))) for index in range(total))
        )
        expected_ids = {result.run.run_id for result in runs}

        leases = await asyncio.gather(
            *(
                self.queue.claim("parse_article", worker_id=f"worker-{index}")
                for index in range(total * 2)
            )
        )
        claimed = [lease for lease in leases if lease is not None]

        self.assertEqual(len(claimed), total)
        self.assertEqual({lease.run.run_id for lease in claimed}, expected_ids)
        self.assertEqual(len({lease.token for lease in claimed}), total)

        await asyncio.gather(
            *(self.queue.complete(lease, output={"ok": True}) for lease in claimed)
        )
        counts = await self.queue.counts("parse_article")
        self.assertEqual(counts.ready, 0)
        self.assertEqual(counts.leased, 0)

    async def test_retry_preserves_run_and_increments_attempt(self) -> None:
        enqueued = await self.queue.enqueue(request_for("doc-1"), max_attempts=3)
        first = await self.queue.claim("parse_article", worker_id="worker-1")
        self.assertIsNotNone(first)
        assert first is not None

        failed = await self.queue.fail(
            first,
            kind=FailureKind.RETRYABLE,
            error_code="timeout",
            error_message="request timed out",
            retry_delay_ms=0,
        )
        self.assertEqual(failed.state, StageStatus.RETRY_WAIT)
        self.assertEqual(failed.run_id, enqueued.run.run_id)

        second = await self.queue.claim("parse_article", worker_id="worker-2")
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.run.attempt_count, 2)
        await self.queue.complete(second)

    async def test_expired_lease_is_reclaimed_after_crash(self) -> None:
        await self.queue.enqueue(request_for("doc-1"), max_attempts=3)
        stale = await self.queue.claim(
            "parse_article",
            worker_id="crashed-worker",
            lease_ms=10,
        )
        self.assertIsNotNone(stale)
        assert stale is not None
        await asyncio.sleep(0.02)

        reclaimed = await self.queue.reclaim_expired(
            "parse_article",
            retry_delay_ms=0,
        )
        self.assertEqual(reclaimed, 1)
        recovered = await self.queue.claim(
            "parse_article",
            worker_id="recovery-worker",
        )
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered.run.attempt_count, 2)

        with self.assertRaises(LeaseLostError):
            await self.queue.complete(stale)
        completed = await self.queue.complete(recovered)
        self.assertEqual(completed.state, StageStatus.SUCCEEDED)

    async def test_permanent_and_blocked_failures_use_terminal_queues(self) -> None:
        await self.queue.enqueue(request_for("permanent"))
        permanent = await self.queue.claim("parse_article", worker_id="worker-1")
        self.assertIsNotNone(permanent)
        assert permanent is not None
        await self.queue.fail(
            permanent,
            kind=FailureKind.PERMANENT,
            error_code="invalid",
            error_message="invalid input",
        )

        await self.queue.enqueue(request_for("blocked"))
        blocked = await self.queue.claim("parse_article", worker_id="worker-2")
        self.assertIsNotNone(blocked)
        assert blocked is not None
        await self.queue.fail(
            blocked,
            kind=FailureKind.BLOCKED,
            error_code="policy",
            error_message="policy denied",
        )

        counts = await self.queue.counts("parse_article")
        self.assertEqual(counts.dead, 1)
        self.assertEqual(counts.blocked, 1)

    async def test_fetch_origin_max_inflight_and_token_safe_release(self) -> None:
        url = "https://example.com/articles/one"
        origin = Origin.from_url(url)
        for index in range(3):
            await self.queue.enqueue(
                request_for(
                    str(index),
                    stage_name=FETCH_RAW_STAGE,
                    url=f"https://example.com/articles/{index}",
                ),
                origin_max_inflight=2,
            )

        first = await self.queue.claim(FETCH_RAW_STAGE, worker_id="worker-1")
        second = await self.queue.claim(FETCH_RAW_STAGE, worker_id="worker-2")
        unavailable = await self.queue.claim(FETCH_RAW_STAGE, worker_id="worker-3")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertIsNone(unavailable)
        assert first is not None
        assert second is not None

        stale_attempt = replace(first.attempt, lease_token="stale-token")
        stale_lease = replace(first, attempt=stale_attempt)
        with self.assertRaises(LeaseLostError):
            await self.queue.complete(stale_lease)
        state = await self.queue.origin_state(origin)
        self.assertEqual(int(state["inflight"]), 2)

        await self.queue.complete(first)
        third = await self.queue.claim(FETCH_RAW_STAGE, worker_id="worker-3")
        self.assertIsNotNone(third)
        await self.queue.complete(second)
        assert third is not None
        await self.queue.complete(third)
        state = await self.queue.origin_state(origin)
        self.assertEqual(int(state["inflight"]), 0)

    async def test_fetch_claims_rotate_across_ready_origins(self) -> None:
        for index in range(5):
            await self.queue.enqueue(
                request_for(
                    f"a-{index}",
                    stage_name=FETCH_RAW_STAGE,
                    url=f"https://a.example/articles/{index}",
                ),
                origin_max_inflight=5,
            )
        await self.queue.enqueue(
            request_for(
                "b-0",
                stage_name=FETCH_RAW_STAGE,
                url="https://b.example/articles/0",
            ),
            origin_max_inflight=5,
        )

        first = await self.queue.claim(FETCH_RAW_STAGE, worker_id="worker-1")
        second = await self.queue.claim(FETCH_RAW_STAGE, worker_id="worker-2")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        assert first is not None
        assert second is not None
        self.assertNotEqual(first.run.origin_id, second.run.origin_id)

    async def test_origin_backoff_does_not_block_other_origins(self) -> None:
        await self.queue.enqueue(
            request_for(
                "a-1",
                stage_name=FETCH_RAW_STAGE,
                url="https://a.example/one",
            ),
        )
        await self.queue.enqueue(
            request_for(
                "a-2",
                stage_name=FETCH_RAW_STAGE,
                url="https://a.example/two",
            ),
        )
        await self.queue.enqueue(
            request_for(
                "b-1",
                stage_name=FETCH_RAW_STAGE,
                url="https://b.example/one",
            ),
        )

        first = await self.queue.claim(FETCH_RAW_STAGE, worker_id="worker-1")
        self.assertIsNotNone(first)
        assert first is not None
        await self.queue.fail(
            first,
            kind=FailureKind.RETRYABLE,
            error_code="rate_limited",
            error_message="upstream returned 429",
            retry_delay_ms=0,
            origin_backoff_ms=100,
        )

        next_lease = await self.queue.claim(FETCH_RAW_STAGE, worker_id="worker-2")
        self.assertIsNotNone(next_lease)
        assert next_lease is not None
        self.assertNotEqual(next_lease.run.origin_id, first.run.origin_id)

    async def test_queue_holds_more_than_ten_thousand_ready_runs(self) -> None:
        total = 10_001
        batch_size = 200
        for start in range(0, total, batch_size):
            await asyncio.gather(
                *(
                    self.queue.enqueue(request_for(str(index)))
                    for index in range(start, min(start + batch_size, total))
                )
            )

        counts = await self.queue.counts("parse_article")
        self.assertEqual(counts.ready, total)


if __name__ == "__main__":
    unittest.main()
