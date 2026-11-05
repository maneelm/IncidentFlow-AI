from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import CurrentUser
from app.api.observability_dependencies import Detections, Logs
from app.schemas.common import Page
from app.schemas.detection import DetectionFilters, DetectionRead
from app.schemas.logs import LogFilters, LogRead

router = APIRouter(prefix="/detections", tags=["detections"])


@router.get("", response_model=Page[DetectionRead])
async def list_detections(
    filters: Annotated[DetectionFilters, Query()], service: Detections, user: CurrentUser
) -> Page[DetectionRead]:
    return await service.list(filters.limit, filters.offset, filters.service_id, filters.rule_id)


@router.get("/{detection_id}", response_model=DetectionRead)
async def get_detection(
    detection_id: UUID, service: Detections, user: CurrentUser
) -> DetectionRead:
    return DetectionRead.model_validate(await service.get(detection_id))


@router.get("/{detection_id}/logs", response_model=Page[LogRead])
async def evidence(
    detection_id: UUID,
    service: Detections,
    logs: Logs,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> Page[LogRead]:
    await service.get(detection_id)
    return await logs.list(LogFilters(detection_id=detection_id, limit=limit, offset=offset))
