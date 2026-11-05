from __future__ import annotations

from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Incident, IncidentEvent
from app.schemas.incidents import IncidentFilters


class IncidentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, incident_id: UUID) -> Incident | None:
        return await self.session.get(Incident, incident_id)

    async def list(self, filters: IncidentFilters) -> tuple[list[Incident], int]:
        conditions = []
        for field in ("status", "severity", "service_id", "assignee_id"):
            value = getattr(filters, field)
            if value is not None:
                conditions.append(getattr(Incident, field) == value)
        if filters.unassigned:
            conditions.append(Incident.assignee_id.is_(None))
        if filters.created_from:
            conditions.append(Incident.created_at >= filters.created_from)
        if filters.created_to:
            conditions.append(Incident.created_at <= filters.created_to)
        total = await self.session.scalar(
            select(func.count()).select_from(Incident).where(*conditions)
        )
        columns = {
            "created_at": Incident.created_at,
            "updated_at": Incident.updated_at,
            "incident_number": Incident.number,
            "severity": case(
                {"low": 1, "medium": 2, "high": 3, "critical": 4}, value=Incident.severity
            ),
        }
        column = columns[filters.sort_by]
        sort = column.asc() if filters.sort_order == "asc" else column.desc()
        result = await self.session.scalars(
            select(Incident)
            .where(*conditions)
            .order_by(sort, Incident.id.asc())
            .limit(filters.limit)
            .offset(filters.offset)
        )
        return list(result), total or 0

    async def events(
        self, incident_id: UUID, limit: int, offset: int
    ) -> tuple[list[IncidentEvent], int]:
        predicate = IncidentEvent.incident_id == incident_id
        total = await self.session.scalar(
            select(func.count()).select_from(IncidentEvent).where(predicate)
        )
        result = await self.session.scalars(
            select(IncidentEvent)
            .where(predicate)
            .order_by(IncidentEvent.incident_version)
            .limit(limit)
            .offset(offset)
        )
        return list(result), total or 0
