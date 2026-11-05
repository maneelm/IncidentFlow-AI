from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def database_now(session: AsyncSession) -> datetime:
    """Use PostgreSQL's wall clock rather than transaction start or a worker's host clock."""
    return await session.scalar(select(func.clock_timestamp()))
