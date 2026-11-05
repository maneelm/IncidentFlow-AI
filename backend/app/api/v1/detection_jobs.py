from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import Admin
from app.api.observability_dependencies import Detections
from app.schemas.common import Page
from app.schemas.detection import JobFilters, JobRead

router = APIRouter(prefix="/detection-jobs", tags=["detection jobs"])


@router.get("", response_model=Page[JobRead])
async def jobs(
    filters: Annotated[JobFilters, Query()], service: Detections, actor: Admin
) -> Page[JobRead]:
    return await service.jobs(filters.limit, filters.offset, filters.service_id, filters.status)


@router.get("/{job_id}", response_model=JobRead)
async def get_job(job_id: UUID, service: Detections, actor: Admin) -> JobRead:
    return JobRead.model_validate(await service.get_job(job_id))


@router.post("/{job_id}/retry", response_model=JobRead)
async def retry(job_id: UUID, service: Detections, actor: Admin) -> JobRead:
    return JobRead.model_validate(await service.retry(job_id))
