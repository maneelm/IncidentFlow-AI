from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models import DetectionJob, DetectionOccurrence, DetectionRule, LogEntry
from app.models.enums import JobStatus


class DetectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def current(self, rule_id: UUID) -> DetectionOccurrence | None:
        return await self.session.scalar(
            select(DetectionOccurrence).where(
                DetectionOccurrence.rule_id == rule_id, DetectionOccurrence.is_current.is_(True)
            )
        )

    async def eligible_rules(self, log: LogEntry) -> list[DetectionRule]:
        result = await self.session.scalars(
            select(DetectionRule)
            .where(
                DetectionRule.service_id == log.service_id,
                DetectionRule.enabled.is_(True),
                DetectionRule.effective_from < log.ingested_at,
            )
            .order_by(DetectionRule.id)
            .with_for_update()
        )
        return list(result)

    async def witnesses(self, rule: DetectionRule, start: datetime, end: datetime) -> list[UUID]:
        conditions = [
            LogEntry.service_id == rule.service_id,
            LogEntry.level == rule.level,
            LogEntry.ingested_at > start,
            LogEntry.ingested_at <= end,
        ]
        if rule.error_code is not None:
            conditions.append(LogEntry.error_code == rule.error_code)
        result = await self.session.scalars(
            select(LogEntry.id)
            .where(*conditions)
            .order_by(LogEntry.ingested_at, LogEntry.source_event_id, LogEntry.id)
            .limit(rule.threshold)
        )
        return list(result)

    async def page(
        self,
        model: type,
        limit: int,
        offset: int,
        *,
        service_id: UUID | None = None,
        rule_id: UUID | None = None,
        enabled: bool | None = None,
        status: JobStatus | None = None,
    ) -> tuple[list, int]:
        conditions = []
        for key, value in {
            "service_id": service_id,
            "rule_id": rule_id,
            "enabled": enabled,
            "status": status,
        }.items():
            if value is not None:
                conditions.append(getattr(model, key) == value)
        total = await self.session.scalar(
            select(func.count()).select_from(model).where(*conditions)
        )
        items = await self.session.scalars(
            select(model)
            .where(*conditions)
            .order_by(model.created_at.desc(), model.id)
            .limit(limit)
            .offset(offset)
        )
        return list(items), total or 0


class JobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def next_claimable(self, now: datetime) -> DetectionJob | None:
        earlier = aliased(DetectionJob)
        # Only the first unfinished job of each service is eligible. SKIP LOCKED
        # then allows other services to progress without reordering one stream.
        blocked = (
            select(earlier.id)
            .where(
                earlier.service_id == DetectionJob.service_id,
                earlier.position < DetectionJob.position,
                earlier.status.in_([JobStatus.PENDING, JobStatus.PROCESSING]),
            )
            .exists()
        )
        return await self.session.scalar(
            select(DetectionJob)
            .where(
                ~blocked,
                or_(
                    and_(
                        DetectionJob.status == JobStatus.PENDING, DetectionJob.available_at <= now
                    ),
                    and_(
                        DetectionJob.status == JobStatus.PROCESSING,
                        DetectionJob.lease_expires_at <= now,
                    ),
                ),
            )
            .order_by(DetectionJob.position)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
