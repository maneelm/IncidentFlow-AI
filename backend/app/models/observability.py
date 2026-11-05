from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    literal_column,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdentityMixin, TimestampMixin
from app.models.entities import enum_column
from app.models.enums import JobStatus, LogLevel, Severity


class IngestionCredential(IdentityMixin, Base):
    __tablename__ = "ingestion_credentials"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="positive_lifetime"),
        CheckConstraint("length(secret_hash) = 64", name="hash_length"),
        Index("ix_ingestion_credentials_service_created", "service_id", "created_at"),
    )

    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(100))
    secret_hash: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LogEntry(IdentityMixin, Base):
    __tablename__ = "log_entries"
    __table_args__ = (
        UniqueConstraint("service_id", "source_event_id", name="uq_log_entries_service_source"),
        CheckConstraint("length(trim(message)) > 0", name="message_nonempty"),
        CheckConstraint("length(content_hash) = 64", name="content_hash_length"),
        CheckConstraint("jsonb_typeof(metadata) = 'object'", name="metadata_object"),
        Index("ix_log_entries_service_level_received", "service_id", "level", "ingested_at", "id"),
        Index("ix_log_entries_service_error_received", "service_id", "error_code", "ingested_at"),
        Index("ix_log_entries_timestamp_id", "timestamp", "id"),
        Index("ix_log_entries_ingested_id", "ingested_at", "id"),
        Index("ix_log_entries_trace_id", "trace_id"),
        Index("ix_log_entries_request_id", "request_id"),
        Index(
            "ix_log_entries_message_search",
            func.to_tsvector(literal_column("'simple'::regconfig"), literal_column("message")),
            postgresql_using="gin",
        ),
    )

    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    source_event_id: Mapped[str] = mapped_column(String(128))
    credential_id: Mapped[UUID] = mapped_column(
        ForeignKey("ingestion_credentials.id", ondelete="RESTRICT"), index=True
    )
    level: Mapped[LogLevel] = mapped_column(enum_column(LogLevel, "log_level"))
    message: Mapped[str] = mapped_column(String(4000))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(String(128))
    request_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(100))
    details: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))


class DetectionRule(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "detection_rules"
    __table_args__ = (
        UniqueConstraint("service_id", "name", name="uq_detection_rules_service_name"),
        CheckConstraint("threshold BETWEEN 1 AND 1000", name="threshold_bounds"),
        CheckConstraint("window_seconds BETWEEN 5 AND 3600", name="window_bounds"),
        CheckConstraint("cooldown_seconds BETWEEN 0 AND 86400", name="cooldown_bounds"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_detection_rules_service_enabled", "service_id", "enabled"),
    )

    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(default=True)
    level: Mapped[LogLevel] = mapped_column(enum_column(LogLevel, "rule_log_level"))
    error_code: Mapped[str | None] = mapped_column(String(100))
    threshold: Mapped[int] = mapped_column(Integer)
    window_seconds: Mapped[int] = mapped_column(Integer)
    incident_severity: Mapped[Severity] = mapped_column(enum_column(Severity, "rule_severity"))
    cooldown_seconds: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    eligible_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version}


class DetectionOccurrence(IdentityMixin, Base):
    __tablename__ = "detection_occurrences"
    __table_args__ = (
        CheckConstraint("matching_count BETWEEN 1 AND 1000", name="matching_count_bounds"),
        CheckConstraint("window_end >= window_start", name="window_order"),
        Index("ix_detection_occurrences_rule_created", "rule_id", "created_at", "id"),
        Index("ix_detection_occurrences_service_created", "service_id", "created_at", "id"),
        Index(
            "uq_detection_occurrences_current_rule",
            "rule_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    rule_id: Mapped[UUID] = mapped_column(ForeignKey("detection_rules.id", ondelete="RESTRICT"))
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    incident_id: Mapped[UUID] = mapped_column(
        ForeignKey("incidents.id", ondelete="RESTRICT"), unique=True
    )
    is_current: Mapped[bool] = mapped_column(default=True)
    rule_version: Mapped[int] = mapped_column(Integer)
    rule_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    matching_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def detection_key(self) -> str:
        return f"{self.service_id}:{self.rule_id}"


class DetectionEvidence(Base):
    __tablename__ = "detection_evidence"

    occurrence_id: Mapped[UUID] = mapped_column(
        ForeignKey("detection_occurrences.id", ondelete="RESTRICT"), primary_key=True
    )
    log_id: Mapped[UUID] = mapped_column(
        ForeignKey("log_entries.id", ondelete="RESTRICT"), primary_key=True, index=True
    )


class DetectionJob(IdentityMixin, Base):
    __tablename__ = "detection_jobs"
    __table_args__ = (
        CheckConstraint("attempts BETWEEN 0 AND max_attempts", name="attempt_bounds"),
        CheckConstraint("max_attempts BETWEEN 1 AND 10", name="max_attempt_bounds"),
        CheckConstraint("total_attempts >= attempts AND requeues >= 0", name="history_bounds"),
        CheckConstraint(
            "(status = 'processing' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'processing' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name="processing_lease",
        ),
        CheckConstraint(
            "(status IN ('completed', 'failed')) = (finished_at IS NOT NULL)",
            name="finished_timestamp",
        ),
        Index("ix_detection_jobs_service_position", "service_id", "position"),
        Index("ix_detection_jobs_status_available", "status", "available_at", "position"),
    )

    position: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    log_id: Mapped[UUID] = mapped_column(
        ForeignKey("log_entries.id", ondelete="RESTRICT"), unique=True
    )
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    status: Mapped[JobStatus] = mapped_column(enum_column(JobStatus, "job_status"))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    total_attempts: Mapped[int] = mapped_column(Integer, default=0)
    requeues: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
