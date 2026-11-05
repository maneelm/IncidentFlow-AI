from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError, not_found
from app.db.clock import database_now
from app.models import DetectionJob, DetectionOccurrence
from app.models.enums import JobStatus
from app.repositories.detection import DetectionRepository
from app.schemas.common import Page
from app.schemas.detection import DetectionRead, JobRead


class DetectionQueries:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = DetectionRepository(session)

    async def get(self, detection_id: UUID) -> DetectionOccurrence:
        occurrence = await self.session.get(DetectionOccurrence, detection_id)
        if occurrence is None:
            raise not_found("Detection")
        return occurrence

    async def list(
        self, limit: int, offset: int, service_id: UUID | None, rule_id: UUID | None
    ) -> Page[DetectionRead]:
        items, total = await self.repository.page(
            DetectionOccurrence, limit, offset, service_id=service_id, rule_id=rule_id
        )
        return Page[DetectionRead](items=items, total=total, limit=limit, offset=offset)

    async def get_job(self, job_id: UUID) -> DetectionJob:
        job = await self.session.get(DetectionJob, job_id)
        if job is None:
            raise not_found("Detection job")
        return job

    async def jobs(
        self, limit: int, offset: int, service_id: UUID | None, status: JobStatus | None
    ) -> Page[JobRead]:
        items, total = await self.repository.page(
            DetectionJob, limit, offset, service_id=service_id, status=status
        )
        return Page[JobRead](items=items, total=total, limit=limit, offset=offset)

    async def retry(self, job_id: UUID) -> DetectionJob:
        job = await self.session.scalar(
            select(DetectionJob).where(DetectionJob.id == job_id).with_for_update()
        )
        if job is None:
            raise not_found("Detection job")
        if job.status != JobStatus.FAILED:
            raise DomainError(409, "job_not_failed", "Only failed jobs may be manually requeued")
        job.status = JobStatus.PENDING
        job.attempts = 0
        job.requeues += 1
        job.available_at = await database_now(self.session)
        job.finished_at = None
        job.last_error_code = None
        await self.session.flush()
        return job
