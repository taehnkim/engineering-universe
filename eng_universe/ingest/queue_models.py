from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import time
from typing import Any
from urllib.parse import urlsplit
import uuid

from eng_universe.ingest.contracts import JsonValue, StageStatus


QUEUE_SCHEMA_VERSION = "1"
QUEUE_NAMESPACE = "eu:v1"
FETCH_RAW_STAGE = "fetch_raw"


class FailureKind(str, Enum):
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    BLOCKED = "blocked"


def _text(value: object | None, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _integer(value: object | None, default: int = 0) -> int:
    raw = _text(value)
    if not raw:
        return default
    return int(raw)


def _optional_integer(value: object | None) -> int | None:
    raw = _text(value)
    return int(raw) if raw else None


def _boolean(value: object | None) -> bool:
    return _text(value) == "1"


def decode_hash(values: Mapping[object, object]) -> dict[str, str]:
    return {_text(key): _text(value) for key, value in values.items()}


def new_run_id() -> str:
    return f"r_{time.time_ns():020d}_{uuid.uuid4().hex}"


def new_lease_token() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True, slots=True)
class Origin:
    origin_id: str
    value: str

    @classmethod
    def from_url(cls, url: str) -> Origin:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise ValueError("fetch URL must use http or https")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("fetch URL must not contain user information")
        if parsed.hostname is None:
            raise ValueError("fetch URL must contain a host")
        try:
            host = parsed.hostname.encode("idna").decode("ascii").lower()
            port = parsed.port
        except (UnicodeError, ValueError) as exc:
            raise ValueError("fetch URL contains an invalid host or port") from exc
        default_port = 80 if scheme == "http" else 443
        authority = host if port in {None, default_port} else f"{host}:{port}"
        value = f"{scheme}://{authority}"
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return cls(origin_id=f"o_{digest}", value=value)


@dataclass(frozen=True, slots=True)
class StageQueueKeys:
    namespace: str = QUEUE_NAMESPACE

    def __post_init__(self) -> None:
        if not self.namespace or self.namespace.endswith(":"):
            raise ValueError("queue namespace must be non-empty and omit a trailing colon")

    @property
    def events(self) -> str:
        return f"{self.namespace}:events"

    @property
    def fetch_origins(self) -> str:
        return f"{self.namespace}:q:origins:{FETCH_RAW_STAGE}"

    @property
    def unused(self) -> str:
        return f"{self.namespace}:unused"

    def run(self, run_id: str) -> str:
        return f"{self.namespace}:run:{run_id}"

    def attempt(self, attempt_id: str) -> str:
        return f"{self.namespace}:attempt:{attempt_id}"

    def idempotency(self, execution_key: str) -> str:
        digest = hashlib.sha256(execution_key.encode("utf-8")).hexdigest()
        return f"{self.namespace}:idem:{digest}"

    def ready(self, stage_name: str) -> str:
        return f"{self.namespace}:q:ready:{stage_name}"

    def leased(self, stage_name: str) -> str:
        return f"{self.namespace}:q:leased:{stage_name}"

    def dead(self, stage_name: str) -> str:
        return f"{self.namespace}:q:dead:{stage_name}"

    def blocked(self, stage_name: str) -> str:
        return f"{self.namespace}:q:blocked:{stage_name}"

    def origin_ready(self, origin_id: str) -> str:
        return f"{self.namespace}:q:ready:{FETCH_RAW_STAGE}:origin:{origin_id}"

    def origin_state(self, origin_id: str) -> str:
        return f"{self.namespace}:origin:{origin_id}"


@dataclass(frozen=True, slots=True)
class StageRunRecord:
    run_id: str
    stage_name: str
    stage_version: str
    state: StageStatus
    semantic_idempotency_key: str
    execution_idempotency_key: str
    config_version: str
    input_json: str
    promote: bool
    force: bool
    rerun_nonce: str | None
    max_attempts: int
    attempt_count: int
    due_at_ms: int
    created_at_ms: int
    origin_id: str | None = None
    origin: str | None = None
    started_at_ms: int | None = None
    finished_at_ms: int | None = None
    lease_owner: str | None = None
    lease_token: str | None = None
    lease_until_ms: int | None = None
    attempt_id: str | None = None
    output_json: str | None = None
    error_class: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    schema_version: str = QUEUE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("run_id must not be empty")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.attempt_count < 0:
            raise ValueError("attempt_count must not be negative")
        if self.stage_name == FETCH_RAW_STAGE and not self.origin_id:
            raise ValueError("fetch_raw runs require an origin")

    @property
    def input_payload(self) -> Mapping[str, JsonValue]:
        payload = json.loads(self.input_json)
        if not isinstance(payload, dict):
            raise ValueError("stage input JSON must contain an object")
        return payload

    @property
    def output_payload(self) -> JsonValue | None:
        return json.loads(self.output_json) if self.output_json else None

    def to_redis(self) -> dict[str, str | int]:
        values: dict[str, str | int] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "stage": self.stage_name,
            "stage_version": self.stage_version,
            "state": self.state.value,
            "semantic_idempotency_key": self.semantic_idempotency_key,
            "execution_idempotency_key": self.execution_idempotency_key,
            "config_version": self.config_version,
            "input_json": self.input_json,
            "promote": int(self.promote),
            "force": int(self.force),
            "max_attempts": self.max_attempts,
            "attempt_count": self.attempt_count,
            "due_at_ms": self.due_at_ms,
            "created_at_ms": self.created_at_ms,
        }
        optional: dict[str, object | None] = {
            "origin_id": self.origin_id,
            "origin": self.origin,
            "rerun_nonce": self.rerun_nonce,
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
            "lease_owner": self.lease_owner,
            "lease_token": self.lease_token,
            "lease_until_ms": self.lease_until_ms,
            "attempt_id": self.attempt_id,
            "output_json": self.output_json,
            "error_class": self.error_class,
            "error_code": self.error_code,
            "error_message": self.error_message,
        }
        values.update({key: value for key, value in optional.items() if value is not None})
        return values

    @classmethod
    def from_redis(cls, values: Mapping[object, object]) -> StageRunRecord:
        decoded = decode_hash(values)
        if not decoded:
            raise ValueError("stage run record is empty")
        return cls(
            schema_version=decoded.get("schema_version", ""),
            run_id=decoded["run_id"],
            stage_name=decoded["stage"],
            stage_version=decoded["stage_version"],
            state=StageStatus(decoded["state"]),
            semantic_idempotency_key=decoded["semantic_idempotency_key"],
            execution_idempotency_key=decoded["execution_idempotency_key"],
            config_version=decoded["config_version"],
            input_json=decoded["input_json"],
            promote=_boolean(decoded.get("promote")),
            force=_boolean(decoded.get("force")),
            rerun_nonce=decoded.get("rerun_nonce") or None,
            max_attempts=_integer(decoded.get("max_attempts")),
            attempt_count=_integer(decoded.get("attempt_count")),
            due_at_ms=_integer(decoded.get("due_at_ms")),
            created_at_ms=_integer(decoded.get("created_at_ms")),
            origin_id=decoded.get("origin_id") or None,
            origin=decoded.get("origin") or None,
            started_at_ms=_optional_integer(decoded.get("started_at_ms")),
            finished_at_ms=_optional_integer(decoded.get("finished_at_ms")),
            lease_owner=decoded.get("lease_owner") or None,
            lease_token=decoded.get("lease_token") or None,
            lease_until_ms=_optional_integer(decoded.get("lease_until_ms")),
            attempt_id=decoded.get("attempt_id") or None,
            output_json=decoded.get("output_json") or None,
            error_class=decoded.get("error_class") or None,
            error_code=decoded.get("error_code") or None,
            error_message=decoded.get("error_message") or None,
        )


@dataclass(frozen=True, slots=True)
class AttemptRecord:
    attempt_id: str
    run_id: str
    number: int
    worker_id: str
    lease_token: str
    state: str
    started_at_ms: int
    finished_at_ms: int | None = None
    error_class: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def from_redis(cls, values: Mapping[object, object]) -> AttemptRecord:
        decoded = decode_hash(values)
        if not decoded:
            raise ValueError("attempt record is empty")
        return cls(
            attempt_id=decoded["attempt_id"],
            run_id=decoded["run_id"],
            number=_integer(decoded["number"]),
            worker_id=decoded["worker_id"],
            lease_token=decoded["lease_token"],
            state=decoded["state"],
            started_at_ms=_integer(decoded["started_at_ms"]),
            finished_at_ms=_optional_integer(decoded.get("finished_at_ms")),
            error_class=decoded.get("error_class") or None,
            error_code=decoded.get("error_code") or None,
            error_message=decoded.get("error_message") or None,
        )


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    run: StageRunRecord
    created: bool


@dataclass(frozen=True, slots=True)
class StageLease:
    run: StageRunRecord
    attempt: AttemptRecord

    @property
    def token(self) -> str:
        return self.attempt.lease_token

    @property
    def worker_id(self) -> str:
        return self.attempt.worker_id


@dataclass(frozen=True, slots=True)
class QueueCounts:
    ready: int
    leased: int
    dead: int
    blocked: int


def require_mapping(value: Any, *, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} must be a mapping")
    return value
