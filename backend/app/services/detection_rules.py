from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, not_found
from app.db.clock import database_now
from app.models import DetectionRule, Incident, IncidentStatus, Service
from app.repositories.detection import DetectionRepository
from app.schemas.common import Page
from app.schemas.detection import RuleCreate, RuleRead, RuleUpdate

MAX_RULES_PER_SERVICE = 25
ACTIVE_STATUSES = {IncidentStatus.TRIGGERED, IncidentStatus.INVESTIGATING, IncidentStatus.MITIGATED}


class RuleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = DetectionRepository(session)

    async def get(self, rule_id: UUID, *, lock: bool = False) -> DetectionRule:
        query = select(DetectionRule).where(DetectionRule.id == rule_id)
        if lock:
            query = query.with_for_update()
        rule = await self.session.scalar(query)
        if rule is None:
            raise not_found("Detection rule")
        return rule

    async def create(self, data: RuleCreate) -> DetectionRule:
        service = await self.session.scalar(
            select(Service).where(Service.id == data.service_id).with_for_update()
        )
        if service is None:
            raise not_found("Service")
        count = await self.session.scalar(
            select(func.count())
            .select_from(DetectionRule)
            .where(DetectionRule.service_id == data.service_id)
        )
        if count >= MAX_RULES_PER_SERVICE:
            raise DomainError(409, "rule_limit", "At most 25 rules may exist for one service")
        rule = DetectionRule(**data.model_dump(), effective_from=await database_now(self.session))
        self.session.add(rule)
        await self.session.flush()
        return rule

    async def update(self, rule_id: UUID, data: RuleUpdate) -> DetectionRule:
        rule = await self.get(rule_id, lock=True)
        if rule.version != data.expected_version:
            raise DomainError(409, "version_conflict", "Rule changed; retrieve it and retry")
        changes = {
            key: value
            for key, value in data.model_dump(
                exclude_unset=True, exclude={"expected_version"}
            ).items()
            if getattr(rule, key) != value
        }
        if not changes:
            raise DomainError(409, "no_change", "Rule already has the requested values")
        config_changes = changes.keys() - {"name", "enabled"}
        current = await self.repository.current(rule.id)
        if config_changes and current:
            incident = await self.session.scalar(
                select(Incident).where(Incident.id == current.incident_id).with_for_update()
            )
            if incident.status in ACTIVE_STATUSES:
                raise DomainError(
                    409,
                    "active_detection",
                    "Resolve the current incident before changing rule criteria",
                )
        for key, value in changes.items():
            setattr(rule, key, value)
        if changes.keys() - {"name"}:
            rule.effective_from = await database_now(self.session)
        await self.session.flush()
        await self.session.refresh(rule)
        return rule

    async def list(
        self, limit: int, offset: int, service_id: UUID | None, enabled: bool | None
    ) -> Page[RuleRead]:
        items, total = await self.repository.page(
            DetectionRule, limit, offset, service_id=service_id, enabled=enabled
        )
        return Page[RuleRead](items=items, total=total, limit=limit, offset=offset)
