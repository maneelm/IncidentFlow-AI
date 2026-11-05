import hashlib
import json
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError, not_found
from app.db.clock import database_now
from app.models import DetectionJob, IngestionCredential, LogEntry, Service
from app.models.enums import JobStatus
from app.repositories.logs import LogRepository
from app.schemas.common import Page
from app.schemas.logs import IngestedItem, IngestResult, LogBatch, LogFilters, LogInput, LogRead


def content_hash(log: LogInput) -> str:
    canonical = json.dumps(log.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LogService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.session = session
        self.settings = settings
        self.repository = LogRepository(session)

    async def ingest(self, data: LogBatch, credential: IngestionCredential) -> IngestResult:
        if credential.service_id != data.service_id:
            raise DomainError(
                403, "service_scope_mismatch", "Credential does not authorize this service"
            )
        # Serialize receipts for one service, including their commit order. Detection
        # jobs can then be consumed deterministically even with concurrent producers.
        await self.session.scalar(
            select(Service.id).where(Service.id == data.service_id).with_for_update()
        )
        now = await database_now(self.session)
        earliest = now - timedelta(seconds=self.settings.log_max_age_seconds)
        latest = now + timedelta(seconds=self.settings.log_max_future_seconds)
        unique: dict[str, tuple[LogInput, str]] = {}
        for log in data.logs:
            if not earliest <= log.timestamp <= latest:
                raise DomainError(
                    422,
                    "timestamp_out_of_range",
                    "Source timestamp is outside the accepted tolerance",
                )
            digest = content_hash(log)
            previous = unique.get(log.source_event_id)
            if previous is not None and previous[1] != digest:
                raise DomainError(
                    409,
                    "source_event_conflict",
                    "Source event ID was reused with different content",
                )
            unique[log.source_event_id] = (log, digest)
        rows = []
        for source_id in sorted(unique):
            log, digest = unique[source_id]
            values = log.model_dump(exclude={"metadata"})
            rows.append(
                values
                | {
                    "id": uuid4(),
                    "service_id": data.service_id,
                    "credential_id": credential.id,
                    "ingested_at": now,
                    "metadata": log.metadata,
                    "content_hash": digest,
                }
            )
        statement = (
            insert(LogEntry.__table__)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["service_id", "source_event_id"])
            .returning(LogEntry.id)
        )
        created = set((await self.session.scalars(statement)).all())
        logs = list(
            await self.session.scalars(
                select(LogEntry)
                .where(LogEntry.service_id == data.service_id, LogEntry.source_event_id.in_(unique))
                .order_by(LogEntry.source_event_id)
            )
        )
        for log in logs:
            if log.content_hash != unique[log.source_event_id][1]:
                raise DomainError(
                    409,
                    "source_event_conflict",
                    "Source event ID was reused with different content",
                )
            if log.id in created:
                self.session.add(
                    DetectionJob(
                        log_id=log.id,
                        service_id=log.service_id,
                        status=JobStatus.PENDING,
                        available_at=now,
                        max_attempts=self.settings.worker_max_attempts,
                    )
                )
        await self.session.flush()
        jobs = list(
            await self.session.scalars(
                select(DetectionJob).where(DetectionJob.log_id.in_([log.id for log in logs]))
            )
        )
        by_log = {job.log_id: job for job in jobs}
        items = [
            IngestedItem(
                source_event_id=log.source_event_id,
                log_id=log.id,
                job_id=by_log[log.id].id,
                created=log.id in created,
            )
            for log in sorted(logs, key=lambda row: row.source_event_id)
        ]
        return IngestResult(
            accepted=len(created), duplicates=len(data.logs) - len(created), items=items
        )

    async def get(self, log_id: UUID) -> LogEntry:
        log = await self.repository.get(log_id)
        if log is None:
            raise not_found("Log")
        return log

    async def list(self, filters: LogFilters) -> Page[LogRead]:
        items, total = await self.repository.list(filters)
        return Page[LogRead](items=items, total=total, limit=filters.limit, offset=filters.offset)
