import asyncio
import unittest
from collections.abc import Mapping
from dataclasses import dataclass, replace

import fakeredis.aioredis as fakeredis

from eng_universe.ingest.contracts import (
    JsonValue,
    StageIdentity,
    StageRequest,
    StageStatus,
)
from eng_universe.ingest.queue_models import (
    FETCH_RAW_STAGE,
    FailureKind,
    Origin,
    StageQueueKeys,
)
from eng_universe.ingest.stage_queue import LeaseLostError, StageQueue


@dataclass(frozen=True)
class QueueInput:
    """Provides semantic input for queue tests."""

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
    """Tests Redis stage queue behavior."""

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

    def test_origin_identity_normalizes_ports_and_ipv6(self) -> None:
        self.assertEqual(
            Origin.from_url("https://EXAMPLE.com:443/path"),
            Origin.from_url("https://example.com/other"),
        )
        self.assertEqual(Origin.from_url("http://[::1]:80/path").value, "http://[::1]")

    async def test_fetch_rejects_origin_that_does_not_match_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match"):
            await self.queue.enqueue(
                request_for(
                    "mismatch",
                    stage_name=FETCH_RAW_STAGE,
                    url="https://actual.example/article",
                ),
                origin=Origin.from_url("https://other.example/article"),
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

    async def test_concurrent_reclaimers_expire_one_attempt_once(self) -> None:
        await self.queue.enqueue(request_for("doc-1"), max_attempts=3)
        lease = await self.queue.claim(
            "parse_article",
            worker_id="crashed-worker",
            lease_ms=10,
        )
        self.assertIsNotNone(lease)
        await asyncio.sleep(0.02)

        reclaimed = await asyncio.gather(
            self.queue.reclaim_expired("parse_article", retry_delay_ms=0),
            self.queue.reclaim_expired("parse_article", retry_delay_ms=0),
        )

        self.assertEqual(sum(reclaimed), 1)
        recovered = await self.queue.claim(
            "parse_article",
            worker_id="recovery-worker",
        )
        self.assertIsNotNone(recovered)
        assert recovered is not None
        self.assertEqual(recovered.run.attempt_count, 2)

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

        stale_lease = replace(first, token="stale-token")
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

    async def test_fetch_claims_fill_high_origin_limit_under_contention(self) -> None:
        total = 100
        await asyncio.gather(
            *(
                self.queue.enqueue(
                    request_for(
                        str(index),
                        stage_name=FETCH_RAW_STAGE,
                        url=f"https://bulk.example/articles/{index}",
                    ),
                    origin_max_inflight=total,
                )
                for index in range(total)
            )
        )

        leases = await asyncio.gather(
            *(
                self.queue.claim(FETCH_RAW_STAGE, worker_id=f"worker-{index}")
                for index in range(total)
            )
        )

        claimed = [lease for lease in leases if lease is not None]
        self.assertEqual(len(claimed), total)
        self.assertEqual(len({lease.run.run_id for lease in claimed}), total)

    async def test_fetch_global_limit_is_shared_across_queue_clients(self) -> None:
        limit = 10
        first_queue = StageQueue(
            self.redis,  # type: ignore[arg-type]
            keys=self.queue.keys,
            fetch_global_limit=limit,
        )
        second_queue = StageQueue(
            self.redis,  # type: ignore[arg-type]
            keys=self.queue.keys,
            fetch_global_limit=limit,
        )
        await asyncio.gather(
            *(
                first_queue.enqueue(
                    request_for(
                        str(index),
                        stage_name=FETCH_RAW_STAGE,
                        url=f"https://origin-{index}.example/article",
                    )
                )
                for index in range(limit * 2)
            )
        )

        leases = await asyncio.gather(
            *(
                (first_queue if index % 2 else second_queue).claim(
                    FETCH_RAW_STAGE,
                    worker_id=f"worker-{index}",
                )
                for index in range(limit * 2)
            )
        )

        claimed = [lease for lease in leases if lease is not None]
        self.assertEqual(len(claimed), limit)
        global_state = await first_queue.fetch_global_state()
        self.assertEqual(int(global_state["inflight"]), limit)
        await asyncio.gather(*(first_queue.complete(lease) for lease in claimed))
        global_state = await first_queue.fetch_global_state()
        self.assertEqual(int(global_state["inflight"]), 0)

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


    async def test_complete_sets_ttl_on_succeeded_runs_only(self) -> None:
        from unittest.mock import patch
        from eng_universe.config import Settings

        with patch.object(Settings, "stage_succeeded_run_ttl_s", 3600):
            enqueued = await self.queue.enqueue(request_for("ttl-ok"))
            lease = await self.queue.claim(
                enqueued.run.stage_name,
                worker_id="worker-1",
            )
            self.assertIsNotNone(lease)
            assert lease is not None
            await self.queue.complete(lease, output={"ok": True})
            ttl = await self.redis.ttl(self.queue.keys.run(enqueued.run.run_id))
            self.assertGreater(ttl, 0)
            self.assertLessEqual(ttl, 3600)

        failed = await self.queue.enqueue(request_for("ttl-fail"), max_attempts=1)
        fail_lease = await self.queue.claim(
            failed.run.stage_name,
            worker_id="worker-2",
        )
        self.assertIsNotNone(fail_lease)
        assert fail_lease is not None
        await self.queue.fail(
            fail_lease,
            kind=FailureKind.PERMANENT,
            error_code="boom",
            error_message="permanent",
        )
        fail_ttl = await self.redis.ttl(self.queue.keys.run(failed.run.run_id))
        self.assertEqual(fail_ttl, -1)



if __name__ == "__main__":
    unittest.main()
