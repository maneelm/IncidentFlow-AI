from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    func,
    literal_column,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdentityMixin, TimestampMixin
from app.models.enums import EventType, IncidentStatus, Role, Severity


def enum_column(enum: type, name: str) -> Enum:
    return Enum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
    )


class User(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="email_lowercase"),
        CheckConstraint("length(trim(display_name)) > 0", name="display_name_nonempty"),
    )

    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Role] = mapped_column(enum_column(Role, "user_role"))


class Service(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("name = lower(name) AND length(trim(name)) > 0", name="name_valid"),
        CheckConstraint("length(trim(environment)) > 0", name="environment_nonempty"),
    )

    name: Mapped[str] = mapped_column(String(100), unique=True)
    description: Mapped[str] = mapped_column(Text)
    environment: Mapped[str] = mapped_column(String(50))


incident_number_seq = Sequence("incident_number_seq", metadata=Base.metadata)


class Incident(IdentityMixin, TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(trim(title)) > 0", name="title_nonempty"),
        CheckConstraint(
            "(status IN ('resolved', 'closed')) = (resolved_at IS NOT NULL)",
            name="resolution_timestamp",
        ),
        CheckConstraint("(status = 'closed') = (closed_at IS NOT NULL)", name="closing_timestamp"),
        Index("ix_incidents_created_at_id", "created_at", "id"),
        Index("ix_incidents_status_created_at", "status", "created_at"),
        Index("ix_incidents_severity_created_at", "severity", "created_at"),
        Index(
            "ix_incidents_knowledge_search",
            func.to_tsvector(
                literal_column("'simple'::regconfig"),
                literal_column("(title::text || ' '::text) || description"),
            ),
            postgresql_using="gin",
        ),
    )

    number: Mapped[int] = mapped_column(
        BigInteger,
        incident_number_seq,
        server_default=incident_number_seq.next_value(),
        unique=True,
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    severity: Mapped[Severity] = mapped_column(enum_column(Severity, "incident_severity"))
    status: Mapped[IncidentStatus] = mapped_column(enum_column(IncidentStatus, "incident_status"))
    service_id: Mapped[UUID] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), index=True
    )
    assignee_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version}

    service: Mapped[Service] = relationship(lazy="raise")
    assignee: Mapped[User | None] = relationship(lazy="raise")

    @property
    def incident_number(self) -> str:
        return f"INC-{self.number:06d}"


class IncidentEvent(IdentityMixin, Base):
    __tablename__ = "incident_events"
    __table_args__ = (
        CheckConstraint(
            "(actor_id IS NOT NULL AND system_actor IS NULL) OR "
            "(actor_id IS NULL AND system_actor IS NOT NULL AND length(trim(system_actor)) > 0)",
            name="exactly_one_actor",
        ),
        CheckConstraint("incident_version >= 1", name="incident_version_positive"),
        Index("ix_incident_events_incident_id_occurred_at", "incident_id", "occurred_at", "id"),
        Index(
            "uq_incident_events_incident_version", "incident_id", "incident_version", unique=True
        ),
    )

    incident_id: Mapped[UUID] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"))
    event_type: Mapped[EventType] = mapped_column(enum_column(EventType, "event_type"))
    actor_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    system_actor: Mapped[str | None] = mapped_column(String(120))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    incident_version: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB)
    incident: Mapped[Incident] = relationship(lazy="raise")
    actor: Mapped[User | None] = relationship(lazy="raise")
