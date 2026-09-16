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
    _text,
    decode_hash,
)

# Status labels shown in the TUI table and flow panes.
STATUS_QUEUED = "queued"
STATUS_DELAYED = "delayed"
STATUS_IN_FLIGHT = "in_flight"
STATUS_FAILED = "failed"
STATUS_BLOCKED = "blocked"

FLOW_BOX_LIMIT = 12


@dataclass(frozen=True, slots=True)
class RunRow:
    """One row in the stage monitor table."""

    run_id: str
    subject: str
    status: str
    detail: str


@dataclass(frozen=True, slots=True)
class CrawlMonitorSnapshot:
    """Immutable view of one stage queue at a sample time."""

    queued: int
    delayed: int
    in_flight: int
    failed: int
    blocked: int
    ready_run_ids: tuple[str, ...]
    inflight_run_ids: tuple[str, ...]
    rows: tuple[RunRow, ...]


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
    queued: int,
    delayed: int,
    in_flight: int,
    failed: int,
    blocked: int,
    ready_sample: Sequence[tuple[str, float]],
    leased: Sequence[str],
    dead: Sequence[str],
    blocked_ids: Sequence[str],
    runs: Mapping[str, Mapping[str, str]],
    row_limit: int = 40,
) -> CrawlMonitorSnapshot:
    """
    Builds a monitor snapshot from decoded Redis membership.

    Counts are authoritative (from ZCOUNT/ZCARD). Ready/leased/dead/blocked ID
    lists are display samples only. Succeeded runs are not scanned — that would
    require walking every eu:v1:run:* hash.
    """

    due_ids = tuple(run_id for run_id, score in ready_sample if score <= now_ms)
    delayed_sample = tuple(
        run_id for run_id, score in ready_sample if score > now_ms
    )
    leased_ids = tuple(leased)
    dead_ids = tuple(dead)
    blocked_sample = tuple(blocked_ids)
    ready_run_ids = tuple(run_id for run_id, _ in ready_sample)

    membership: dict[str, str] = {}
    for run_id in due_ids:
        membership[run_id] = STATUS_QUEUED
    for run_id in delayed_sample:
        membership[run_id] = STATUS_DELAYED
    for run_id in leased_ids:
        membership[run_id] = STATUS_IN_FLIGHT
    for run_id in dead_ids:
        membership[run_id] = STATUS_FAILED
    for run_id in blocked_sample:
        membership[run_id] = STATUS_BLOCKED

    rows: list[RunRow] = []
    # Prefer active work first, then delayed, then terminal samples.
    ordered_ids = (
        list(leased_ids)
        + list(due_ids)
        + list(delayed_sample)
        + list(dead_ids)
        + list(blocked_sample)
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
        rows.append(
            RunRow(
                run_id=run_id,
                subject=subject,
                status=membership[run_id],
                detail=detail,
            )
        )
        if len(rows) >= row_limit:
            break

    return CrawlMonitorSnapshot(
        queued=queued,
        delayed=delayed,
        in_flight=in_flight,
        failed=failed,
        blocked=blocked,
        ready_run_ids=ready_run_ids[:FLOW_BOX_LIMIT],
        inflight_run_ids=leased_ids[:FLOW_BOX_LIMIT],
        rows=tuple(rows),
    )


async def collect_snapshot(
    redis_client: Any,
    *,
    keys: StageQueueKeys | None = None,
    stage: str = CRAWL_STAGE,
    now_ms: int | None = None,
    row_limit: int = 40,
) -> CrawlMonitorSnapshot:
    """
    Reads eu:v1 stage-queue keys and builds a monitor snapshot.

    Uses ZCOUNT/ZCARD for totals and bounded ZRANGE samples for the table and
    flow panes. Does not SCAN run hashes for succeeded counts.
    """

    queue_keys = keys or StageQueueKeys()
    sample_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    ready_key = queue_keys.ready(stage)
    leased_key = queue_keys.leased(stage)
    dead_key = queue_keys.dead(stage)
    blocked_key = queue_keys.blocked(stage)
    sample_end = max(row_limit, FLOW_BOX_LIMIT) - 1

    pipe = redis_client.pipeline()
    pipe.zcount(ready_key, "-inf", sample_ms)
    pipe.zcard(ready_key)
    pipe.zcard(leased_key)
    pipe.zcard(dead_key)
    pipe.zcard(blocked_key)
    pipe.zrangebyscore(
        ready_key, "-inf", sample_ms, start=0, num=sample_end + 1, withscores=True
    )
    pipe.zrangebyscore(
        ready_key,
        f"({sample_ms}",
        "+inf",
        start=0,
        num=sample_end + 1,
        withscores=True,
    )
    pipe.zrange(leased_key, 0, sample_end)
    pipe.zrange(dead_key, 0, sample_end)
    pipe.zrange(blocked_key, 0, sample_end)
    (
        queued,
        ready_total,
        in_flight,
        failed,
        blocked,
        due_raw,
        delayed_raw,
        leased_raw,
        dead_raw,
        blocked_raw,
    ) = await pipe.execute()

    due_pairs = [(_text(run_id), float(score)) for run_id, score in due_raw]
    delayed_pairs = [(_text(run_id), float(score)) for run_id, score in delayed_raw]
    ready_sample = due_pairs + delayed_pairs
    leased = [_text(run_id) for run_id in leased_raw]
    dead = [_text(run_id) for run_id in dead_raw]
    blocked_ids = [_text(run_id) for run_id in blocked_raw]
    delayed = max(int(ready_total) - int(queued), 0)

    # Only hydrate hashes for the bounded display sample.
    interest = (
        [run_id for run_id, _ in ready_sample] + leased + dead + blocked_ids
    )[: row_limit * 2]
    runs: dict[str, dict[str, str]] = {}
    if interest:
        hash_pipe = redis_client.pipeline()
        for run_id in interest:
            hash_pipe.hgetall(queue_keys.run(run_id))
        for run_id, values in zip(interest, await hash_pipe.execute(), strict=True):
            if values:
                runs[run_id] = decode_hash(values)

    return build_snapshot(
        stage=stage,
        now_ms=sample_ms,
        queued=int(queued),
        delayed=delayed,
        in_flight=int(in_flight),
        failed=int(failed),
        blocked=int(blocked),
        ready_sample=ready_sample,
        leased=leased,
        dead=dead,
        blocked_ids=blocked_ids,
        runs=runs,
        row_limit=row_limit,
    )


def format_flow_boxes(
    ready_ids: Sequence[str],
    inflight_ids: Sequence[str],
    *,
    max_boxes: int = FLOW_BOX_LIMIT,
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
