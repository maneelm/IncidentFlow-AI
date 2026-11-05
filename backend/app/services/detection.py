from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DetectionRule, Incident, LogEntry
from app.repositories.detection import DetectionRepository
from app.services.detection_rules import ACTIVE_STATUSES
from app.services.incident_automation import IncidentAutomation


class DetectionEngine:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = DetectionRepository(session)
        self.automation = IncidentAutomation(session)

    async def evaluate(self, log: LogEntry) -> None:
        for rule in await self.repository.eligible_rules(log):
            if rule.level != log.level or (
                rule.error_code is not None and rule.error_code != log.error_code
            ):
                continue
            await self.evaluate_rule(rule, log)

    async def evaluate_rule(self, rule: DetectionRule, log: LogEntry) -> None:
        current = await self.repository.current(rule.id)
        if current:
            # Coordinate lifecycle reads with human transitions without changing
            # the incident's version, status, assignee, or immutable history.
            incident = await self.session.scalar(
                select(Incident).where(Incident.id == current.incident_id).with_for_update()
            )
            if incident.status in ACTIVE_STATUSES:
                if log.ingested_at > current.window_start:
                    await self.automation.correlate(current.id, [log.id])
                return
            terminal_at = incident.closed_at or incident.resolved_at
            current.is_current = False
            current.retired_at = terminal_at
            boundary = terminal_at + timedelta(seconds=rule.cooldown_seconds)
            rule.eligible_after = max(rule.eligible_after or boundary, boundary)
            await self.session.flush()
        start = max(log.ingested_at - timedelta(seconds=rule.window_seconds), rule.effective_from)
        if rule.eligible_after:
            start = max(start, rule.eligible_after)
        if log.ingested_at <= start:
            return
        witnesses = await self.repository.witnesses(rule, start, log.ingested_at)
        if len(witnesses) >= rule.threshold:
            await self.automation.trigger(rule, start, log.ingested_at, witnesses)
