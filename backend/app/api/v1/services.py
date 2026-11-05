from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.api.dependencies import Admin, Catalog, CurrentUser
from app.schemas.common import Page
from app.schemas.services import ServiceCreate, ServiceRead, ServiceUpdate

router = APIRouter(prefix="/services", tags=["services"])


@router.post("", response_model=ServiceRead, status_code=201)
async def create_service(
    data: ServiceCreate, response: Response, service: Catalog, actor: Admin
) -> ServiceRead:
    result = await service.create(data)
    response.headers["Location"] = f"/api/v1/services/{result.id}"
    return ServiceRead.model_validate(result)


@router.get("", response_model=Page[ServiceRead])
async def list_services(
    service: Catalog,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> Page[ServiceRead]:
    return await service.list(limit, offset)


@router.get("/{service_id}", response_model=ServiceRead)
async def get_service(service_id: UUID, service: Catalog, user: CurrentUser) -> ServiceRead:
    return ServiceRead.model_validate(await service.get(service_id))


@router.patch("/{service_id}", response_model=ServiceRead)
async def update_service(
    service_id: UUID, data: ServiceUpdate, service: Catalog, actor: Admin
) -> ServiceRead:
    return ServiceRead.model_validate(await service.update(service_id, data))
