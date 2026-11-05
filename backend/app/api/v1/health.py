import asyncio

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import DomainError

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> dict[str, str]:
    try:
        async with asyncio.timeout(3), request.app.state.engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            if revision != "0004":
                raise DomainError(503, "not_ready", "Database migrations are not current")
    except (SQLAlchemyError, OSError, TimeoutError):
        raise DomainError(503, "not_ready", "Database is unavailable or not migrated") from None
    return {"status": "ready"}
