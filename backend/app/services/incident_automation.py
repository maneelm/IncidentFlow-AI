from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import SystemActor
from app.models import DetectionEvidence, DetectionOccurrence, DetectionRule
from app.schemas.detection import RuleRead
from app.schemas.incidents import IncidentCreate
from app.services.incidents import IncidentService


class IncidentAutomation:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def correlate(self, occurrence_id: UUID, log_ids: list[UUID]) -> None:
        if log_ids:
            await self.session.execute(
                insert(DetectionEvidence)
                .values([{"occurrence_id": occurrence_id, "log_id": log_id} for log_id in log_ids])
                .on_conflict_do_nothing(index_elements=["occurrence_id", "log_id"])
            )

    async def trigger(
        self, rule: DetectionRule, start: datetime, end: datetime, witnesses: list[UUID]
    ) -> DetectionOccurrence:
        occurrence_id = uuid4()
        snapshot = RuleRead.model_validate(rule).model_dump(mode="json")
        context = {
            "detection_id": str(occurrence_id),
            "rule_id": str(rule.id),
            "rule_version": rule.version,
            "detection_key": f"{rule.service_id}:{rule.id}",
            "matching_count": len(witnesses),
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "time_basis": "ingested_at",
            "rule": snapshot,
        }
        incident = await IncidentService(self.session).create(
            IncidentCreate(
                title=f"Detected: {rule.name}",
                description=(
                    f"At least {len(witnesses)} matching {rule.level} logs received within "
                    f"{rule.window_seconds} seconds. Detection {occurrence_id}. "
                    "Review linked evidence before taking action."
                ),
                severity=rule.incident_severity,
                service_id=rule.service_id,
            ),
            SystemActor("detection_worker"),
            automation_context=context,
        )
        occurrence = DetectionOccurrence(
            id=occurrence_id,
            rule_id=rule.id,
            service_id=rule.service_id,
            incident_id=incident.id,
            is_current=True,
            rule_version=rule.version,
            rule_snapshot=snapshot,
            window_start=start,
            window_end=end,
            matching_count=len(witnesses),
        )
        self.session.add(occurrence)
        await self.session.flush()
        await self.correlate(occurrence.id, witnesses)
        return occurrence
