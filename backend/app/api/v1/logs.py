from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser
from app.api.observability_dependencies import Logs, Machine
from app.schemas.common import Page
from app.schemas.logs import IngestResult, LogBatch, LogFilters, LogRead

router = APIRouter(prefix="/logs", tags=["logs"])


@router.post(
    "/ingest",
    response_model=IngestResult,
    status_code=202,
    summary="Atomically persist up to 100 logs and durable detection jobs",
    description="Requires a service-scoped X-Ingestion-Key, not a human JWT. "
    "Repeated source IDs with identical content are idempotent; changed content is 409.",
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "example": {
                        "service_id": "00000000-0000-0000-0000-000000000001",
                        "logs": [
                            {
                                "source_event_id": "payment-timeout-0001",
                                "level": "error",
                                "message": "SYNTHETIC database connection timeout",
                                "timestamp": "2026-11-02T10:00:00Z",
                                "error_code": "DB_CONNECTION_TIMEOUT",
                                "metadata": {"synthetic": True, "pool_size": 20},
                            }
                        ],
                    }
                }
            }
        }
    },
)
async def ingest(data: LogBatch, credential: Machine, service: Logs) -> IngestResult:
    return await service.ingest(data, credential)


@router.get("", response_model=Page[LogRead])
async def list_logs(
    filters: Annotated[LogFilters, Query()], service: Logs, user: CurrentUser
) -> Page[LogRead]:
    return await service.list(filters)


@router.get("/{log_id}", response_model=LogRead)
async def get_log(log_id: UUID, service: Logs, user: CurrentUser) -> LogRead:
    return LogRead.model_validate(await service.get(log_id))
