from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeAlias, TypeVar

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = (
    JsonScalar | list["JsonValue"] | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
)

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class StageStatus(str, Enum):
    """
    Lifecycle state persisted for one stage run.
    Example: StageStatus.SUCCEEDED.
    """

    QUEUED = "queued"
    LEASED = "leased"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class StageIdentity:
    """
    Stable name and version for one stage implementation.
    Example: StageIdentity(name="parse_article", version="1.0.0").
    """

    name: str
    version: str

    def __post_init__(self) -> None:
        if not _NAME_PATTERN.fullmatch(self.name):
            raise ValueError(
                "stage name must start with a lowercase letter and contain only "
                "lowercase letters, numbers, and underscores"
            )
        if not _VERSION_PATTERN.fullmatch(self.version):
            raise ValueError("stage version contains unsupported characters")


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """
    Reference to an immutable stage artifact in object storage, such as Cloudflare R2.
    Example: ArtifactRef(..., kind="raw_http", object_key="raw-http/v1/...").
    """

    artifact_id: str
    kind: str
    schema_version: str
    content_sha256: str
    object_key: str
    content_type: str
    byte_size: int

    def __post_init__(self) -> None:
        if not self.artifact_id:
            raise ValueError("artifact_id must not be empty")
        if not _NAME_PATTERN.fullmatch(self.kind):
            raise ValueError("artifact kind must use the stage-name format")
        if not _VERSION_PATTERN.fullmatch(self.schema_version):
            raise ValueError("artifact schema version contains unsupported characters")
        if not _SHA256_PATTERN.fullmatch(self.content_sha256):
            raise ValueError("artifact content_sha256 must be a lowercase SHA-256 value")
        if not self.object_key or self.object_key.startswith("/"):
            raise ValueError("artifact object_key must be a non-rooted object key")
        if not self.content_type:
            raise ValueError("artifact content_type must not be empty")
        if self.byte_size < 0:
            raise ValueError("artifact byte_size must not be negative")


class StageInput(Protocol):
    """
    Semantic input that a stage uses to calculate its idempotency key.
    Example: ParseArticleInput(fetch_id="fetch-1") implements this protocol.
    """

    def idempotency_payload(self) -> Mapping[str, JsonValue]:
        """
        Return only values that affect the semantic stage result.
        """


InputT = TypeVar("InputT", bound=StageInput)
OutputT = TypeVar("OutputT")


@dataclass(frozen=True, slots=True)
class StageRequest(Generic[InputT]):
    """
    Versioned stage invocation with semantic input and configuration.
    Example: StageRequest(identity=identity, stage_input=value, config_version="1").
    """

    identity: StageIdentity
    stage_input: InputT
    config_version: str

    def __post_init__(self) -> None:
        if not _VERSION_PATTERN.fullmatch(self.config_version):
            raise ValueError("configuration version contains unsupported characters")

    def idempotency_key(self) -> str:
        return make_idempotency_key(
            identity=self.identity,
            input_payload=self.stage_input.idempotency_payload(),
            config_version=self.config_version,
        )




@dataclass(frozen=True, slots=True)
class StageResult(Generic[OutputT]):
    """
    Typed stage output with references to immutable artifacts.
    Example: StageResult(output=parsed, artifacts=(raw_artifact,)).
    """

    output: OutputT
    artifacts: tuple[ArtifactRef, ...] = ()




def _normalize_json_value(value: object, path: str = "$") -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} contains a non-finite float")
        return value
    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path} contains a non-string mapping key")
            normalized[key] = _normalize_json_value(item, f"{path}.{key}")
        return normalized
    if isinstance(value, (list, tuple)):
        return [
            _normalize_json_value(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    raise TypeError(f"{path} contains unsupported value type {type(value).__name__}")


def canonical_json(value: JsonValue) -> str:
    normalized = _normalize_json_value(value)
    return json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def make_idempotency_key(
    *,
    identity: StageIdentity,
    input_payload: Mapping[str, JsonValue],
    config_version: str,
) -> str:
    if not _VERSION_PATTERN.fullmatch(config_version):
        raise ValueError("configuration version contains unsupported characters")
    preimage: JsonValue = {
        "configuration_version": config_version,
        "input": input_payload,
        "stage": {
            "name": identity.name,
            "version": identity.version,
        },
    }
    digest = hashlib.sha256(canonical_json(preimage).encode("utf-8")).hexdigest()
    return f"{identity.name}:{identity.version}:{digest}"


def make_run_idempotency_key(base_key: str, *, rerun_nonce: str | None = None) -> str:
    if not base_key:
        raise ValueError("base_key must not be empty")
    if not rerun_nonce:
        return base_key
    nonce_digest = hashlib.sha256(rerun_nonce.encode("utf-8")).hexdigest()
    return f"{base_key}:force:{nonce_digest}"
