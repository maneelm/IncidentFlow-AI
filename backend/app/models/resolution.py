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
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdentityMixin, TimestampMixin
from app.models.entities import enum_column


class ProposalStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    QUEUED = "queued"
    RUNNING = "running"
    REJECTED = "rejected"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class SyntheticServiceState(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "synthetic_service_states"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            (
                "snapshot @> '{\"synthetic\": true}'::jsonb AND jsonb_typeof(snapsho"
                "t) = 'object' AND octet_length(snapshot::text) <= 8192"
            ),
            name="synthetic_snapshot",
        ),
    )
    service_id: Mapped[UUID] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), unique=True
    )
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
    initialized_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    __mapper_args__ = {"version_id_col": version}


class ResolutionProposal(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "resolution_proposals"
    __table_args__ = (
        UniqueConstraint(
            "requested_by", "client_request_id", name="uq_resolution_proposals_client"
        ),
        CheckConstraint(
            "version >= 1 AND incident_version >= 1 AND state_version >= 1",
            name="versions_positive",
        ),
        CheckConstraint("risk IN ('read_only','synthetic_mutation')", name="risk_valid"),
        CheckConstraint("origin IN ('human','investigation')", name="origin_valid"),
        CheckConstraint(
            "risk != 'synthetic_mutation' OR origin = 'human'", name="mutation_human_authored"
        ),
        CheckConstraint(
            ("jsonb_typeof(parameters) = 'object' AND octet_length(parameters::text) <= 2048"),
            name="parameters_bounded",
        ),
        CheckConstraint(
            ("jsonb_typeof(provenance) = 'object' AND octet_length(provenance::text) <= 8192"),
            name="provenance_bounded",
        ),
        CheckConstraint(
            "jsonb_typeof(policy) = 'object' AND octet_length(policy::text) <= 2048",
            name="policy_bounded",
        ),
        Index("ix_resolution_proposals_incident_created", "incident_id", "created_at", "id"),
        Index("ix_resolution_proposals_expiration", "status", "approval_deadline"),
    )
    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"))
    investigation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("investigation_reports.investigation_id", ondelete="RESTRICT")
    )
    service_id: Mapped[UUID] = mapped_column(ForeignKey("services.id", ondelete="RESTRICT"))
    state_id: Mapped[UUID] = mapped_column(
        ForeignKey("synthetic_service_states.id", ondelete="RESTRICT")
    )
    incident_version: Mapped[int] = mapped_column(Integer)
    state_version: Mapped[int] = mapped_column(Integer)
    action_id: Mapped[str] = mapped_column(String(60))
    action_fingerprint: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(String(2000))
    origin: Mapped[str] = mapped_column(String(20))
    risk: Mapped[str] = mapped_column(String(30))
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    policy: Mapped[dict[str, Any]] = mapped_column(JSONB)
    payload_hash: Mapped[str] = mapped_column(String(64))
    client_request_id: Mapped[UUID] = mapped_column()
    status: Mapped[ProposalStatus] = mapped_column(enum_column(ProposalStatus, "proposal_status"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    approval_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(60))
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class ResolutionEvidence(Base):
    __tablename__ = "resolution_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["investigation_id", "reference"],
            ["investigation_evidence.investigation_id", "investigation_evidence.reference"],
            ondelete="RESTRICT",
            name="fk_resolution_evidence_source",
        ),
    )
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("resolution_proposals.id", ondelete="RESTRICT"), primary_key=True
    )
    investigation_id: Mapped[UUID] = mapped_column(
        ForeignKey("investigation_reports.investigation_id", ondelete="RESTRICT")
    )
    reference: Mapped[str] = mapped_column(String(80), primary_key=True)


class ResolutionApproval(IdentityMixin, Base):
    __tablename__ = "resolution_approvals"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_resolution_approvals_proposal"),
        UniqueConstraint("proposal_id", "id", name="uq_resolution_approvals_pair"),
        CheckConstraint("decision IN ('approved','rejected')", name="decision_valid"),
        CheckConstraint(
            "jsonb_typeof(snapshot) = 'object' AND octet_length(snapshot::text) <= 16384",
            name="snapshot_bounded",
        ),
    )
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("resolution_proposals.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    decision: Mapped[str] = mapped_column(String(20))
    payload_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResolutionJob(IdentityMixin, Base):
    __tablename__ = "resolution_jobs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["proposal_id", "approval_id"],
            ["resolution_approvals.proposal_id", "resolution_approvals.id"],
            ondelete="RESTRICT",
            name="fk_resolution_jobs_approval",
        ),
        CheckConstraint(
            "status IN ('pending','processing','completed','failed','cancelled','expired')",
            name="status_valid",
        ),
        CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts AND max_attempts BETWEEN 1 AND 5",
            name="attempts_valid",
        ),
        CheckConstraint(
            (
                "(status = 'processing' AND lease_token IS NOT NULL AND lease_expi"
                "res_at IS NOT NULL) OR (status <> 'processing' AND lease_token IS"
                " NULL AND lease_expires_at IS NULL)"
            ),
            name="lease_consistent",
        ),
        Index("ix_resolution_jobs_ready", "status", "available_at", "created_at"),
    )
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("resolution_proposals.id", ondelete="RESTRICT"), unique=True
    )
    approval_id: Mapped[UUID] = mapped_column()
    status: Mapped[str] = mapped_column(String(20))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[UUID | None] = mapped_column()
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResolutionExecution(IdentityMixin, Base):
    __tablename__ = "resolution_executions"
    __table_args__ = (
        CheckConstraint(
            "attempt >= 1 AND before_version >= 1 AND after_version >= before_version",
            name="versions_valid",
        ),
        CheckConstraint(
            ("result @> '{\"synthetic\": true}'::jsonb AND octet_length(result::text) <= 16384"),
            name="result_bounded",
        ),
        CheckConstraint(
            "verification @> '{\"synthetic\": true}'::jsonb "
            "AND octet_length(verification::text) <= 2048",
            name="verification_bounded",
        ),
    )
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("resolution_proposals.id", ondelete="RESTRICT"), unique=True
    )
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("resolution_jobs.id", ondelete="RESTRICT"), unique=True
    )
    attempt: Mapped[int] = mapped_column(Integer)
    before_version: Mapped[int] = mapped_column(Integer)
    after_version: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    verification: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResolutionEvent(IdentityMixin, Base):
    __tablename__ = "resolution_events"
    __table_args__ = (
        UniqueConstraint("proposal_id", "proposal_version", name="uq_resolution_events_version"),
        CheckConstraint("proposal_version >= 1", name="version_positive"),
        CheckConstraint(
            "(actor_id IS NOT NULL AND system_actor IS NULL) OR "
            "(actor_id IS NULL AND system_actor = 'resolution_worker')",
            name="one_actor",
        ),
        CheckConstraint(
            "jsonb_typeof(details) = 'object' AND octet_length(details::text) <= 4096",
            name="details_bounded",
        ),
        CheckConstraint(
            (
                "event_type IN ('proposal_created','proposal_submitted','approval_"
                "granted','approval_rejected','approval_expired','proposal_cancell"
                "ed','execution_queued','execution_started','execution_retried','e"
                "xecution_succeeded','execution_failed','verification_completed')"
            ),
            name="event_type_valid",
        ),
        Index("ix_resolution_events_history", "proposal_id", "occurred_at", "id"),
    )
    proposal_id: Mapped[UUID] = mapped_column(
        ForeignKey("resolution_proposals.id", ondelete="RESTRICT")
    )
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    system_actor: Mapped[str | None] = mapped_column(String(60))
    event_type: Mapped[str] = mapped_column(String(40))
    proposal_version: Mapped[int] = mapped_column(Integer)
    correlation_id: Mapped[UUID | None] = mapped_column()
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    details: Mapped[dict[str, Any]] = mapped_column(JSONB)
