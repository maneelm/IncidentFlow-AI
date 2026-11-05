"""Authenticated bounded read models for the operations dashboard."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import Field
from sqlalchemy import func, select, text

from app.api.dependencies import CurrentUser, Session
from app.core.errors import not_found
from app.db.clock import database_now
from app.models import DetectionOccurrence, Incident, Investigation, Role, Service, User
from app.models.resolution import ProposalStatus, ResolutionProposal
from app.schemas.common import InputModel, Page
from app.schemas.detection import DetectionRead
from app.schemas.investigations import InvestigationRead
from app.schemas.resolution import ProposalRead

router = APIRouter(tags=["dashboard read models"])


class CountGroup(InputModel):
    name: str
    count: int


class ActivityRead(InputModel):
    incident_id: UUID
    source_id: UUID
    source_type: str
    event_type: str
    occurred_at: datetime


class OverviewRead(InputModel):
    observed_at: datetime
    total_incidents: int
    active_incidents: int
    total_services: int
    pending_approvals: int
    severity: list[CountGroup]
    statuses: list[CountGroup]
    proposal_statuses: list[CountGroup]
    recent_detections: list[DetectionRead]
    recent_investigations: list[InvestigationRead]
    approval_queue: list[ProposalRead]
    recent_activity: list[ActivityRead]


class ActivityFilters(InputModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)
    incident_id: UUID | None = None
    status: str | None = Field(default=None, max_length=30)


@router.get("/dashboard/overview", response_model=OverviewRead)
async def overview(session: Session, user: CurrentUser):
    async def grouped(column):
        rows = (await session.execute(select(column, func.count()).group_by(column))).all()
        return [CountGroup(name=str(name), count=count) for name, count in rows]

    statuses = await grouped(Incident.status)
    proposals = await grouped(ResolutionProposal.status)
    return OverviewRead(
        observed_at=await database_now(session),
        total_incidents=sum(item.count for item in statuses),
        active_incidents=sum(
            item.count for item in statuses if item.name not in {"resolved", "closed"}
        ),
        total_services=await session.scalar(select(func.count()).select_from(Service)),
        pending_approvals=sum(item.count for item in proposals if item.name == "pending_approval"),
        severity=await grouped(Incident.severity),
        statuses=statuses,
        proposal_statuses=proposals,
        recent_detections=list(
            await session.scalars(
                select(DetectionOccurrence)
                .order_by(DetectionOccurrence.created_at.desc(), DetectionOccurrence.id)
                .limit(8)
            )
        ),
        recent_investigations=list(
            await session.scalars(
                select(Investigation)
                .order_by(Investigation.created_at.desc(), Investigation.id)
                .limit(8)
            )
        ),
        approval_queue=list(
            await session.scalars(
                select(ResolutionProposal)
                .where(ResolutionProposal.status == ProposalStatus.PENDING_APPROVAL)
                .order_by(ResolutionProposal.created_at, ResolutionProposal.id)
                .limit(8)
            )
        ),
        recent_activity=[
            ActivityRead.model_validate(dict(row))
            for row in (
                await session.execute(
                    text("""
            SELECT * FROM (
                SELECT incident_id,id AS source_id,'incident_event' AS source_type,
                    event_type,occurred_at FROM incident_events
                UNION ALL
                SELECT p.incident_id,e.id,'resolution_event',e.event_type,e.occurred_at
                FROM resolution_events e JOIN resolution_proposals p ON p.id=e.proposal_id
                UNION ALL
                SELECT incident_id,id,'detection','detection_created',created_at
                FROM detection_occurrences
                UNION ALL
                SELECT incident_id,id,'investigation','investigation_requested',created_at
                FROM investigations
            ) activity ORDER BY occurred_at DESC,source_type,source_id LIMIT 12
        """)
                )
            ).mappings()
        ],
    )


async def activity_page(session, model, schema, filters):
    clauses = []
    if filters.incident_id:
        clauses.append(model.incident_id == filters.incident_id)
    if filters.status:
        clauses.append(model.status == filters.status)
    count = await session.scalar(select(func.count()).select_from(model).where(*clauses))
    rows = list(
        await session.scalars(
            select(model)
            .where(*clauses)
            .order_by(model.created_at.desc(), model.id)
            .limit(filters.limit)
            .offset(filters.offset)
        )
    )
    return Page[schema](items=rows, total=count, limit=filters.limit, offset=filters.offset)


@router.get("/dashboard/investigations", response_model=Page[InvestigationRead])
async def investigations(
    filters: Annotated[ActivityFilters, Query()], session: Session, user: CurrentUser
):
    return await activity_page(session, Investigation, InvestigationRead, filters)


@router.get("/dashboard/resolution-proposals", response_model=Page[ProposalRead])
async def proposals(
    filters: Annotated[ActivityFilters, Query()], session: Session, user: CurrentUser
):
    return await activity_page(session, ResolutionProposal, ProposalRead, filters)


@router.get("/incidents/{incident_id}/detections", response_model=Page[DetectionRead])
async def incident_detections(
    incident_id: UUID,
    session: Session,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    if await session.get(Incident, incident_id) is None:
        raise not_found("Incident")
    clauses = [DetectionOccurrence.incident_id == incident_id]
    count = await session.scalar(
        select(func.count()).select_from(DetectionOccurrence).where(*clauses)
    )
    rows = list(
        await session.scalars(
            select(DetectionOccurrence)
            .where(*clauses)
            .order_by(DetectionOccurrence.created_at, DetectionOccurrence.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return Page[DetectionRead](items=rows, total=count, limit=limit, offset=offset)


class AssigneeRead(InputModel):
    id: UUID
    display_name: str
    role: Role


@router.get("/directory/users", response_model=Page[AssigneeRead])
async def assignees(
    session: Session,
    user: CurrentUser,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0, le=100000),
):
    count = await session.scalar(
        select(func.count()).select_from(User).where(User.role != Role.VIEWER)
    )
    rows = list(
        await session.scalars(
            select(User)
            .where(User.role != Role.VIEWER)
            .order_by(User.display_name, User.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return Page[AssigneeRead](
        items=[
            AssigneeRead.model_validate(
                {key: getattr(row, key) for key in ("id", "display_name", "role")}
            )
            for row in rows
        ],
        total=count,
        limit=limit,
        offset=offset,
    )
