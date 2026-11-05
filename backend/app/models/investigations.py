from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdentityMixin, TimestampMixin
from app.models.entities import enum_column


class InvestigationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    INCONCLUSIVE = "inconclusive"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvestigationJobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Runbook(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "runbooks"
    __table_args__ = (CheckConstraint("current_version >= 1", name="positive_version"),)

    title: Mapped[str] = mapped_column(String(200))
    service_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), index=True
    )
    tags: Mapped[list[str]] = mapped_column(JSONB)
    current_version: Mapped[int] = mapped_column(Integer)


class RunbookVersion(IdentityMixin, Base):
    __tablename__ = "runbook_versions"
    __table_args__ = (
        UniqueConstraint("runbook_id", "version", name="uq_runbook_versions_number"),
        CheckConstraint("version >= 1", name="positive_version"),
        CheckConstraint("format IN ('markdown', 'text')", name="format_allowed"),
        CheckConstraint("octet_length(content) BETWEEN 1 AND 131072", name="content_size"),
        CheckConstraint("length(content_hash) = 64", name="hash_length"),
    )

    runbook_id: Mapped[UUID] = mapped_column(ForeignKey("runbooks.id", ondelete="RESTRICT"))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    service_id: Mapped[UUID | None] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    tags: Mapped[list[str]] = mapped_column(JSONB)
    format: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RunbookChunk(IdentityMixin, Base):
    __tablename__ = "runbook_chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="uq_runbook_chunks_ordinal"),
        CheckConstraint(
            "ordinal >= 0 AND end_char > start_char AND start_char >= 0", name="chunk_offsets"
        ),
        CheckConstraint("length(content) BETWEEN 1 AND 1800", name="content_size"),
        Index(
            "ix_runbook_chunks_search",
            func.to_tsvector(literal_column("'simple'::regconfig"), literal_column("search_text")),
            postgresql_using="gin",
        ),
    )

    version_id: Mapped[UUID] = mapped_column(ForeignKey("runbook_versions.id", ondelete="RESTRICT"))
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    search_text: Mapped[str] = mapped_column(Text)
    start_char: Mapped[int] = mapped_column(Integer)
    end_char: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))


class ResolutionNote(IdentityMixin, Base):
    __tablename__ = "resolution_notes"
    __table_args__ = (
        CheckConstraint("length(content) BETWEEN 1 AND 8000", name="content_size"),
        Index("ix_resolution_notes_incident_created", "incident_id", "created_at", "id"),
        Index(
            "ix_resolution_notes_search",
            func.to_tsvector(literal_column("'simple'::regconfig"), literal_column("content")),
            postgresql_using="gin",
        ),
    )

    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Investigation(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "investigations"
    __table_args__ = (
        UniqueConstraint("requested_by", "client_request_id", name="uq_investigations_request"),
        CheckConstraint("provider IN ('groq', 'fake')", name="provider_allowed"),
        CheckConstraint(
            "llm_calls >= 0 AND input_tokens_reserved >= 0 AND output_tokens_reserved >= 0",
            name="budgets_positive",
        ),
        CheckConstraint(
            "jsonb_typeof(checkpoint) = 'object' AND octet_length(checkpoint::text) <= 65536",
            name="checkpoint_size",
        ),
        CheckConstraint("jsonb_typeof(policy) = 'object'", name="policy_object"),
        CheckConstraint(
            "(status IN ('completed','inconclusive','failed','cancelled')) = ("
            "completed_at IS NOT NULL)",
            name="terminal_timestamp",
        ),
        Index("ix_investigations_incident_created", "incident_id", "created_at", "id"),
        Index("ix_investigations_requester_status", "requested_by", "status"),
        Index(
            "uq_investigations_active_incident",
            "incident_id",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )

    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    client_request_id: Mapped[UUID] = mapped_column()
    status: Mapped[InvestigationStatus] = mapped_column(
        enum_column(InvestigationStatus, "investigation_status")
    )
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100))
    stage: Mapped[str] = mapped_column(String(40))
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSONB)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens_reserved: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens_reserved: Mapped[int] = mapped_column(Integer, default=0)
    actual_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    actual_output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    error_code: Mapped[str | None] = mapped_column(String(60))


class InvestigationJob(IdentityMixin, Base):
    __tablename__ = "investigation_jobs"
    __table_args__ = (
        CheckConstraint(
            "attempts BETWEEN 0 AND max_attempts AND max_attempts BETWEEN 1 AND 5",
            name="attempt_bounds",
        ),
        CheckConstraint(
            "(status = 'processing' AND lease_token IS NOT NULL AND lease_expi"
            "res_at IS NOT NULL) OR (status <> 'processing' AND lease_token IS"
            " NULL AND lease_expires_at IS NULL)",
            name="lease_state",
        ),
        CheckConstraint(
            "(status IN ('completed','failed','cancelled')) = (finished_at IS NOT NULL)",
            name="terminal_timestamp",
        ),
        Index("ix_investigation_jobs_ready", "status", "available_at", "created_at"),
    )

    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="RESTRICT"), unique=True
    )
    status: Mapped[InvestigationJobStatus] = mapped_column(
        enum_column(InvestigationJobStatus, "investigation_job_status")
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InvestigationStep(IdentityMixin, Base):
    __tablename__ = "investigation_steps"
    __table_args__ = (
        UniqueConstraint(
            "investigation_id", "attempt", "stage", name="uq_investigation_steps_stage"
        ),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint(
            "jsonb_typeof(details) = 'object' AND octet_length(details::text) <= 8192",
            name="details_size",
        ),
    )

    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="RESTRICT")
    )
    attempt: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationEvidence(IdentityMixin, Base):
    __tablename__ = "investigation_evidence"
    __table_args__ = (
        UniqueConstraint(
            "investigation_id", "reference", name="uq_investigation_evidence_reference"
        ),
        CheckConstraint(
            "num_nonnulls(incident_id, service_id, event_id, log_id, detection"
            "_id, chunk_id, note_id) = 1",
            name="one_source",
        ),
        CheckConstraint(
            "(source_type IN ('incident','historical_incident') AND incident_i"
            "d IS NOT NULL) OR (source_type='service' AND service_id IS NOT NU"
            "LL) OR (source_type='event' AND event_id IS NOT NULL) OR (source_"
            "type='log' AND log_id IS NOT NULL) OR (source_type='detection' AN"
            "D detection_id IS NOT NULL) OR (source_type='runbook_chunk' AND c"
            "hunk_id IS NOT NULL) OR (source_type='resolution_note' AND note_i"
            "d IS NOT NULL)",
            name="source_type_matches",
        ),
        CheckConstraint(
            "reference = source_type || ':' || coalesce(incident_id,service_id"
            ",event_id,log_id,detection_id,chunk_id,note_id)::text",
            name="reference_matches",
        ),
        CheckConstraint(
            "jsonb_typeof(provenance) = 'object' AND octet_length(provenance::text) <= 4096",
            name="provenance_size",
        ),
    )

    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="RESTRICT")
    )
    reference: Mapped[str] = mapped_column(String(80))
    source_type: Mapped[str] = mapped_column(String(30))
    incident_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT")
    )
    service_id: Mapped[UUID | None] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    event_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("incident_events.id", ondelete="RESTRICT")
    )
    log_id: Mapped[UUID | None] = mapped_column(ForeignKey("log_entries.id", ondelete="RESTRICT"))
    detection_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("detection_occurrences.id", ondelete="RESTRICT")
    )
    chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("runbook_chunks.id", ondelete="RESTRICT")
    )
    note_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("resolution_notes.id", ondelete="RESTRICT")
    )
    label: Mapped[str] = mapped_column(String(240))
    excerpt: Mapped[str] = mapped_column(String(600))
    role: Mapped[str] = mapped_column(String(40))
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    quarantined: Mapped[bool] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationReport(Base):
    __tablename__ = "investigation_reports"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' AND octet_length(payload::text) <= 65536",
            name="payload_size",
        ),
    )

    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigations.id", ondelete="RESTRICT"), primary_key=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvestigationCitation(Base):
    __tablename__ = "investigation_citations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["investigation_id", "reference"],
            ["investigation_evidence.investigation_id", "investigation_evidence.reference"],
            name="fk_investigation_citations_evidence",
            ondelete="RESTRICT",
        ),
    )

    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigation_reports.investigation_id", ondelete="RESTRICT"), primary_key=True
    )
    reference: Mapped[str] = mapped_column(String(80), primary_key=True)
