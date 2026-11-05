"""Structured logs durable detection and ingestion credentials

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # This revision is additive: the Phase 1 tables and append-only trigger remain intact.
    op.create_table(
        "detection_rules",
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "level",
            sa.Enum(
                "debug",
                "info",
                "warning",
                "error",
                "critical",
                name="rule_log_level",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "incident_severity",
            sa.Enum(
                "critical",
                "high",
                "medium",
                "low",
                name="rule_severity",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eligible_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "cooldown_seconds BETWEEN 0 AND 86400", name=op.f("ck_detection_rules_cooldown_bounds")
        ),
        sa.CheckConstraint(
            "threshold BETWEEN 1 AND 1000", name=op.f("ck_detection_rules_threshold_bounds")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_detection_rules_version_positive")),
        sa.CheckConstraint(
            "window_seconds BETWEEN 5 AND 3600", name=op.f("ck_detection_rules_window_bounds")
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_detection_rules_service_id_services"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detection_rules")),
        sa.UniqueConstraint("service_id", "name", name="uq_detection_rules_service_name"),
    )
    op.create_index(
        "ix_detection_rules_service_enabled",
        "detection_rules",
        ["service_id", "enabled"],
        unique=False,
    )
    op.create_table(
        "ingestion_credentials",
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_ingestion_credentials_positive_lifetime")
        ),
        sa.CheckConstraint(
            "length(secret_hash) = 64", name=op.f("ck_ingestion_credentials_hash_length")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_ingestion_credentials_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_ingestion_credentials_service_id_services"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_credentials")),
    )
    op.create_index(
        op.f("ix_ingestion_credentials_created_by"),
        "ingestion_credentials",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_credentials_service_created",
        "ingestion_credentials",
        ["service_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "detection_occurrences",
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("rule_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("matching_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "matching_count BETWEEN 1 AND 1000",
            name=op.f("ck_detection_occurrences_matching_count_bounds"),
        ),
        sa.CheckConstraint(
            "window_end >= window_start", name=op.f("ck_detection_occurrences_window_order")
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_detection_occurrences_incident_id_incidents"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["detection_rules.id"],
            name=op.f("fk_detection_occurrences_rule_id_detection_rules"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_detection_occurrences_service_id_services"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detection_occurrences")),
        sa.UniqueConstraint("incident_id", name=op.f("uq_detection_occurrences_incident_id")),
    )
    op.create_index(
        "ix_detection_occurrences_rule_created",
        "detection_occurrences",
        ["rule_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_detection_occurrences_service_created",
        "detection_occurrences",
        ["service_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_detection_occurrences_current_rule",
        "detection_occurrences",
        ["rule_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "log_entries",
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.String(length=128), nullable=False),
        sa.Column("credential_id", sa.Uuid(), nullable=False),
        sa.Column(
            "level",
            sa.Enum(
                "debug",
                "info",
                "warning",
                "error",
                "critical",
                name="log_level",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("message", sa.String(length=4000), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'", name=op.f("ck_log_entries_metadata_object")
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64", name=op.f("ck_log_entries_content_hash_length")
        ),
        sa.CheckConstraint(
            "length(trim(message)) > 0", name=op.f("ck_log_entries_message_nonempty")
        ),
        sa.ForeignKeyConstraint(
            ["credential_id"],
            ["ingestion_credentials.id"],
            name=op.f("fk_log_entries_credential_id_ingestion_credentials"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_log_entries_service_id_services"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_log_entries")),
        sa.UniqueConstraint("service_id", "source_event_id", name="uq_log_entries_service_source"),
    )
    op.create_index(
        op.f("ix_log_entries_credential_id"), "log_entries", ["credential_id"], unique=False
    )
    op.create_index(
        "ix_log_entries_ingested_id", "log_entries", ["ingested_at", "id"], unique=False
    )
    op.create_index("ix_log_entries_request_id", "log_entries", ["request_id"], unique=False)
    op.create_index(
        "ix_log_entries_service_error_received",
        "log_entries",
        ["service_id", "error_code", "ingested_at"],
        unique=False,
    )
    op.create_index(
        "ix_log_entries_service_level_received",
        "log_entries",
        ["service_id", "level", "ingested_at", "id"],
        unique=False,
    )
    op.create_index("ix_log_entries_timestamp_id", "log_entries", ["timestamp", "id"], unique=False)
    op.create_index("ix_log_entries_trace_id", "log_entries", ["trace_id"], unique=False)
    op.create_index(
        "ix_log_entries_message_search",
        "log_entries",
        [sa.text("to_tsvector('simple'::regconfig, message)")],
        postgresql_using="gin",
    )
    op.create_table(
        "detection_evidence",
        sa.Column("occurrence_id", sa.Uuid(), nullable=False),
        sa.Column("log_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["log_id"],
            ["log_entries.id"],
            name=op.f("fk_detection_evidence_log_id_log_entries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["occurrence_id"],
            ["detection_occurrences.id"],
            name=op.f("fk_detection_evidence_occurrence_id_detection_occurrences"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("occurrence_id", "log_id", name=op.f("pk_detection_evidence")),
    )
    op.create_index(
        op.f("ix_detection_evidence_log_id"), "detection_evidence", ["log_id"], unique=False
    )
    op.create_table(
        "detection_jobs",
        sa.Column("position", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("log_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "completed",
                "failed",
                name="job_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("total_attempts", sa.Integer(), nullable=False),
        sa.Column("requeues", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(status = 'processing' AND lease_token IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'processing' AND lease_token IS NULL AND lease_expires_at IS NULL)",
            name=op.f("ck_detection_jobs_processing_lease"),
        ),
        sa.CheckConstraint(
            "(status IN ('completed', 'failed')) = (finished_at IS NOT NULL)",
            name=op.f("ck_detection_jobs_finished_timestamp"),
        ),
        sa.CheckConstraint(
            "attempts BETWEEN 0 AND max_attempts", name=op.f("ck_detection_jobs_attempt_bounds")
        ),
        sa.CheckConstraint(
            "max_attempts BETWEEN 1 AND 10", name=op.f("ck_detection_jobs_max_attempt_bounds")
        ),
        sa.CheckConstraint(
            "total_attempts >= attempts AND requeues >= 0",
            name=op.f("ck_detection_jobs_history_bounds"),
        ),
        sa.ForeignKeyConstraint(
            ["log_id"],
            ["log_entries.id"],
            name=op.f("fk_detection_jobs_log_id_log_entries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_detection_jobs_service_id_services"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_detection_jobs")),
        sa.UniqueConstraint("log_id", name=op.f("uq_detection_jobs_log_id")),
        sa.UniqueConstraint("position", name=op.f("uq_detection_jobs_position")),
    )
    op.create_index(
        "ix_detection_jobs_service_position",
        "detection_jobs",
        ["service_id", "position"],
        unique=False,
    )
    op.create_index(
        "ix_detection_jobs_status_available",
        "detection_jobs",
        ["status", "available_at", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_log_entries_message_search", table_name="log_entries")
    op.drop_index("ix_detection_jobs_status_available", table_name="detection_jobs")
    op.drop_index("ix_detection_jobs_service_position", table_name="detection_jobs")
    op.drop_table("detection_jobs")
    op.drop_index(op.f("ix_detection_evidence_log_id"), table_name="detection_evidence")
    op.drop_table("detection_evidence")
    op.drop_index("ix_log_entries_trace_id", table_name="log_entries")
    op.drop_index("ix_log_entries_timestamp_id", table_name="log_entries")
    op.drop_index("ix_log_entries_service_level_received", table_name="log_entries")
    op.drop_index("ix_log_entries_service_error_received", table_name="log_entries")
    op.drop_index("ix_log_entries_request_id", table_name="log_entries")
    op.drop_index("ix_log_entries_ingested_id", table_name="log_entries")
    op.drop_index(op.f("ix_log_entries_credential_id"), table_name="log_entries")
    op.drop_table("log_entries")
    op.drop_index(
        "uq_detection_occurrences_current_rule",
        table_name="detection_occurrences",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_index("ix_detection_occurrences_service_created", table_name="detection_occurrences")
    op.drop_index("ix_detection_occurrences_rule_created", table_name="detection_occurrences")
    op.drop_table("detection_occurrences")
    op.drop_index("ix_ingestion_credentials_service_created", table_name="ingestion_credentials")
    op.drop_index(op.f("ix_ingestion_credentials_created_by"), table_name="ingestion_credentials")
    op.drop_table("ingestion_credentials")
    op.drop_index("ix_detection_rules_service_enabled", table_name="detection_rules")
    op.drop_table("detection_rules")
