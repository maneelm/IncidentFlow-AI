from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError, not_found
from app.db.clock import database_now
from app.investigation.providers import provider_available
from app.models import Incident, User
from app.models.investigations import (
    Investigation,
    InvestigationEvidence,
    InvestigationJob,
    InvestigationJobStatus,
    InvestigationReport,
    InvestigationStatus,
    InvestigationStep,
)
from app.schemas.common import Page
from app.schemas.investigations import (
    EvidenceRead,
    InvestigationCreate,
    InvestigationRead,
    ReportRead,
    StepRead,
)

ACTIVE = [InvestigationStatus.QUEUED, InvestigationStatus.RUNNING]


class InvestigationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session, self.settings = session, settings

    async def get(self, investigation_id: UUID) -> Investigation:
        investigation = await self.session.get(Investigation, investigation_id)
        if investigation is None:
            raise not_found("Investigation")
        return investigation

    async def create(
        self, incident_id: UUID, data: InvestigationCreate, actor: User
    ) -> Investigation:
        if await self.session.get(Incident, incident_id) is None:
            raise not_found("Incident")
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext(current_schema()), hashtext"
                "('investigation-admission'))"
            )
        )
        existing = await self.session.scalar(
            select(Investigation).where(
                Investigation.requested_by == actor.id,
                Investigation.client_request_id == data.client_request_id,
            )
        )
        if existing:
            if existing.incident_id != incident_id:
                raise DomainError(
                    409, "request_id_conflict", "Request ID belongs to another incident"
                )
            return existing
        provider = self.settings.investigation_llm_provider
        model = self.settings.investigation_llm_model or ("fake-v1" if provider == "fake" else "")
        if not provider_available(provider, model, self.settings):
            raise DomainError(
                503,
                "investigation_provider_unavailable",
                "Configure the investigation provider before requesting AI work",
            )
        current = await self.session.scalar(
            select(Investigation.id).where(
                Investigation.incident_id == incident_id, Investigation.status.in_(ACTIVE)
            )
        )
        if current:
            raise DomainError(
                409, "investigation_active", "This incident already has an active investigation"
            )
        total = await self.session.scalar(
            select(func.count()).select_from(Investigation).where(Investigation.status.in_(ACTIVE))
        )
        own = await self.session.scalar(
            select(func.count())
            .select_from(Investigation)
            .where(Investigation.requested_by == actor.id, Investigation.status.in_(ACTIVE))
        )
        if total >= 20 or own >= 5:
            raise DomainError(
                429,
                "investigation_limit",
                "Too many active investigations; wait or cancel existing work",
            )
        policy = {
            "max_calls": self.settings.investigation_max_llm_calls,
            "max_input_tokens": self.settings.investigation_max_input_tokens,
            "max_output_tokens": self.settings.investigation_max_output_tokens,
            "timeout_seconds": self.settings.investigation_llm_timeout_seconds,
            "duration_seconds": self.settings.investigation_max_duration_seconds,
            "lease_seconds": self.settings.investigation_lease_seconds,
            "max_logs": self.settings.investigation_max_logs,
            "max_knowledge": self.settings.investigation_max_knowledge,
        }
        investigation = Investigation(
            incident_id=incident_id,
            requested_by=actor.id,
            client_request_id=data.client_request_id,
            status=InvestigationStatus.QUEUED,
            provider=provider,
            model=model,
            stage="queued",
            checkpoint={},
            policy=policy,
        )
        self.session.add(investigation)
        await self.session.flush()
        self.session.add(
            InvestigationJob(
                investigation_id=investigation.id,
                status=InvestigationJobStatus.PENDING,
                available_at=await database_now(self.session),
                max_attempts=self.settings.investigation_max_attempts,
            )
        )
        await self.session.flush()
        await self.session.refresh(investigation)
        return investigation

    async def cancel(self, investigation_id: UUID, actor: User) -> Investigation:
        job = await self.session.scalar(
            select(InvestigationJob)
            .where(InvestigationJob.investigation_id == investigation_id)
            .with_for_update()
        )
        investigation = await self.get(investigation_id)
        if investigation.status == InvestigationStatus.CANCELLED:
            return investigation
        if investigation.status not in ACTIVE:
            raise DomainError(
                409, "investigation_terminal", "A finished investigation cannot be cancelled"
            )
        now = await database_now(self.session)
        investigation.status = InvestigationStatus.CANCELLED
        investigation.cancelled_by = actor.id
        investigation.completed_at = now
        investigation.error_code = "cancelled"
        investigation.checkpoint = investigation.checkpoint | {
            "status": "cancelled",
            "error_code": "cancelled",
        }
        job.status = InvestigationJobStatus.CANCELLED
        job.finished_at = now
        job.lease_token = None
        job.lease_expires_at = None
        await self.session.flush()
        await self.session.refresh(investigation)
        return investigation

    async def list(self, incident_id: UUID, limit: int, offset: int) -> Page[InvestigationRead]:
        if await self.session.get(Incident, incident_id) is None:
            raise not_found("Incident")
        predicate = Investigation.incident_id == incident_id
        total = await self.session.scalar(
            select(func.count()).select_from(Investigation).where(predicate)
        )
        items = await self.session.scalars(
            select(Investigation)
            .where(predicate)
            .order_by(Investigation.created_at.desc(), Investigation.id)
            .limit(limit)
            .offset(offset)
        )
        return Page[InvestigationRead](
            items=list(items), total=total or 0, limit=limit, offset=offset
        )

    async def evidence(self, investigation_id: UUID, limit: int, offset: int) -> Page[EvidenceRead]:
        await self.get(investigation_id)
        predicate = InvestigationEvidence.investigation_id == investigation_id
        total = await self.session.scalar(
            select(func.count()).select_from(InvestigationEvidence).where(predicate)
        )
        items = await self.session.scalars(
            select(InvestigationEvidence)
            .where(predicate)
            .order_by(InvestigationEvidence.reference)
            .limit(limit)
            .offset(offset)
        )
        return Page[EvidenceRead](items=list(items), total=total or 0, limit=limit, offset=offset)

    async def steps(self, investigation_id: UUID, limit: int, offset: int) -> Page[StepRead]:
        await self.get(investigation_id)
        predicate = InvestigationStep.investigation_id == investigation_id
        total = await self.session.scalar(
            select(func.count()).select_from(InvestigationStep).where(predicate)
        )
        items = await self.session.scalars(
            select(InvestigationStep)
            .where(predicate)
            .order_by(InvestigationStep.created_at, InvestigationStep.id)
            .limit(limit)
            .offset(offset)
        )
        return Page[StepRead](items=list(items), total=total or 0, limit=limit, offset=offset)

    async def report(self, investigation_id: UUID) -> ReportRead:
        await self.get(investigation_id)
        report = await self.session.get(InvestigationReport, investigation_id)
        if report is None:
            raise DomainError(409, "report_not_ready", "This investigation has no completed report")
        return ReportRead.model_validate(report.payload)
