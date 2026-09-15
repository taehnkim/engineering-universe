from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

FETCH_STATES = ("pending", "leased", "retry_wait", "fetched", "blocked")
URL_KINDS = ("primary", "redirect_alias", "canonical_candidate")
FETCH_OUTCOMES = (
    "succeeded",
    "not_modified",
    "http_error",
    "transport_error",
    "robots_denied",
    "cancelled",
)
RELATION_TYPES = (
    "exact_content",
    "near_duplicate",
    "permanent_redirect",
    "canonical",
)
STAGE_STATUSES = (
    "queued",
    "leased",
    "running",
    "succeeded",
    "retry_wait",
    "failed",
    "blocked",
    "cancelled",
    "skipped",
)
ERROR_CATEGORIES = ("retryable", "permanent", "blocked")


def _sql_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            f"fetch_state IN ({_sql_values(FETCH_STATES)})",
            name="fetch_state",
        ),
        CheckConstraint(
            """
            (
                fetch_state = 'leased'
                AND lease_owner IS NOT NULL
                AND lease_until IS NOT NULL
            )
            OR
            (
                fetch_state <> 'leased'
                AND lease_owner IS NULL
                AND lease_until IS NULL
            )
            """,
            name="lease_fields",
        ),
        Index(
            "ix_documents_due",
            "next_fetch_at",
            "id",
            postgresql_where=text(
                "fetch_state IN ('pending', 'retry_wait') "
                "AND next_fetch_at IS NOT NULL AND lease_until IS NULL"
            ),
        ),
        Index(
            "ix_documents_lease_expiry",
            "lease_until",
            "id",
            postgresql_where=text("lease_until IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    fetch_state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'pending'")
    )
    next_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_fetch_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "document_fetches.id",
            name="fk_documents_current_fetch_id_document_fetches",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    current_extraction_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "artifacts.id",
            name="fk_documents_current_extraction_artifact_id_artifacts",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )
    current_indexed_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey(
            "artifacts.id",
            name="fk_documents_current_indexed_artifact_id_artifacts",
            use_alter=True,
            ondelete="SET NULL",
        ),
    )


class DocumentURL(Base):
    __tablename__ = "document_urls"
    __table_args__ = (
        UniqueConstraint("normalized_url", name="uq_document_urls_normalized_url"),
        CheckConstraint(
            f"url_kind IN ({_sql_values(URL_KINDS)})",
            name="url_kind",
        ),
        CheckConstraint(
            "normalized_url ~ '^https?://'",
            name="normalized_url_http",
        ),
        CheckConstraint(
            "original_url ~ '^https?://'",
            name="original_url_http",
        ),
        Index("ix_document_urls_document_id", "document_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    normalized_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    original_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    url_kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'primary'")
    )
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentDiscovery(Base):
    __tablename__ = "document_discoveries"
    __table_args__ = (
        Index(
            "ix_document_discoveries_document_time",
            "document_id",
            text("discovered_at DESC"),
        ),
        Index(
            "ix_document_discoveries_source_time",
            "source_key",
            text("discovered_at DESC"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_config_version: Mapped[str] = mapped_column(String(128), nullable=False)
    discovered_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    source_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    discovery_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )


class StageRun(TimestampMixin, Base):
    __tablename__ = "stage_runs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_stage_runs_idempotency_key"),
        CheckConstraint(
            f"status IN ({_sql_values(STAGE_STATUSES)})",
            name="status",
        ),
        CheckConstraint(
            f"error_category IS NULL OR "
            f"error_category IN ({_sql_values(ERROR_CATEGORIES)})",
            name="error_category",
        ),
        CheckConstraint(
            "attempt >= 0 AND max_attempts > 0 AND attempt <= max_attempts",
            name="attempt_range",
        ),
        CheckConstraint(
            """
            (
                status IN ('leased', 'running')
                AND lease_owner IS NOT NULL
                AND lease_until IS NOT NULL
            )
            OR
            (
                status NOT IN ('leased', 'running')
                AND lease_owner IS NULL
                AND lease_until IS NULL
            )
            """,
            name="lease_fields",
        ),
        CheckConstraint(
            "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at",
            name="time_order",
        ),
        Index(
            "ix_stage_runs_retry_due",
            "next_attempt_at",
            "id",
            postgresql_where=text(
                "status IN ('queued', 'retry_wait') "
                "AND next_attempt_at IS NOT NULL"
            ),
        ),
        Index(
            "ix_stage_runs_lease_expiry",
            "lease_until",
            "id",
            postgresql_where=text("lease_until IS NOT NULL"),
        ),
        Index("ix_stage_runs_document_time", "document_id", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    parent_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stage_runs.id", ondelete="SET NULL")
    )
    stage_name: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_version: Mapped[str] = mapped_column(String(128), nullable=False)
    config_version: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'queued'")
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_payload: Mapped[dict[str, Any]] = mapped_column(
        "input", JSONB, nullable=False
    )
    output_payload: Mapped[dict[str, Any] | None] = mapped_column("output", JSONB)
    error_category: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_artifacts_object_key"),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="content_sha256",
        ),
        CheckConstraint("byte_size >= 0", name="byte_size"),
        Index("ix_artifacts_document_kind", "document_id", "kind"),
        Index("ix_artifacts_producer_stage_run_id", "producer_stage_run_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    producer_stage_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("stage_runs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(128), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentFetch(Base):
    __tablename__ = "document_fetches"
    __table_args__ = (
        CheckConstraint(
            f"outcome IN ({_sql_values(FETCH_OUTCOMES)})",
            name="outcome",
        ),
        CheckConstraint(
            "status_code IS NULL OR status_code BETWEEN 100 AND 599",
            name="status_code",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="time_order",
        ),
        Index(
            "ix_document_fetches_document_time",
            "document_id",
            text("started_at DESC"),
        ),
        Index("ix_document_fetches_stage_run_id", "stage_run_id"),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_url_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("document_urls.id", ondelete="RESTRICT"),
        nullable=False,
    )
    stage_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stage_runs.id", ondelete="SET NULL")
    )
    body_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    method: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'GET'")
    )
    requested_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    final_url: Mapped[str | None] = mapped_column(String(2048))
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_headers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    etag: Mapped[str | None] = mapped_column(String(1024))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    robots_policy_hash: Mapped[str | None] = mapped_column(String(64))
    robots_allowed: Mapped[bool | None] = mapped_column(Boolean)
    error_kind: Mapped[str | None] = mapped_column(String(128))
    error_detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DocumentRelation(Base):
    __tablename__ = "document_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "target_document_id",
            "relation_type",
            name="uq_document_relations_edge",
        ),
        CheckConstraint(
            f"relation_type IN ({_sql_values(RELATION_TYPES)})",
            name="relation_type",
        ),
        CheckConstraint(
            "source_document_id <> target_document_id",
            name="different_documents",
        ),
        Index("ix_document_relations_target_document_id", "target_document_id"),
        Index("ix_document_relations_created_at", text("created_at DESC")),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    source_document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_document_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    evidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
