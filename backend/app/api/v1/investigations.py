from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import AppSettings, CurrentUser, Writer
from app.api.investigation_dependencies import Investigations
from app.investigation.providers import provider_available
from app.schemas.common import Page
from app.schemas.investigations import (
    EvidenceRead,
    InvestigationCapability,
    InvestigationCreate,
    InvestigationPage,
    InvestigationRead,
    ReportRead,
    StepRead,
)

router = APIRouter(tags=["investigations"])


@router.get("/investigation-capabilities", response_model=InvestigationCapability)
async def capabilities(settings: AppSettings, user: CurrentUser) -> InvestigationCapability:
    provider = settings.investigation_llm_provider
    model = settings.investigation_llm_model or ("fake-v1" if provider == "fake" else "")
    return InvestigationCapability(
        provider=provider,
        model=model,
        configured=provider_available(provider, model, settings),
        synthetic=provider == "fake",
    )


@router.post(
    "/incidents/{incident_id}/investigations", response_model=InvestigationRead, status_code=202
)
async def create(
    incident_id: UUID,
    data: InvestigationCreate,
    response: Response,
    service: Investigations,
    actor: Writer,
) -> InvestigationRead:
    result = await service.create(incident_id, data, actor)
    response.headers["Location"] = f"/api/v1/investigations/{result.id}"
    return InvestigationRead.model_validate(result)


@router.get("/incidents/{incident_id}/investigations", response_model=Page[InvestigationRead])
async def list_for_incident(
    incident_id: UUID,
    filters: Annotated[InvestigationPage, Query()],
    service: Investigations,
    user: CurrentUser,
) -> Page[InvestigationRead]:
    return await service.list(incident_id, filters.limit, filters.offset)


@router.get("/investigations/{investigation_id}", response_model=InvestigationRead)
async def get(
    investigation_id: UUID, service: Investigations, user: CurrentUser
) -> InvestigationRead:
    return InvestigationRead.model_validate(await service.get(investigation_id))


@router.get("/investigations/{investigation_id}/report", response_model=ReportRead)
async def report(investigation_id: UUID, service: Investigations, user: CurrentUser) -> ReportRead:
    return await service.report(investigation_id)


@router.get("/investigations/{investigation_id}/evidence", response_model=Page[EvidenceRead])
async def evidence(
    investigation_id: UUID,
    filters: Annotated[InvestigationPage, Query()],
    service: Investigations,
    user: CurrentUser,
) -> Page[EvidenceRead]:
    return await service.evidence(investigation_id, filters.limit, filters.offset)


@router.get("/investigations/{investigation_id}/steps", response_model=Page[StepRead])
async def steps(
    investigation_id: UUID,
    filters: Annotated[InvestigationPage, Query()],
    service: Investigations,
    user: CurrentUser,
) -> Page[StepRead]:
    return await service.steps(investigation_id, filters.limit, filters.offset)


@router.post("/investigations/{investigation_id}/cancel", response_model=InvestigationRead)
async def cancel(
    investigation_id: UUID, service: Investigations, actor: Writer
) -> InvestigationRead:
    return InvestigationRead.model_validate(await service.cancel(investigation_id, actor))
