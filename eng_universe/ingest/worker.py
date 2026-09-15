from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from eng_universe.ingest.contracts import (
    ExecutionPolicy,
    JsonValue,
    Stage,
    StageContext,
    StageInput,
    canonical_json,
)
from eng_universe.ingest.queue_models import FailureKind, StageLease
from eng_universe.ingest.stage_queue import LeaseLostError, StageQueue


class StageHandler(Protocol):
    async def __call__(self, lease: StageLease) -> JsonValue:
        """
        Execute one leased stage run and return its serializable output.
        """


class ContractStageHandler:
    def __init__(
        self,
        stage: Stage[StageInput, Any],
        *,
        decode_input: Callable[[Mapping[str, JsonValue]], StageInput],
        encode_output: Callable[[Any], JsonValue],
    ) -> None:
        self.stage = stage
        self.decode_input = decode_input
        self.encode_output = encode_output

    async def __call__(self, lease: StageLease) -> JsonValue:
        if lease.run.stage_name != self.stage.identity.name:
            raise ValueError("leased stage name does not match handler identity")
        if lease.run.stage_version != self.stage.identity.version:
            raise ValueError("leased stage version does not match handler identity")
        policy = ExecutionPolicy(
            force=lease.run.force,
            promote=lease.run.promote,
            rerun_nonce=lease.run.rerun_nonce,
        )
        stage_input = self.decode_input(lease.run.input_payload)
        result = await self.stage.execute(
            stage_input,
            StageContext(
                run_id=lease.run.run_id,
                attempt=lease.run.attempt_count,
                policy=policy,
            ),
        )
        output = self.encode_output(result.output)
        canonical_json(output)
        artifacts: list[JsonValue] = [
            {
                "artifact_id": artifact.artifact_id,
                "kind": artifact.kind,
                "schema_version": artifact.schema_version,
                "content_sha256": artifact.content_sha256,
                "object_key": artifact.object_key,
                "content_type": artifact.content_type,
                "byte_size": artifact.byte_size,
            }
            for artifact in result.artifacts
        ]
        return {"output": output, "artifacts": artifacts}


@dataclass(frozen=True, slots=True)
class StageExecutionError(Exception):
    error_code: str
    message: str
    retry_delay_ms: int | None = None
    origin_backoff_ms: int = 0

    def __str__(self) -> str:
        return self.message


class RetryableStageError(StageExecutionError):
    pass


class PermanentStageError(StageExecutionError):
    pass


class BlockedStageError(StageExecutionError):
    pass


class StageWorkerPool:
    def __init__(
        self,
        queue: StageQueue,
        handlers: Mapping[str, StageHandler],
        *,
        concurrency: int,
        lease_ms: int = 30_000,
        heartbeat_interval_ms: int = 10_000,
        idle_sleep_ms: int = 100,
        reclaim_interval_ms: int = 1_000,
        reclaim_limit: int = 100,
        pool_id: str | None = None,
    ) -> None:
        if not handlers:
            raise ValueError("at least one stage handler is required")
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        if lease_ms < 1:
            raise ValueError("lease_ms must be positive")
        if heartbeat_interval_ms < 1 or heartbeat_interval_ms >= lease_ms:
            raise ValueError(
                "heartbeat interval must be positive and shorter than lease"
            )
        if idle_sleep_ms < 1 or reclaim_interval_ms < 1:
            raise ValueError("worker timing values must be positive")
        if reclaim_limit < 1 or reclaim_limit > 100:
            raise ValueError("reclaim_limit must be between 1 and 100")
        self.queue = queue
        self.handlers = dict(handlers)
        self.stage_names = tuple(self.handlers)
        self.concurrency = concurrency
        self.lease_ms = lease_ms
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.idle_sleep_ms = idle_sleep_ms
        self.reclaim_interval_ms = reclaim_interval_ms
        self.reclaim_limit = reclaim_limit
        self.pool_id = pool_id or f"pool-{uuid.uuid4().hex}"
        self._stage_cursor = 0

    async def run(self, stop_event: asyncio.Event) -> None:
        workers = [
            asyncio.create_task(
                self._worker_loop(stop_event, f"{self.pool_id}:{index}"),
                name=f"{self.pool_id}-worker-{index}",
            )
            for index in range(self.concurrency)
        ]
        reclaimer = asyncio.create_task(
            self._reclaim_loop(stop_event),
            name=f"{self.pool_id}-reclaimer",
        )
        tasks = [*workers, reclaimer]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_one(self, *, worker_id: str | None = None) -> bool:
        selected_worker = worker_id or f"{self.pool_id}:manual"
        for _ in range(len(self.stage_names)):
            stage_name = self._next_stage()
            lease = await self.queue.claim(
                stage_name,
                worker_id=selected_worker,
                lease_ms=self.lease_ms,
            )
            if lease is None:
                continue
            await self._process_lease(lease)
            return True
        return False

    def _next_stage(self) -> str:
        stage_name = self.stage_names[self._stage_cursor % len(self.stage_names)]
        self._stage_cursor += 1
        return stage_name

    async def _worker_loop(
        self,
        stop_event: asyncio.Event,
        worker_id: str,
    ) -> None:
        while not stop_event.is_set():
            worked = await self.run_one(worker_id=worker_id)
            if not worked:
                try:
                    await asyncio.wait_for(
                        stop_event.wait(),
                        timeout=self.idle_sleep_ms / 1000,
                    )
                except TimeoutError:
                    pass

    async def _reclaim_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            for stage_name in self.stage_names:
                await self.queue.reclaim_expired(
                    stage_name,
                    limit=self.reclaim_limit,
                )
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.reclaim_interval_ms / 1000,
                )
            except TimeoutError:
                pass

    async def _process_lease(self, lease: StageLease) -> None:
        handler = self.handlers[lease.run.stage_name]
        lease_lost = asyncio.Event()
        execution = asyncio.create_task(handler(lease))
        heartbeat = asyncio.create_task(self._heartbeat(lease, execution, lease_lost))
        try:
            output = await execution
            await self.queue.complete(lease, output=output)
        except RetryableStageError as exc:
            await self._fail_if_owned(
                lease,
                kind=FailureKind.RETRYABLE,
                error=exc,
            )
        except BlockedStageError as exc:
            await self._fail_if_owned(
                lease,
                kind=FailureKind.BLOCKED,
                error=exc,
            )
        except PermanentStageError as exc:
            await self._fail_if_owned(
                lease,
                kind=FailureKind.PERMANENT,
                error=exc,
            )
        except LeaseLostError:
            return
        except asyncio.CancelledError:
            if lease_lost.is_set():
                return
            raise
        except Exception as exc:  # noqa: BLE001
            error = RetryableStageError(
                error_code=type(exc).__name__,
                message=str(exc) or type(exc).__name__,
            )
            await self._fail_if_owned(
                lease,
                kind=FailureKind.RETRYABLE,
                error=error,
            )
        finally:
            if not heartbeat.done():
                heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def _heartbeat(
        self,
        lease: StageLease,
        execution: asyncio.Task[JsonValue],
        lease_lost: asyncio.Event,
    ) -> None:
        while not execution.done():
            await asyncio.sleep(self.heartbeat_interval_ms / 1000)
            if execution.done():
                return
            try:
                owned = await self.queue.heartbeat(lease, lease_ms=self.lease_ms)
            except Exception:  # noqa: BLE001
                lease_lost.set()
                execution.cancel()
                return
            if not owned:
                lease_lost.set()
                execution.cancel()
                return

    async def _fail_if_owned(
        self,
        lease: StageLease,
        *,
        kind: FailureKind,
        error: StageExecutionError,
    ) -> None:
        try:
            await self.queue.fail(
                lease,
                kind=kind,
                error_code=error.error_code,
                error_message=error.message,
                retry_delay_ms=error.retry_delay_ms,
                origin_backoff_ms=error.origin_backoff_ms,
            )
        except LeaseLostError:
            return
