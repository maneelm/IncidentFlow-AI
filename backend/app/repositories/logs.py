from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DetectionEvidence, DetectionOccurrence, LogEntry
from app.schemas.logs import LogFilters


class LogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, log_id: UUID) -> LogEntry | None:
        return await self.session.get(LogEntry, log_id)

    async def list(self, filters: LogFilters) -> tuple[list[LogEntry], int]:
        conditions = []
        for key in ("service_id", "level", "trace_id", "request_id"):
            if (value := getattr(filters, key)) is not None:
                conditions.append(getattr(LogEntry, key) == value)
        if filters.timestamp_from:
            conditions.append(LogEntry.timestamp >= filters.timestamp_from)
        if filters.timestamp_to:
            conditions.append(LogEntry.timestamp <= filters.timestamp_to)
        if filters.search:
            config = literal_column("'simple'::regconfig")
            conditions.append(
                func.to_tsvector(config, LogEntry.message).bool_op("@@")(
                    func.plainto_tsquery(config, filters.search)
                )
            )
        if filters.incident_id or filters.detection_id:
            evidence = (
                select(DetectionEvidence.log_id)
                .join(
                    DetectionOccurrence, DetectionOccurrence.id == DetectionEvidence.occurrence_id
                )
                .where(DetectionEvidence.log_id == LogEntry.id)
            )
            if filters.incident_id:
                evidence = evidence.where(DetectionOccurrence.incident_id == filters.incident_id)
            if filters.detection_id:
                evidence = evidence.where(DetectionOccurrence.id == filters.detection_id)
            conditions.append(evidence.exists())
        total = await self.session.scalar(
            select(func.count()).select_from(LogEntry).where(*conditions)
        )
        order = (
            LogEntry.timestamp.asc() if filters.sort_order == "asc" else LogEntry.timestamp.desc()
        )
        result = await self.session.scalars(
            select(LogEntry)
            .where(*conditions)
            .order_by(order, LogEntry.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        return list(result), total or 0
