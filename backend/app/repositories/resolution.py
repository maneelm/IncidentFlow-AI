from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.models.resolution import ResolutionJob, ResolutionProposal, SyntheticServiceState


class ResolutionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def proposal(self, proposal_id: UUID, *, lock: bool = False) -> ResolutionProposal:
        query = select(ResolutionProposal).where(ResolutionProposal.id == proposal_id)
        if lock:
            query = query.with_for_update()
        result = await self.session.scalar(query.execution_options(populate_existing=True))
        if result is None:
            raise not_found("Resolution proposal")
        return result

    async def state(self, service_id: UUID, *, lock: bool = False) -> SyntheticServiceState:
        query = select(SyntheticServiceState).where(SyntheticServiceState.service_id == service_id)
        if lock:
            query = query.with_for_update()
        result = await self.session.scalar(query.execution_options(populate_existing=True))
        if result is None:
            raise not_found("Synthetic state; initialize a scenario first")
        return result

    async def job(self, proposal_id: UUID, *, lock: bool = False) -> ResolutionJob | None:
        query = select(ResolutionJob).where(ResolutionJob.proposal_id == proposal_id)
        if lock:
            query = query.with_for_update()
        return await self.session.scalar(query.execution_options(populate_existing=True))
