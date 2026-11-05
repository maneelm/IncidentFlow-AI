from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import CurrentUser, Incidents, Writer
from app.schemas.common import Page
from app.schemas.incidents import (
    AssignmentInput,
    EventRead,
    IncidentCreate,
    IncidentFilters,
    IncidentRead,
    IncidentUpdate,
    TransitionInput,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post("", response_model=IncidentRead, status_code=201)
async def create_incident(
    data: IncidentCreate, response: Response, service: Incidents, actor: Writer
) -> IncidentRead:
    result = await service.create(data, actor)
    response.headers["Location"] = f"/api/v1/incidents/{result.id}"
    return IncidentRead.model_validate(result)


@router.get("", response_model=Page[IncidentRead])
async def list_incidents(
    filters: Annotated[IncidentFilters, Query()], service: Incidents, user: CurrentUser
) -> Page[IncidentRead]:
    return await service.list(filters)


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(incident_id: UUID, service: Incidents, user: CurrentUser) -> IncidentRead:
    return IncidentRead.model_validate(await service.get(incident_id))


@router.patch("/{incident_id}", response_model=IncidentRead)
async def update_incident(
    incident_id: UUID, data: IncidentUpdate, service: Incidents, actor: Writer
) -> IncidentRead:
    return IncidentRead.model_validate(await service.update(incident_id, data, actor))


@router.put("/{incident_id}/assignee", response_model=IncidentRead)
async def assign_incident(
    incident_id: UUID, data: AssignmentInput, service: Incidents, actor: Writer
) -> IncidentRead:
    return IncidentRead.model_validate(await service.assign(incident_id, data, actor))


@router.post("/{incident_id}/transitions", response_model=IncidentRead)
async def transition_incident(
    incident_id: UUID, data: TransitionInput, service: Incidents, actor: Writer
) -> IncidentRead:
    return IncidentRead.model_validate(await service.transition(incident_id, data, actor))


@router.get("/{incident_id}/events", response_model=Page[EventRead])
async def event_history(
    incident_id: UUID,
    service: Incidents,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> Page[EventRead]:
    return await service.events(incident_id, limit, offset)
