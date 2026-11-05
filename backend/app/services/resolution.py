from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError, not_found
from app.db.clock import database_now
from app.investigation.safety import Sanitizer
from app.models import (
    Incident,
    Investigation,
    InvestigationEvidence,
    InvestigationReport,
    Role,
    Service,
    User,
)
from app.models.resolution import (
    ProposalStatus,
    ResolutionApproval,
    ResolutionEvent,
    ResolutionEvidence,
    ResolutionJob,
    ResolutionProposal,
    SyntheticServiceState,
)
from app.repositories.resolution import ResolutionRepository
from app.resolution.catalog import CATALOG, action_definition, digest
from app.resolution.simulator import scenario_snapshot
from app.schemas.common import Page
from app.schemas.resolution import (
    ProposalCreate,
    ProposalRead,
    ResolutionFilters,
    ReviewInput,
    ScenarioInput,
)

TERMINAL = {
    ProposalStatus.SUCCEEDED,
    ProposalStatus.FAILED,
    ProposalStatus.REJECTED,
    ProposalStatus.CANCELLED,
    ProposalStatus.EXPIRED,
}


def require_human(actor: User) -> None:
    if not isinstance(actor, User) or actor.role not in {Role.ADMIN, Role.ENGINEER}:
        raise DomainError(403, "forbidden", "An authenticated engineer or admin is required")


def approved_payload(proposal: ResolutionProposal) -> dict:
    return {
        key: str(getattr(proposal, key))
        if isinstance(getattr(proposal, key), UUID)
        else getattr(proposal, key)
        for key in (
            "incident_id",
            "investigation_id",
            "service_id",
            "state_id",
            "incident_version",
            "state_version",
            "action_id",
            "action_fingerprint",
            "parameters",
            "provenance",
            "explanation",
            "origin",
            "risk",
            "requested_by",
            "policy",
            "client_request_id",
        )
    }


async def record_event(
    session: AsyncSession,
    proposal: ResolutionProposal,
    event_type: str,
    *,
    actor: User | None = None,
    details: dict | None = None,
    correlation_id: UUID | None = None,
    initial: bool = False,
) -> None:
    if not initial:
        proposal.version += 1
    await session.flush()
    session.add(
        ResolutionEvent(
            proposal_id=proposal.id,
            event_type=event_type,
            actor_id=actor.id if actor else None,
            system_actor=None if actor else "resolution_worker",
            proposal_version=proposal.version,
            occurred_at=await database_now(session),
            details=details or {},
            correlation_id=correlation_id,
        )
    )
    await session.flush()
    await session.refresh(proposal)


def check_version(proposal: ResolutionProposal, expected: int) -> None:
    if proposal.version != expected:
        raise DomainError(409, "version_conflict", "Proposal changed; retrieve it and review again")


async def invalid_context(session: AsyncSession, proposal: ResolutionProposal) -> str | None:
    "Caller locks proposal first; incident and synthetic target are then locked in this order."
    incident = await session.scalar(
        select(Incident)
        .where(Incident.id == proposal.incident_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    state = await ResolutionRepository(session).state(proposal.service_id, lock=True)
    if (
        incident.status in {"resolved", "closed"}
        or incident.version != proposal.incident_version
        or incident.service_id != proposal.service_id
    ):
        return "incident_changed"
    if state.id != proposal.state_id or state.version != proposal.state_version:
        return "synthetic_state_changed"
    definition = CATALOG.get(proposal.action_id)
    if definition is None or definition.fingerprint != proposal.action_fingerprint:
        return "action_definition_changed"
    if digest(approved_payload(proposal)) != proposal.payload_hash:
        return "payload_changed"
    if proposal.investigation_id:
        report = await session.get(InvestigationReport, proposal.investigation_id)
        investigation = await session.get(Investigation, proposal.investigation_id)
        if (
            report is None
            or investigation is None
            or investigation.incident_id != proposal.incident_id
            or investigation.status not in {"completed", "inconclusive"}
            or digest(report.payload) != proposal.provenance.get("report_hash")
        ):
            return "investigation_changed"
        usable = set(
            await session.scalars(
                select(InvestigationEvidence.reference).where(
                    InvestigationEvidence.investigation_id == proposal.investigation_id,
                    InvestigationEvidence.quarantined.is_(False),
                    InvestigationEvidence.reference.in_(proposal.provenance["evidence_refs"]),
                )
            )
        )
        if usable != set(proposal.provenance["evidence_refs"]):
            return "investigation_evidence_changed"
    return None


class ResolutionService:
    def __init__(self, session: AsyncSession, settings: Settings):
        self.session, self.settings = session, settings
        self.repository = ResolutionRepository(session)
        self.sanitizer = Sanitizer.from_settings(settings)

    async def initialize(
        self, service_id: UUID, data: ScenarioInput, actor: User
    ) -> SyntheticServiceState:
        if not isinstance(actor, User) or actor.role != Role.ADMIN:
            raise DomainError(403, "forbidden", "Only admins initialize/reset synthetic scenarios")
        service = await self.session.scalar(
            select(Service).where(Service.id == service_id).with_for_update()
        )
        if service is None:
            raise not_found("Service")
        state = await self.session.scalar(
            select(SyntheticServiceState)
            .where(SyntheticServiceState.service_id == service_id)
            .with_for_update()
        )
        if (state.version if state else 0) != data.expected_version:
            raise DomainError(409, "version_conflict", "Synthetic state changed")
        snapshot = scenario_snapshot(data.scenario).model_dump(mode="json")
        if state is None:
            state = SyntheticServiceState(
                service_id=service_id, snapshot=snapshot, initialized_by=actor.id
            )
            self.session.add(state)
        else:
            state.snapshot = snapshot
            state.initialized_by = actor.id
            # A reset invalidates approvals even when restoring identical values.
            state.version += 1
        await self.session.flush()
        await self.session.refresh(state)
        return state

    async def create(
        self, incident_id: UUID, data: ProposalCreate, actor: User
    ) -> ResolutionProposal:
        require_human(actor)
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtext(current_schema()), "
                "hashtext('resolution-admission'))"
            )
        )
        existing = await self.session.scalar(
            select(ResolutionProposal).where(
                ResolutionProposal.requested_by == actor.id,
                ResolutionProposal.client_request_id == data.client_request_id,
            )
        )
        request_hash = digest(data.model_dump(mode="json") | {"incident_id": str(incident_id)})
        if existing:
            if existing.provenance.get("request_hash") != request_hash:
                raise DomainError(
                    409,
                    "idempotency_conflict",
                    "Client request ID was used for a different proposal",
                )
            return existing
        active = await self.session.scalar(
            select(func.count())
            .select_from(ResolutionProposal)
            .where(
                ResolutionProposal.status.not_in(TERMINAL),
                ResolutionProposal.requested_by == actor.id,
            )
        )
        if active >= 20:
            raise DomainError(
                429, "proposal_limit", "At most twenty active proposals per requester"
            )
        incident = await self.session.scalar(
            select(Incident).where(Incident.id == incident_id).with_for_update()
        )
        if incident is None:
            raise not_found("Incident")
        if incident.version != data.expected_incident_version or incident.status in {
            "resolved",
            "closed",
        }:
            raise DomainError(409, "stale_context", "Select a current unresolved incident")
        state = await self.repository.state(incident.service_id, lock=True)
        if (
            state.id != data.parameters.state_id
            or state.version != data.parameters.expected_state_version
        ):
            raise DomainError(
                409,
                "stale_context",
                "Synthetic target does not match the current incident service/state",
            )
        definition = action_definition(data.action_id)
        provenance = {"request_hash": request_hash, "evidence_refs": sorted(data.evidence_refs)}
        if data.investigation_id:
            investigation = await self.session.get(Investigation, data.investigation_id)
            report = await self.session.get(InvestigationReport, data.investigation_id)
            if (
                investigation is None
                or investigation.incident_id != incident.id
                or report is None
                or investigation.status not in {"completed", "inconclusive"}
            ):
                raise DomainError(
                    422,
                    "invalid_investigation",
                    "Select a completed/inconclusive investigation belonging to this incident",
                )
            refs = set(
                await self.session.scalars(
                    select(InvestigationEvidence.reference).where(
                        InvestigationEvidence.investigation_id == investigation.id,
                        InvestigationEvidence.quarantined.is_(False),
                        InvestigationEvidence.reference.in_(data.evidence_refs),
                    )
                )
            )
            if refs != set(data.evidence_refs):
                raise DomainError(
                    422,
                    "invalid_evidence_reference",
                    "Evidence must belong to this investigation and be usable",
                )
            provenance |= {
                "report_hash": digest(report.payload),
                "report_created_at": report.created_at.isoformat(),
            }
            if data.origin == "investigation":
                recommendations = report.payload["recommended_actions"]
                if data.recommendation_index >= len(recommendations) or not refs.intersection(
                    recommendations[data.recommendation_index]["evidence_ids"]
                ):
                    raise DomainError(
                        422,
                        "invalid_recommendation",
                        "Select an actual recommendation and its evidence",
                    )
                provenance |= {
                    "recommendation_index": data.recommendation_index,
                    "recommendation": recommendations[data.recommendation_index],
                }
        proposal = ResolutionProposal(
            id=uuid4(),
            incident_id=incident.id,
            investigation_id=data.investigation_id,
            service_id=incident.service_id,
            state_id=state.id,
            incident_version=incident.version,
            state_version=state.version,
            action_id=definition.id,
            action_fingerprint=definition.fingerprint,
            parameters=data.parameters.model_dump(mode="json"),
            provenance=provenance,
            explanation=self.sanitizer.text(data.explanation, 2000, quarantine=True),
            origin=data.origin,
            risk="synthetic_mutation" if definition.mutates_synthetic_state else "read_only",
            requested_by=actor.id,
            policy={
                "approval_required": True,
                "distinct_approver": definition.mutates_synthetic_state,
                "approval_ttl_seconds": self.settings.resolution_approval_ttl_seconds,
                "max_attempts": self.settings.resolution_max_attempts,
                "lease_seconds": self.settings.resolution_lease_seconds,
                "timeout_seconds": definition.timeout_seconds,
            },
            client_request_id=data.client_request_id,
            status=ProposalStatus.DRAFT,
            version=1,
        )
        proposal.payload_hash = digest(approved_payload(proposal))
        self.session.add(proposal)
        await self.session.flush()
        self.session.add_all(
            [
                ResolutionEvidence(
                    proposal_id=proposal.id, investigation_id=data.investigation_id, reference=ref
                )
                for ref in data.evidence_refs
            ]
        )
        await record_event(self.session, proposal, "proposal_created", actor=actor, initial=True)
        return proposal

    async def submit(self, proposal_id: UUID, expected: int, actor: User) -> ResolutionProposal:
        require_human(actor)
        proposal = await self.repository.proposal(proposal_id, lock=True)
        if actor.id != proposal.requested_by and actor.role != Role.ADMIN:
            raise DomainError(403, "forbidden", "Only the proposer or admin can submit")
        check_version(proposal, expected)
        if proposal.status != ProposalStatus.DRAFT:
            raise DomainError(409, "invalid_transition", "Only a draft can be submitted")
        if code := await invalid_context(self.session, proposal):
            raise DomainError(409, "stale_context", code)
        proposal.status = ProposalStatus.PENDING_APPROVAL
        proposal.approval_deadline = await database_now(self.session) + timedelta(
            seconds=proposal.policy["approval_ttl_seconds"]
        )
        await record_event(self.session, proposal, "proposal_submitted", actor=actor)
        return proposal

    async def expire(
        self,
        proposal: ResolutionProposal,
        job: ResolutionJob | None = None,
        code: str = "approval_expired",
    ) -> None:
        proposal.status = ProposalStatus.EXPIRED
        proposal.error_code = code
        if job:
            job.status = "expired"
            job.lease_token = job.lease_expires_at = None
            job.finished_at = await database_now(self.session)
            job.last_error_code = code
        await record_event(self.session, proposal, "approval_expired", details={"reason": code})

    async def review(
        self, proposal_id: UUID, data: ReviewInput, actor: User, decision: str
    ) -> ResolutionProposal:
        require_human(actor)
        proposal = await self.repository.proposal(proposal_id, lock=True)
        check_version(proposal, data.expected_version)
        if proposal.status != ProposalStatus.PENDING_APPROVAL:
            raise DomainError(409, "invalid_transition", "Only a pending proposal can be reviewed")
        if proposal.policy["distinct_approver"] and actor.id == proposal.requested_by:
            raise DomainError(
                403,
                "separation_of_duties",
                "A different engineer or admin must review synthetic changes",
            )
        if await database_now(self.session) >= proposal.approval_deadline:
            await self.expire(proposal)
            return proposal
        if code := await invalid_context(self.session, proposal):
            await self.expire(proposal, code=code)
            return proposal
        approval = ResolutionApproval(
            id=uuid4(),
            proposal_id=proposal.id,
            actor_id=actor.id,
            decision=decision,
            payload_hash=proposal.payload_hash,
            snapshot=approved_payload(proposal),
            reason=self.sanitizer.text(data.reason, 1000, quarantine=True),
        )
        self.session.add(approval)
        await self.session.flush()
        if decision == "rejected":
            proposal.status = ProposalStatus.REJECTED
            await record_event(
                self.session, proposal, "approval_rejected", actor=actor, correlation_id=approval.id
            )
        else:
            # Approval and queue insertion commit together; approved is an audit event.
            await record_event(
                self.session, proposal, "approval_granted", actor=actor, correlation_id=approval.id
            )
            proposal.status = ProposalStatus.QUEUED
            # The job INSERT trigger must see the queued row within this transaction.
            await self.session.flush()
            self.session.add(
                ResolutionJob(
                    id=uuid4(),
                    proposal_id=proposal.id,
                    approval_id=approval.id,
                    status="pending",
                    attempts=0,
                    max_attempts=proposal.policy["max_attempts"],
                    available_at=await database_now(self.session),
                )
            )
            await record_event(
                self.session, proposal, "execution_queued", actor=actor, correlation_id=approval.id
            )
        return proposal

    async def cancel(self, proposal_id: UUID, expected: int, actor: User) -> ResolutionProposal:
        require_human(actor)
        job = await self.repository.job(proposal_id, lock=True)
        proposal = await self.repository.proposal(proposal_id, lock=True)
        if actor.id != proposal.requested_by and actor.role != Role.ADMIN:
            raise DomainError(403, "forbidden", "Only the proposer or admin can cancel")
        check_version(proposal, expected)
        if proposal.status in TERMINAL:
            raise DomainError(409, "invalid_transition", "Terminal proposals cannot be cancelled")
        proposal.status = ProposalStatus.CANCELLED
        if job:
            job.status = "cancelled"
            job.lease_token = job.lease_expires_at = None
            job.finished_at = await database_now(self.session)
        await record_event(self.session, proposal, "proposal_cancelled", actor=actor)
        return proposal

    async def list(self, incident_id: UUID, filters: ResolutionFilters) -> Page[ProposalRead]:
        if await self.session.get(Incident, incident_id) is None:
            raise not_found("Incident")
        clauses = [ResolutionProposal.incident_id == incident_id]
        if filters.status:
            clauses.append(ResolutionProposal.status == filters.status)
        total = await self.session.scalar(
            select(func.count()).select_from(ResolutionProposal).where(*clauses)
        )
        rows = await self.session.scalars(
            select(ResolutionProposal)
            .where(*clauses)
            .order_by(ResolutionProposal.created_at, ResolutionProposal.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
        return Page[ProposalRead](
            items=list(rows), total=total, limit=filters.limit, offset=filters.offset
        )
