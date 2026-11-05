from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import SystemActor
from app.core.errors import DomainError, not_found, version_conflict
from app.models import EventType, Incident, IncidentEvent, IncidentStatus, Role, User
from app.repositories.incidents import IncidentRepository
from app.repositories.services import ServiceRepository
from app.repositories.users import UserRepository
from app.schemas.common import Page
from app.schemas.incidents import (
    AssignmentInput,
    EventRead,
    IncidentCreate,
    IncidentFilters,
    IncidentRead,
    IncidentUpdate,
    TransitionInput,
)

TRANSITIONS: dict[IncidentStatus, set[IncidentStatus]] = {
    IncidentStatus.TRIGGERED: {IncidentStatus.INVESTIGATING},
    IncidentStatus.INVESTIGATING: {IncidentStatus.MITIGATED, IncidentStatus.RESOLVED},
    IncidentStatus.MITIGATED: {IncidentStatus.RESOLVED},
    IncidentStatus.RESOLVED: {IncidentStatus.CLOSED, IncidentStatus.INVESTIGATING},
    IncidentStatus.CLOSED: {IncidentStatus.INVESTIGATING},
}


def json_value(value: Any) -> Any:
    return str(value) if isinstance(value, UUID) else value


class IncidentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = IncidentRepository(session)
        self.services = ServiceRepository(session)
        self.users = UserRepository(session)

    async def get(self, incident_id: UUID, expected_version: int | None = None) -> Incident:
        incident = await self.repository.get(incident_id)
        if incident is None:
            raise not_found("Incident")
        if expected_version is not None and incident.version != expected_version:
            raise version_conflict()
        return incident

    async def validate_service(self, service_id: UUID) -> None:
        if await self.services.get(service_id) is None:
            raise not_found("Service")

    async def validate_assignee(self, assignee_id: UUID | None) -> None:
        if assignee_id is None:
            return
        assignee = await self.users.get(assignee_id)
        if assignee is None:
            raise not_found("Assignee")
        if assignee.role == Role.VIEWER:
            raise DomainError(422, "invalid_assignee", "Assign incidents to an engineer or admin")

    async def record(
        self,
        incident: Incident,
        actor: User | SystemActor,
        event_type: EventType,
        details: dict[str, Any],
    ) -> Incident:
        # Flush first to execute the version-checked UPDATE and obtain its new version.
        # This is still the same transaction as the audit INSERT below.
        await self.session.flush()
        self.session.add(
            IncidentEvent(
                incident_id=incident.id,
                actor_id=actor.id if isinstance(actor, User) else None,
                system_actor=actor.name if isinstance(actor, SystemActor) else None,
                event_type=event_type,
                incident_version=incident.version,
                details=details,
            )
        )
        await self.session.flush()
        await self.session.refresh(incident)
        return incident

    async def create(
        self,
        data: IncidentCreate,
        actor: User | SystemActor,
        *,
        automation_context: dict[str, Any] | None = None,
    ) -> Incident:
        await self.validate_service(data.service_id)
        await self.validate_assignee(data.assignee_id)
        incident = Incident(**data.model_dump(), status=IncidentStatus.TRIGGERED)
        self.session.add(incident)
        details = data.model_dump(mode="json")
        if automation_context is not None:
            details["automation"] = automation_context
        return await self.record(incident, actor, EventType.CREATED, details)

    async def update(self, incident_id: UUID, data: IncidentUpdate, actor: User) -> Incident:
        incident = await self.get(incident_id, data.expected_version)
        updates = data.model_dump(exclude_unset=True, exclude={"expected_version"})
        if "service_id" in updates:
            await self.validate_service(updates["service_id"])
        changes = {}
        for field, value in updates.items():
            previous = getattr(incident, field)
            if previous != value:
                changes[field] = {"from": json_value(previous), "to": json_value(value)}
                setattr(incident, field, value)
        if not changes:
            raise DomainError(409, "no_change", "The requested update does not change the incident")
        return await self.record(incident, actor, EventType.UPDATED, {"changes": changes})

    async def assign(self, incident_id: UUID, data: AssignmentInput, actor: User) -> Incident:
        incident = await self.get(incident_id, data.expected_version)
        await self.validate_assignee(data.assignee_id)
        if incident.assignee_id == data.assignee_id:
            raise DomainError(409, "no_change", "Incident already has the requested assignee")
        previous = incident.assignee_id
        incident.assignee_id = data.assignee_id
        return await self.record(
            incident,
            actor,
            EventType.ASSIGNED,
            {"from": json_value(previous), "to": json_value(data.assignee_id)},
        )

    async def transition(self, incident_id: UUID, data: TransitionInput, actor: User) -> Incident:
        incident = await self.get(incident_id, data.expected_version)
        previous = incident.status
        if data.status not in TRANSITIONS[previous]:
            raise DomainError(
                409, "invalid_transition", f"Cannot transition from {previous} to {data.status}"
            )
        reopened = previous in {IncidentStatus.RESOLVED, IncidentStatus.CLOSED} and (
            data.status == IncidentStatus.INVESTIGATING
        )
        incident.status = data.status
        if reopened:
            incident.resolved_at = None
            incident.closed_at = None
        elif data.status == IncidentStatus.RESOLVED:
            incident.resolved_at = datetime.now(UTC)
        elif data.status == IncidentStatus.CLOSED:
            incident.closed_at = datetime.now(UTC)
        return await self.record(
            incident,
            actor,
            EventType.REOPENED if reopened else EventType.TRANSITIONED,
            {"from": previous, "to": data.status, "reason": data.reason},
        )

    async def list(self, filters: IncidentFilters) -> Page[IncidentRead]:
        items, total = await self.repository.list(filters)
        return Page[IncidentRead](
            items=items, total=total, limit=filters.limit, offset=filters.offset
        )

    async def events(self, incident_id: UUID, limit: int, offset: int) -> Page[EventRead]:
        await self.get(incident_id)
        items, total = await self.repository.events(incident_id, limit, offset)
        return Page[EventRead](items=items, total=total, limit=limit, offset=offset)
