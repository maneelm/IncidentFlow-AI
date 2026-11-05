from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select

from app.api.dependencies import Admin, AppSettings, CurrentUser, Session, Writer
from app.core.errors import DomainError, not_found
from app.models.resolution import ResolutionApproval, ResolutionEvent, ResolutionExecution
from app.resolution.catalog import CATALOG, action_definition
from app.schemas.common import Page
from app.schemas.resolution import (
    ApprovalRead,
    CatalogRead,
    ExecutionRead,
    JobRead,
    ProposalCreate,
    ProposalRead,
    ResolutionEventRead,
    ResolutionFilters,
    ReviewInput,
    ScenarioInput,
    SyntheticStateRead,
    VersionInput,
)
from app.services.incident_timeline import TimelinePage, incident_timeline
from app.services.resolution import ResolutionService

router = APIRouter(tags=["resolution workflows"])


def resolution_service(session: Session, settings: AppSettings) -> ResolutionService:
    return ResolutionService(session, settings)


Resolutions = Annotated[ResolutionService, Depends(resolution_service)]


@router.get("/action-catalog", response_model=list[CatalogRead])
async def catalog(user: CurrentUser):
    return [definition.public() for definition in CATALOG.values()]


@router.get("/action-catalog/{action_id}", response_model=CatalogRead)
async def action(action_id: str, user: CurrentUser):
    return action_definition(action_id).public()


@router.get("/services/{service_id}/synthetic-state", response_model=SyntheticStateRead)
async def synthetic_state(service_id: UUID, service: Resolutions, user: CurrentUser):
    return await service.repository.state(service_id)


@router.post("/services/{service_id}/synthetic-state", response_model=SyntheticStateRead)
async def initialize(service_id: UUID, data: ScenarioInput, service: Resolutions, actor: Admin):
    return await service.initialize(service_id, data, actor)


@router.post(
    "/incidents/{incident_id}/resolution-proposals", response_model=ProposalRead, status_code=201
)
async def create(
    incident_id: UUID, data: ProposalCreate, response: Response, service: Resolutions, actor: Writer
):
    result = await service.create(incident_id, data, actor)
    response.headers["Location"] = f"/api/v1/resolution-proposals/{result.id}"
    return result


@router.get("/incidents/{incident_id}/resolution-proposals", response_model=Page[ProposalRead])
async def list_proposals(
    incident_id: UUID,
    filters: Annotated[ResolutionFilters, Query()],
    service: Resolutions,
    user: CurrentUser,
):
    return await service.list(incident_id, filters)


@router.get("/resolution-proposals/{proposal_id}", response_model=ProposalRead)
async def get(proposal_id: UUID, service: Resolutions, user: CurrentUser):
    return await service.repository.proposal(proposal_id)


@router.post("/resolution-proposals/{proposal_id}/submit", response_model=ProposalRead)
async def submit(proposal_id: UUID, data: VersionInput, service: Resolutions, actor: Writer):
    return await service.submit(proposal_id, data.expected_version, actor)


@router.post("/resolution-proposals/{proposal_id}/approve", response_model=ProposalRead)
async def approve(proposal_id: UUID, data: ReviewInput, service: Resolutions, actor: Writer):
    return await service.review(proposal_id, data, actor, "approved")


@router.post("/resolution-proposals/{proposal_id}/reject", response_model=ProposalRead)
async def reject(proposal_id: UUID, data: ReviewInput, service: Resolutions, actor: Writer):
    return await service.review(proposal_id, data, actor, "rejected")


@router.post("/resolution-proposals/{proposal_id}/cancel", response_model=ProposalRead)
async def cancel(proposal_id: UUID, data: VersionInput, service: Resolutions, actor: Writer):
    return await service.cancel(proposal_id, data.expected_version, actor)


@router.get("/resolution-proposals/{proposal_id}/approval", response_model=ApprovalRead)
async def approval(proposal_id: UUID, service: Resolutions, user: CurrentUser):
    await service.repository.proposal(proposal_id)
    row = await service.session.scalar(
        select(ResolutionApproval).where(ResolutionApproval.proposal_id == proposal_id)
    )
    if row is None:
        raise not_found("Approval decision")
    return row


@router.get("/resolution-proposals/{proposal_id}/execution", response_model=JobRead)
async def execution(proposal_id: UUID, service: Resolutions, user: CurrentUser):
    await service.repository.proposal(proposal_id)
    row = await service.repository.job(proposal_id)
    if row is None:
        raise not_found("Resolution job")
    return row


@router.get("/resolution-proposals/{proposal_id}/result", response_model=ExecutionRead)
async def result(proposal_id: UUID, service: Resolutions, user: CurrentUser):
    await service.repository.proposal(proposal_id)
    row = await service.session.scalar(
        select(ResolutionExecution).where(ResolutionExecution.proposal_id == proposal_id)
    )
    if row is None:
        raise DomainError(409, "result_unavailable", "No committed execution result is available")
    return row


@router.get("/resolution-proposals/{proposal_id}/events", response_model=Page[ResolutionEventRead])
async def events(
    proposal_id: UUID,
    service: Resolutions,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    await service.repository.proposal(proposal_id)
    total = await service.session.scalar(
        select(func.count())
        .select_from(ResolutionEvent)
        .where(ResolutionEvent.proposal_id == proposal_id)
    )
    items = await service.session.scalars(
        select(ResolutionEvent)
        .where(ResolutionEvent.proposal_id == proposal_id)
        .order_by(ResolutionEvent.proposal_version)
        .limit(limit)
        .offset(offset)
    )
    return Page[ResolutionEventRead](items=list(items), total=total, limit=limit, offset=offset)


@router.get("/incidents/{incident_id}/timeline", response_model=TimelinePage)
async def timeline(
    incident_id: UUID,
    session: Session,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    cursor: str | None = Query(None, max_length=1500),
):
    return await incident_timeline(session, incident_id, limit, cursor)
