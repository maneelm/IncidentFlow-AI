"""Initial incident management schema

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence("incident_number_seq")))
    op.create_table(
        "services",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("environment", sa.String(length=50), nullable=False),
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
            "length(trim(environment)) > 0", name=op.f("ck_services_environment_nonempty")
        ),
        sa.CheckConstraint(
            "name = lower(name) AND length(trim(name)) > 0", name=op.f("ck_services_name_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_services")),
        sa.UniqueConstraint("name", name=op.f("uq_services_name")),
    )
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "admin",
                "engineer",
                "viewer",
                name="user_role",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
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
        sa.CheckConstraint("email = lower(email)", name=op.f("ck_users_email_lowercase")),
        sa.CheckConstraint(
            "length(trim(display_name)) > 0", name=op.f("ck_users_display_name_nonempty")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "incidents",
        sa.Column(
            "number",
            sa.BigInteger(),
            server_default=sa.text("nextval('incident_number_seq')"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "severity",
            sa.Enum(
                "critical",
                "high",
                "medium",
                "low",
                name="incident_severity",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "triggered",
                "investigating",
                "mitigated",
                "resolved",
                "closed",
                name="incident_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
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
            "(status = 'closed') = (closed_at IS NOT NULL)",
            name=op.f("ck_incidents_closing_timestamp"),
        ),
        sa.CheckConstraint(
            "(status IN ('resolved', 'closed')) = (resolved_at IS NOT NULL)",
            name=op.f("ck_incidents_resolution_timestamp"),
        ),
        sa.CheckConstraint("length(trim(title)) > 0", name=op.f("ck_incidents_title_nonempty")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_incidents_version_positive")),
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["users.id"],
            name=op.f("fk_incidents_assignee_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_incidents_service_id_services"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incidents")),
        sa.UniqueConstraint("number", name=op.f("uq_incidents_number")),
    )
    op.create_index(op.f("ix_incidents_assignee_id"), "incidents", ["assignee_id"], unique=False)
    op.create_index("ix_incidents_created_at_id", "incidents", ["created_at", "id"], unique=False)
    op.create_index(op.f("ix_incidents_service_id"), "incidents", ["service_id"], unique=False)
    op.create_index(
        "ix_incidents_severity_created_at", "incidents", ["severity", "created_at"], unique=False
    )
    op.create_index(
        "ix_incidents_status_created_at", "incidents", ["status", "created_at"], unique=False
    )
    op.create_table(
        "incident_events",
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(
            "event_type",
            sa.Enum(
                "created",
                "updated",
                "assigned",
                "transitioned",
                "reopened",
                name="event_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("system_actor", sa.String(length=120), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("incident_version", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(actor_id IS NOT NULL AND system_actor IS NULL) OR "
            "(actor_id IS NULL AND system_actor IS NOT NULL AND length(trim(system_actor)) > 0)",
            name=op.f("ck_incident_events_exactly_one_actor"),
        ),
        sa.CheckConstraint(
            "incident_version >= 1", name=op.f("ck_incident_events_incident_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_incident_events_actor_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["incident_id"],
            ["incidents.id"],
            name=op.f("fk_incident_events_incident_id_incidents"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_incident_events")),
    )
    op.create_index(
        op.f("ix_incident_events_actor_id"), "incident_events", ["actor_id"], unique=False
    )
    op.create_index(
        "ix_incident_events_incident_id_occurred_at",
        "incident_events",
        ["incident_id", "occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "uq_incident_events_incident_version",
        "incident_events",
        ["incident_id", "incident_version"],
        unique=True,
    )
    op.execute("""
        CREATE FUNCTION reject_incident_event_changes() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Incident events are append-only' USING ERRCODE = '23514';
        END $$
    """)
    op.execute("""
        CREATE TRIGGER incident_events_append_only
        BEFORE UPDATE OR DELETE ON incident_events
        FOR EACH ROW EXECUTE FUNCTION reject_incident_event_changes()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER incident_events_append_only ON incident_events")
    op.execute("DROP FUNCTION reject_incident_event_changes()")
    op.drop_index("uq_incident_events_incident_version", table_name="incident_events")
    op.drop_index("ix_incident_events_incident_id_occurred_at", table_name="incident_events")
    op.drop_index(op.f("ix_incident_events_actor_id"), table_name="incident_events")
    op.drop_table("incident_events")
    op.drop_index("ix_incidents_status_created_at", table_name="incidents")
    op.drop_index("ix_incidents_severity_created_at", table_name="incidents")
    op.drop_index(op.f("ix_incidents_service_id"), table_name="incidents")
    op.drop_index("ix_incidents_created_at_id", table_name="incidents")
    op.drop_index(op.f("ix_incidents_assignee_id"), table_name="incidents")
    op.drop_table("incidents")
    op.drop_table("users")
    op.drop_table("services")
    op.execute(sa.schema.DropSequence(sa.Sequence("incident_number_seq")))
