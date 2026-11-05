from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import Admin
from app.api.observability_dependencies import Credentials
from app.schemas.common import Page
from app.schemas.detection import (
    CredentialCreate,
    CredentialIssued,
    CredentialRead,
    ServicePageFilters,
)

router = APIRouter(prefix="/ingestion-credentials", tags=["ingestion credentials"])


@router.post("", response_model=CredentialIssued, status_code=201)
async def issue(
    data: CredentialCreate, response: Response, service: Credentials, actor: Admin
) -> CredentialIssued:
    issued = await service.create(data, actor)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Location"] = f"/api/v1/ingestion-credentials/{issued.id}"
    return issued


@router.get("", response_model=Page[CredentialRead])
async def list_credentials(
    filters: Annotated[ServicePageFilters, Query()], service: Credentials, actor: Admin
) -> Page[CredentialRead]:
    return await service.list(filters.limit, filters.offset, filters.service_id)


@router.get("/{credential_id}", response_model=CredentialRead)
async def get_credential(credential_id: UUID, service: Credentials, actor: Admin) -> CredentialRead:
    return CredentialRead.model_validate(await service.get(credential_id))


@router.post("/{credential_id}/revoke", response_model=CredentialRead)
async def revoke(credential_id: UUID, service: Credentials, actor: Admin) -> CredentialRead:
    return CredentialRead.model_validate(await service.revoke(credential_id))
