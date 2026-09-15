from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections.abc import Mapping, Sequence
from typing import Any

import redis.asyncio as redis
from redis.exceptions import NoScriptError

from eng_universe.ingest.contracts import (
    ExecutionPolicy,
    JsonValue,
    StageInput,
    StageRequest,
    canonical_json,
    make_run_idempotency_key,
)
from eng_universe.ingest.queue_models import (
    FETCH_RAW_STAGE,
    QUEUE_SCHEMA_VERSION,
    AttemptRecord,
    EnqueueResult,
    FailureKind,
    Origin,
    QueueCounts,
    StageLease,
    StageQueueKeys,
    StageRunRecord,
    decode_hash,
    new_lease_token,
    new_run_id,
)
from eng_universe.ingest.queue_scripts import (
    CLAIM_FETCH_RUN,
    CLAIM_RUN,
    COMPLETE_RUN,
    CONFIGURE_ORIGIN,
    ENQUEUE_RUN,
    FAIL_RUN,
    HEARTBEAT_RUN,
    RECLAIM_RUN,
)


class LeaseLostError(RuntimeError):
    pass


class _LuaScript:
    def __init__(self, source: str) -> None:
        self.source = source
        self.sha = hashlib.sha1(source.encode("utf-8")).hexdigest()
        self._load_lock = asyncio.Lock()

    async def __call__(
        self,
        client: redis.Redis,
        *,
        keys: Sequence[str],
        args: Sequence[object],
    ) -> Any:
        values = [*keys, *args]
        try:
            return await client.evalsha(self.sha, len(keys), *values)
        except NoScriptError:
            async with self._load_lock:
                loaded_sha = await client.script_load(self.source)
                if isinstance(loaded_sha, bytes):
                    loaded_sha = loaded_sha.decode("ascii")
                self.sha = str(loaded_sha)
            return await client.evalsha(self.sha, len(keys), *values)


_ENQUEUE = _LuaScript(ENQUEUE_RUN)
_CLAIM = _LuaScript(CLAIM_RUN)
_CLAIM_FETCH = _LuaScript(CLAIM_FETCH_RUN)
_HEARTBEAT = _LuaScript(HEARTBEAT_RUN)
_COMPLETE = _LuaScript(COMPLETE_RUN)
_FAIL = _LuaScript(FAIL_RUN)
_RECLAIM = _LuaScript(RECLAIM_RUN)
_CONFIGURE_ORIGIN = _LuaScript(CONFIGURE_ORIGIN)


def _decoded(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _first_integer(response: object) -> int:
    if not isinstance(response, (list, tuple)) or not response:
        return 0
    return int(response[0])


class StageQueue:
    def __init__(
        self,
        redis_client: redis.Redis,
        *,
        keys: StageQueueKeys | None = None,
        default_lease_ms: int = 30_000,
        claim_scan_limit: int = 64,
        claim_contention_retries: int = 4,
        origin_scan_limit: int = 64,
        origin_busy_delay_ms: int = 50,
        retry_base_ms: int = 1_000,
        retry_max_ms: int = 300_000,
    ) -> None:
        if default_lease_ms < 1:
            raise ValueError("default_lease_ms must be positive")
        if claim_scan_limit < 1 or origin_scan_limit < 1:
            raise ValueError("claim scan limits must be positive")
        if claim_contention_retries < 1:
            raise ValueError("claim_contention_retries must be positive")
        if origin_busy_delay_ms < 1:
            raise ValueError("origin_busy_delay_ms must be positive")
        if retry_base_ms < 0 or retry_max_ms < retry_base_ms:
            raise ValueError("retry delay bounds are invalid")
        self.redis = redis_client
        self.keys = keys or StageQueueKeys()
        self.default_lease_ms = default_lease_ms
        self.claim_scan_limit = claim_scan_limit
        self.claim_contention_retries = claim_contention_retries
        self.origin_scan_limit = origin_scan_limit
        self.origin_busy_delay_ms = origin_busy_delay_ms
        self.retry_base_ms = retry_base_ms
        self.retry_max_ms = retry_max_ms

    async def server_time_ms(self) -> int:
        seconds, microseconds = await self.redis.time()
        return (int(seconds) * 1000) + (int(microseconds) // 1000)

    async def enqueue(
        self,
        request: StageRequest[StageInput],
        *,
        policy: ExecutionPolicy | None = None,
        run_id: str | None = None,
        due_at_ms: int = 0,
        max_attempts: int = 5,
        origin: Origin | None = None,
        origin_max_inflight: int = 1,
        origin_interval_ms: int = 0,
    ) -> EnqueueResult:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if due_at_ms < 0:
            raise ValueError("due_at_ms must not be negative")
        if origin_max_inflight < 1:
            raise ValueError("origin_max_inflight must be at least 1")
        if origin_interval_ms < 0:
            raise ValueError("origin_interval_ms must not be negative")

        execution_policy = policy or ExecutionPolicy()
        semantic_key = request.idempotency_key()
        execution_key = make_run_idempotency_key(semantic_key, execution_policy)
        payload = request.stage_input.idempotency_payload()
        input_json = canonical_json(payload)
        selected_origin = self._origin_for_request(request, origin, payload)
        selected_run_id = run_id or new_run_id()

        origin_id = selected_origin.origin_id if selected_origin else ""
        origin_value = selected_origin.value if selected_origin else ""
        origin_queue = (
            self.keys.origin_ready(origin_id) if selected_origin else self.keys.unused
        )
        origin_state = (
            self.keys.origin_state(origin_id) if selected_origin else self.keys.unused
        )
        response = await _ENQUEUE(
            self.redis,
            keys=[
                self.keys.idempotency(execution_key),
                self.keys.run(selected_run_id),
                self.keys.ready(request.identity.name),
                self.keys.fetch_origins,
                origin_queue,
                origin_state,
            ],
            args=[
                QUEUE_SCHEMA_VERSION,
                selected_run_id,
                request.identity.name,
                request.identity.version,
                semantic_key,
                execution_key,
                request.config_version,
                input_json,
                due_at_ms,
                max_attempts,
                int(execution_policy.promote),
                origin_id,
                origin_value,
                origin_max_inflight,
                origin_interval_ms,
                int(execution_policy.force),
                execution_policy.rerun_nonce or "",
            ],
        )
        created = _first_integer(response) == 1
        actual_run_id = _decoded(response[1])
        run = await self.get_run(actual_run_id)
        if run is None:
            raise RuntimeError("enqueue completed without a stage run record")
        return EnqueueResult(run=run, created=created)

    def _origin_for_request(
        self,
        request: StageRequest[StageInput],
        origin: Origin | None,
        payload: Mapping[str, JsonValue],
    ) -> Origin | None:
        if request.identity.name != FETCH_RAW_STAGE:
            if origin is not None:
                raise ValueError("only fetch_raw runs can use origin scheduling")
            return None
        if origin is not None:
            return origin
        url = payload.get("url")
        if not isinstance(url, str):
            raise TypeError("fetch_raw stage input must contain a string URL")
        return Origin.from_url(url)

    async def get_run(self, run_id: str) -> StageRunRecord | None:
        values = await self.redis.hgetall(self.keys.run(run_id))
        if not values:
            return None
        return StageRunRecord.from_redis(values)

    async def get_attempt(self, attempt_id: str) -> AttemptRecord | None:
        values = await self.redis.hgetall(self.keys.attempt(attempt_id))
        if not values:
            return None
        return AttemptRecord.from_redis(values)

    async def claim(
        self,
        stage_name: str,
        *,
        worker_id: str,
        lease_ms: int | None = None,
    ) -> StageLease | None:
        if not worker_id:
            raise ValueError("worker_id must not be empty")
        duration = lease_ms or self.default_lease_ms
        if duration < 1:
            raise ValueError("lease_ms must be positive")
        if stage_name == FETCH_RAW_STAGE:
            return await self._claim_fetch(worker_id=worker_id, lease_ms=duration)
        return await self._claim_general(
            stage_name=stage_name,
            worker_id=worker_id,
            lease_ms=duration,
        )

    async def _claim_general(
        self,
        *,
        stage_name: str,
        worker_id: str,
        lease_ms: int,
    ) -> StageLease | None:
        for _ in range(self.claim_contention_retries):
            now = await self.server_time_ms()
            run_ids = await self.redis.zrangebyscore(
                self.keys.ready(stage_name),
                "-inf",
                now,
                start=0,
                num=self.claim_scan_limit,
            )
            if not run_ids:
                return None
            for raw_run_id in self._rotated(run_ids):
                run_id = _decoded(raw_run_id)
                lease = await self._claim_candidate(
                    stage_name=stage_name,
                    run_id=run_id,
                    worker_id=worker_id,
                    lease_ms=lease_ms,
                )
                if lease is not None:
                    return lease
        return None

    async def _claim_candidate(
        self,
        *,
        stage_name: str,
        run_id: str,
        worker_id: str,
        lease_ms: int,
    ) -> StageLease | None:
        token = new_lease_token()
        attempt_id = f"a_{token}"
        response = await _CLAIM(
            self.redis,
            keys=[
                self.keys.ready(stage_name),
                self.keys.leased(stage_name),
                self.keys.run(run_id),
                self.keys.attempt(attempt_id),
            ],
            args=[
                run_id,
                worker_id,
                token,
                lease_ms,
                attempt_id,
                QUEUE_SCHEMA_VERSION,
            ],
        )
        if _first_integer(response) == 0:
            return None
        return await self._load_lease(run_id, attempt_id)

    async def _claim_fetch(
        self,
        *,
        worker_id: str,
        lease_ms: int,
    ) -> StageLease | None:
        for _ in range(self.claim_contention_retries):
            now = await self.server_time_ms()
            origins = await self.redis.zrangebyscore(
                self.keys.fetch_origins,
                "-inf",
                now,
                start=0,
                num=self.origin_scan_limit,
            )
            if not origins:
                return None
            for raw_origin_id in self._rotated(origins):
                origin_id = _decoded(raw_origin_id)
                origin_queue = self.keys.origin_ready(origin_id)
                run_ids = await self.redis.zrangebyscore(
                    origin_queue,
                    "-inf",
                    now,
                    start=0,
                    num=1,
                )
                if not run_ids:
                    await self.redis.zrem(self.keys.fetch_origins, origin_id)
                    continue
                run_id = _decoded(run_ids[0])
                token = new_lease_token()
                attempt_id = f"a_{token}"
                response = await _CLAIM_FETCH(
                    self.redis,
                    keys=[
                        self.keys.fetch_origins,
                        origin_queue,
                        self.keys.ready(FETCH_RAW_STAGE),
                        self.keys.leased(FETCH_RAW_STAGE),
                        self.keys.run(run_id),
                        self.keys.attempt(attempt_id),
                        self.keys.origin_state(origin_id),
                    ],
                    args=[
                        origin_id,
                        run_id,
                        worker_id,
                        token,
                        lease_ms,
                        attempt_id,
                        QUEUE_SCHEMA_VERSION,
                        self.origin_busy_delay_ms,
                    ],
                )
                if _first_integer(response) != 0:
                    return await self._load_lease(run_id, attempt_id)
        return None

    @staticmethod
    def _rotated(values: Sequence[object]) -> Sequence[object]:
        if len(values) < 2:
            return values
        offset = secrets.randbelow(len(values))
        return [*values[offset:], *values[:offset]]

    async def _load_lease(self, run_id: str, attempt_id: str) -> StageLease:
        pipe = self.redis.pipeline(transaction=False)
        pipe.hgetall(self.keys.run(run_id))
        pipe.hgetall(self.keys.attempt(attempt_id))
        run_values, attempt_values = await pipe.execute()
        if not run_values or not attempt_values:
            raise RuntimeError("claim completed without run and attempt records")
        return StageLease(
            run=StageRunRecord.from_redis(run_values),
            attempt=AttemptRecord.from_redis(attempt_values),
        )

    async def heartbeat(
        self,
        lease: StageLease,
        *,
        lease_ms: int | None = None,
    ) -> bool:
        duration = lease_ms or self.default_lease_ms
        response = await _HEARTBEAT(
            self.redis,
            keys=[
                self.keys.run(lease.run.run_id),
                self.keys.leased(lease.run.stage_name),
                self.keys.attempt(lease.attempt.attempt_id),
            ],
            args=[
                lease.run.run_id,
                lease.worker_id,
                lease.token,
                duration,
            ],
        )
        return _first_integer(response) == 1

    async def complete(
        self,
        lease: StageLease,
        *,
        output: JsonValue = None,
    ) -> StageRunRecord:
        origin_id = lease.run.origin_id or ""
        response = await _COMPLETE(
            self.redis,
            keys=[
                self.keys.leased(lease.run.stage_name),
                self.keys.run(lease.run.run_id),
                self.keys.attempt(lease.attempt.attempt_id),
                self.keys.fetch_origins if origin_id else self.keys.unused,
                self.keys.origin_ready(origin_id) if origin_id else self.keys.unused,
                self.keys.origin_state(origin_id) if origin_id else self.keys.unused,
            ],
            args=[
                lease.run.run_id,
                lease.worker_id,
                lease.token,
                canonical_json(output),
                origin_id,
            ],
        )
        if _first_integer(response) != 1:
            raise LeaseLostError(f"lease for run {lease.run.run_id} is no longer valid")
        run = await self.get_run(lease.run.run_id)
        if run is None:
            raise RuntimeError("completed stage run disappeared")
        return run

    async def fail(
        self,
        lease: StageLease,
        *,
        kind: FailureKind,
        error_code: str,
        error_message: str,
        retry_delay_ms: int | None = None,
        origin_backoff_ms: int = 0,
    ) -> StageRunRecord:
        if origin_backoff_ms < 0:
            raise ValueError("origin_backoff_ms must not be negative")
        delay = (
            self._retry_delay(lease.run.attempt_count)
            if retry_delay_ms is None
            else retry_delay_ms
        )
        if delay < 0:
            raise ValueError("retry_delay_ms must not be negative")
        origin_id = lease.run.origin_id or ""
        response = await _FAIL(
            self.redis,
            keys=[
                self.keys.leased(lease.run.stage_name),
                self.keys.run(lease.run.run_id),
                self.keys.attempt(lease.attempt.attempt_id),
                self.keys.ready(lease.run.stage_name),
                self.keys.dead(lease.run.stage_name),
                self.keys.blocked(lease.run.stage_name),
                self.keys.fetch_origins if origin_id else self.keys.unused,
                self.keys.origin_ready(origin_id) if origin_id else self.keys.unused,
                self.keys.origin_state(origin_id) if origin_id else self.keys.unused,
            ],
            args=[
                lease.run.run_id,
                lease.worker_id,
                lease.token,
                kind.value,
                error_code[:128],
                error_message[:2048],
                delay,
                QUEUE_SCHEMA_VERSION,
                lease.attempt.attempt_id,
                origin_id,
                origin_backoff_ms,
            ],
        )
        if _first_integer(response) != 1:
            raise LeaseLostError(f"lease for run {lease.run.run_id} is no longer valid")
        run = await self.get_run(lease.run.run_id)
        if run is None:
            raise RuntimeError("failed stage run disappeared")
        return run

    def _retry_delay(self, attempt_count: int) -> int:
        if self.retry_max_ms == 0:
            return 0
        exponent = max(0, attempt_count - 1)
        cap = min(self.retry_max_ms, self.retry_base_ms * (2**exponent))
        return secrets.randbelow(cap + 1) if cap else 0

    async def reclaim_expired(
        self,
        stage_name: str,
        *,
        limit: int = 100,
        retry_delay_ms: int = 0,
    ) -> int:
        if limit < 1 or limit > 100:
            raise ValueError("reclaim limit must be between 1 and 100")
        if retry_delay_ms < 0:
            raise ValueError("retry_delay_ms must not be negative")
        now = await self.server_time_ms()
        run_ids = await self.redis.zrangebyscore(
            self.keys.leased(stage_name),
            "-inf",
            now,
            start=0,
            num=limit,
        )
        reclaimed = 0
        for raw_run_id in run_ids:
            run_id = _decoded(raw_run_id)
            run = await self.get_run(run_id)
            if run is None or not run.attempt_id:
                await self.redis.zrem(self.keys.leased(stage_name), run_id)
                continue
            origin_id = run.origin_id or ""
            response = await _RECLAIM(
                self.redis,
                keys=[
                    self.keys.leased(stage_name),
                    self.keys.run(run_id),
                    self.keys.attempt(run.attempt_id),
                    self.keys.ready(stage_name),
                    self.keys.dead(stage_name),
                    self.keys.blocked(stage_name),
                    self.keys.fetch_origins if origin_id else self.keys.unused,
                    self.keys.origin_ready(origin_id)
                    if origin_id
                    else self.keys.unused,
                    self.keys.origin_state(origin_id)
                    if origin_id
                    else self.keys.unused,
                ],
                args=[
                    run_id,
                    run.lease_owner or "",
                    run.lease_token or "",
                    retry_delay_ms,
                    QUEUE_SCHEMA_VERSION,
                    run.attempt_id,
                    origin_id,
                ],
            )
            reclaimed += int(_first_integer(response) == 1)
        return reclaimed

    async def configure_origin(
        self,
        origin: Origin,
        *,
        max_inflight: int,
        request_interval_ms: int,
        next_allowed_ms: int | None = None,
        backoff_until_ms: int | None = None,
        reserve_from_now: bool = False,
    ) -> int:
        if max_inflight < 1:
            raise ValueError("max_inflight must be at least 1")
        if request_interval_ms < 0:
            raise ValueError("request_interval_ms must not be negative")
        response = await _CONFIGURE_ORIGIN(
            self.redis,
            keys=[
                self.keys.fetch_origins,
                self.keys.origin_ready(origin.origin_id),
                self.keys.origin_state(origin.origin_id),
            ],
            args=[
                QUEUE_SCHEMA_VERSION,
                origin.origin_id,
                origin.value,
                max_inflight,
                request_interval_ms,
                next_allowed_ms if next_allowed_ms is not None else -1,
                backoff_until_ms if backoff_until_ms is not None else -1,
                int(reserve_from_now),
            ],
        )
        return int(response[1])

    async def origin_state(self, origin: Origin) -> dict[str, str]:
        values = await self.redis.hgetall(self.keys.origin_state(origin.origin_id))
        return decode_hash(values)

    async def counts(self, stage_name: str) -> QueueCounts:
        ready, leased, dead, blocked = await asyncio.gather(
            self.redis.zcard(self.keys.ready(stage_name)),
            self.redis.zcard(self.keys.leased(stage_name)),
            self.redis.zcard(self.keys.dead(stage_name)),
            self.redis.zcard(self.keys.blocked(stage_name)),
        )
        return QueueCounts(
            ready=int(ready),
            leased=int(leased),
            dead=int(dead),
            blocked=int(blocked),
        )
