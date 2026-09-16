"""Maps Redis stage-queue keys into a queue-monitor view model."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from eng_universe.ingest.queue_models import (
    CRAWL_STAGE,
    INDEX_RAW_STAGE,
    StageQueueKeys,
    decode_hash,
)

# Status labels shown in the TUI table and flow panes.
STATUS_QUEUED = "queued"
STATUS_DELAYED = "delayed"
STATUS_IN_FLIGHT = "in_flight"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RunRow:
    """One row in the stage monitor table."""

    run_id: str
    subject: str
    status: str
    detail: str

    @property
    def domain(self) -> str:
        """Backward-compatible alias used by crawl-focused callers."""

        return self.subject

    @property
    def url(self) -> str:
        """Backward-compatible alias used by crawl-focused callers."""

        return self.detail


@dataclass(frozen=True, slots=True)
class CrawlMonitorSnapshot:
    """Immutable view of one stage queue at a sample time."""

    stage: str
    queued: int
    delayed: int
    in_flight: int
    succeeded: int
    failed: int
    blocked: int
    ready_run_ids: tuple[str, ...]
    inflight_run_ids: tuple[str, ...]
    rows: tuple[RunRow, ...]
    collected_at_ms: int

    @property
    def total(self) -> int:
        return (
            self.queued
            + self.delayed
            + self.in_flight
            + self.succeeded
            + self.failed
            + self.blocked
        )


def _text(value: object | None, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _payload(raw: str) -> Mapping[str, object]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, Mapping):
        return {}
    return payload


def _url_from_payload(payload: Mapping[str, object]) -> str:
    url = payload.get("url")
    return url if isinstance(url, str) else ""


def _doc_id_from_payload(payload: Mapping[str, object]) -> str:
    doc_id = payload.get("doc_id")
    return doc_id if isinstance(doc_id, str) else ""


def _domain_from_url(url: str, origin: str | None = None) -> str:
    if origin:
        host = urlsplit(origin).hostname
        if host:
            return host
    if not url:
        return ""
    host = urlsplit(url).hostname
    return host or ""


def row_fields_for_run(
    *,
    stage: str,
    input_json: str,
    origin: str | None = None,
) -> tuple[str, str]:
    """
    Returns (subject, detail) for one run row.

    Crawl rows show domain + URL. Index/clean rows show doc_id + clean artifact
    path, because cleaning runs inside the index_raw worker (no separate queue).
    """

    payload = _payload(input_json)
    if stage == INDEX_RAW_STAGE:
        doc_id = _doc_id_from_payload(payload)
        detail = f"clean/{doc_id}.txt" if doc_id else ""
        return doc_id, detail
    url = _url_from_payload(payload)
    return _domain_from_url(url, origin), url


def _short_id(run_id: str, width: int = 10) -> str:
    if len(run_id) <= width:
        return run_id
    return run_id[: width - 1] + "…"


def build_snapshot(
    *,
    stage: str,
    now_ms: int,
    ready: Sequence[tuple[str, float]],
    leased: Sequence[str],
    dead: Sequence[str],
    blocked: Sequence[str],
    runs: Mapping[str, Mapping[str, str]],
    row_limit: int = 40,
) -> CrawlMonitorSnapshot:
    """
    Builds a monitor snapshot from already-decoded Redis membership.

    This function is pure so unit tests can cover Redis→view mapping without I/O.
    """

    ready_ids = tuple(run_id for run_id, _ in ready)
    due_ids = tuple(run_id for run_id, score in ready if score <= now_ms)
    delayed_ids = tuple(run_id for run_id, score in ready if score > now_ms)
    leased_ids = tuple(leased)
    dead_ids = tuple(dead)
    blocked_ids = tuple(blocked)

    membership: dict[str, str] = {}
    for run_id in due_ids:
        membership[run_id] = STATUS_QUEUED
    for run_id in delayed_ids:
        membership[run_id] = STATUS_DELAYED
    for run_id in leased_ids:
        membership[run_id] = STATUS_IN_FLIGHT
    for run_id in dead_ids:
        membership[run_id] = STATUS_FAILED
    for run_id in blocked_ids:
        membership[run_id] = STATUS_BLOCKED

    succeeded_ids: list[str] = []
    for run_id, fields in runs.items():
        if fields.get("stage") and fields.get("stage") != stage:
            continue
        if fields.get("state") != STATUS_SUCCEEDED:
            continue
        if run_id in membership:
            continue
        membership[run_id] = STATUS_SUCCEEDED
        succeeded_ids.append(run_id)

    rows: list[RunRow] = []
    # Prefer active work first, then delayed, then terminal.
    ordered_ids = (
        list(leased_ids)
        + list(due_ids)
        + list(delayed_ids)
        + list(dead_ids)
        + list(blocked_ids)
        + succeeded_ids
    )
    seen: set[str] = set()
    for run_id in ordered_ids:
        if run_id in seen:
            continue
        seen.add(run_id)
        fields = runs.get(run_id, {})
        subject, detail = row_fields_for_run(
            stage=stage,
            input_json=fields.get("input_json", ""),
            origin=fields.get("origin"),
        )
        status = membership.get(run_id) or fields.get("state", "?")
        if status == "leased" or status == "running":
            status = STATUS_IN_FLIGHT
        rows.append(
            RunRow(
                run_id=run_id,
                subject=subject,
                status=status,
                detail=detail,
            )
        )
        if len(rows) >= row_limit:
            break

    return CrawlMonitorSnapshot(
        stage=stage,
        queued=len(due_ids),
        delayed=len(delayed_ids),
        in_flight=len(leased_ids),
        succeeded=len(succeeded_ids),
        failed=len(dead_ids),
        blocked=len(blocked_ids),
        ready_run_ids=ready_ids,
        inflight_run_ids=leased_ids,
        rows=tuple(rows),
        collected_at_ms=now_ms,
    )


async def collect_snapshot(
    redis_client: Any,
    *,
    keys: StageQueueKeys | None = None,
    stage: str = CRAWL_STAGE,
    now_ms: int | None = None,
    row_limit: int = 40,
) -> CrawlMonitorSnapshot:
    """Reads eu:v1 stage-queue keys and builds a monitor snapshot."""

    queue_keys = keys or StageQueueKeys()
    sample_ms = now_ms if now_ms is not None else int(time.time() * 1000)

    ready_raw = await redis_client.zrange(
        queue_keys.ready(stage), 0, -1, withscores=True
    )
    leased_raw = await redis_client.zrange(queue_keys.leased(stage), 0, -1)
    dead_raw = await redis_client.zrange(queue_keys.dead(stage), 0, -1)
    blocked_raw = await redis_client.zrange(queue_keys.blocked(stage), 0, -1)

    ready = [(_text(run_id), float(score)) for run_id, score in ready_raw]
    leased = [_text(run_id) for run_id in leased_raw]
    dead = [_text(run_id) for run_id in dead_raw]
    blocked = [_text(run_id) for run_id in blocked_raw]

    interest = {run_id for run_id, _ in ready} | set(leased) | set(dead) | set(blocked)
    runs: dict[str, dict[str, str]] = {}

    # Load queued/active members first so the table stays accurate under load.
    for run_id in interest:
        values = await redis_client.hgetall(queue_keys.run(run_id))
        if values:
            runs[run_id] = decode_hash(values)

    # Scan remaining run hashes for succeeded (and any stage peers).
    async for raw_key in redis_client.scan_iter(
        match=f"{queue_keys.namespace}:run:*",
        count=200,
    ):
        key = _text(raw_key)
        run_id = key.rsplit(":", 1)[-1]
        if run_id in runs:
            continue
        values = await redis_client.hgetall(key)
        if not values:
            continue
        decoded = decode_hash(values)
        if decoded.get("stage") != stage:
            continue
        runs[run_id] = decoded

    return build_snapshot(
        stage=stage,
        now_ms=sample_ms,
        ready=ready,
        leased=leased,
        dead=dead,
        blocked=blocked,
        runs=runs,
        row_limit=row_limit,
    )


def format_flow_boxes(
    ready_ids: Sequence[str],
    inflight_ids: Sequence[str],
    *,
    max_boxes: int = 12,
    id_width: int = 10,
) -> str:
    """Renders ready → in-flight ASCII boxes for the middle pane."""

    def boxes(ids: Sequence[str], empty_label: str) -> str:
        shown = list(ids)[:max_boxes]
        if not shown:
            return f"[ {empty_label} ]"
        parts = [f"[ {_short_id(run_id, id_width)} ]" for run_id in shown]
        extra = len(ids) - len(shown)
        if extra > 0:
            parts.append(f"[ +{extra} ]")
        return " ".join(parts)

    ready_line = boxes(ready_ids, "empty")
    flight_line = boxes(inflight_ids, "idle")
    return (
        f"READY     {ready_line}\n"
        f"            ──────────►\n"
        f"IN-FLIGHT {flight_line}"
    )
