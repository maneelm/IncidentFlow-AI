from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Service


class ServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, service_id: UUID) -> Service | None:
        return await self.session.get(Service, service_id)

    async def list(self, limit: int, offset: int) -> tuple[list[Service], int]:
        total = await self.session.scalar(select(func.count()).select_from(Service))
        items = await self.session.scalars(
            select(Service).order_by(Service.name, Service.id).limit(limit).offset(offset)
        )
        return list(items), total or 0
