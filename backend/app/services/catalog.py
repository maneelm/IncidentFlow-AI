from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.models import Service
from app.repositories.services import ServiceRepository
from app.schemas.common import Page
from app.schemas.services import ServiceCreate, ServiceRead, ServiceUpdate


class CatalogService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = ServiceRepository(session)

    async def get(self, service_id: UUID) -> Service:
        service = await self.repository.get(service_id)
        if service is None:
            raise not_found("Service")
        return service

    async def create(self, data: ServiceCreate) -> Service:
        service = Service(**data.model_dump())
        self.session.add(service)
        await self.session.flush()
        return service

    async def update(self, service_id: UUID, data: ServiceUpdate) -> Service:
        service = await self.get(service_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(service, field, value)
        await self.session.flush()
        await self.session.refresh(service)
        return service

    async def list(self, limit: int, offset: int) -> Page[ServiceRead]:
        items, total = await self.repository.list(limit, offset)
        return Page[ServiceRead](items=items, total=total, limit=limit, offset=offset)
