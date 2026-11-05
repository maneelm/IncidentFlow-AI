"""Durable at-least-once claims; transactional, idempotent detection effects."""

import argparse
import asyncio
import logging
import signal
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.clock import database_now
from app.db.session import make_engine, make_session_factory
from app.models import DetectionJob, LogEntry
from app.models.enums import JobStatus
from app.ops.runtime import configure_logging, heartbeat_loop, stop_heartbeat
from app.repositories.detection import JobRepository
from app.services.detection import DetectionEngine

logger = logging.getLogger("incidentflow.worker")


@dataclass(frozen=True)
class Claim:
    job_id: UUID
    token: UUID | None


def safe_error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "processing_timeout"
    if isinstance(exc, IntegrityError):
        return "constraint_error"
    if isinstance(exc, SQLAlchemyError):
        return "database_error"
    return "processing_error"


class DetectionWorker:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings

    async def claim_one(self) -> Claim | None:
        async with (
            asyncio.timeout(self.settings.worker_timeout_seconds),
            self.sessions() as session,
            session.begin(),
        ):
            now = await database_now(session)
            job = await JobRepository(session).next_claimable(now)
            if job is None:
                return None
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.FAILED
                job.last_error_code = "lease_expired"
                job.finished_at = now
                job.lease_token = None
                job.lease_expires_at = None
                return Claim(job.id, None)
            job.status = JobStatus.PROCESSING
            job.attempts += 1
            job.total_attempts += 1
            job.lease_token = uuid4()
            job.lease_expires_at = now + timedelta(seconds=self.settings.worker_lease_seconds)
            return Claim(job.id, job.lease_token)

    async def process(self, claim: Claim) -> str:
        if claim.token is None:
            logger.error("Job %s exhausted its lease recovery attempts", claim.job_id)
            return "failed"
        try:
            async with asyncio.timeout(self.settings.worker_timeout_seconds):
                async with self.sessions() as session, session.begin():
                    milliseconds = self.settings.worker_timeout_seconds * 1000
                    await session.execute(
                        text("SELECT set_config('statement_timeout', :value, true)"),
                        {"value": str(milliseconds)},
                    )
                    job = await session.scalar(
                        select(DetectionJob)
                        .where(
                            DetectionJob.id == claim.job_id,
                            DetectionJob.lease_token == claim.token,
                            DetectionJob.status == JobStatus.PROCESSING,
                        )
                        .with_for_update()
                    )
                    if job is None:
                        return "stale_claim"
                    log = await session.get(LogEntry, job.log_id)
                    await DetectionEngine(session).evaluate(log)
                    job.status = JobStatus.COMPLETED
                    job.finished_at = await database_now(session)
                    job.lease_token = None
                    job.lease_expires_at = None
                    job.last_error_code = None
            logger.info("Completed detection job %s", claim.job_id)
            return "completed"
        except Exception as exc:
            # Never include exception text, SQL values, logs, keys, or body content.
            code = safe_error_code(exc)
            logger.error("Detection job %s failed: %s (%s)", claim.job_id, code, type(exc).__name__)
            return await self.record_failure(claim, code)

    async def record_failure(self, claim: Claim, code: str) -> str:
        async with (
            asyncio.timeout(self.settings.worker_timeout_seconds),
            self.sessions() as session,
            session.begin(),
        ):
            job = await session.scalar(
                select(DetectionJob)
                .where(
                    DetectionJob.id == claim.job_id,
                    DetectionJob.lease_token == claim.token,
                    DetectionJob.status == JobStatus.PROCESSING,
                )
                .with_for_update()
            )
            if job is None:
                return "stale_claim"
            now = await database_now(session)
            job.last_error_code = code
            job.lease_token = None
            job.lease_expires_at = None
            if job.attempts >= job.max_attempts:
                job.status = JobStatus.FAILED
                job.finished_at = now
                return "failed"
            delay = min(60, self.settings.worker_backoff_seconds * 2 ** (job.attempts - 1))
            job.status = JobStatus.PENDING
            job.available_at = now + timedelta(seconds=delay)
            return "retry_pending"

    async def run_once(self, stop: asyncio.Event | None = None) -> int:
        processed = 0
        for _ in range(self.settings.worker_batch_size):
            if stop is not None and stop.is_set():
                break
            claim = await self.claim_one()
            if claim is None:
                break
            await self.process(claim)
            processed += 1
        return processed


async def run(once: bool = False) -> None:
    settings = Settings()
    engine = make_engine(settings)
    worker = DetectionWorker(make_session_factory(engine), settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def shutdown(signum: int, frame: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, shutdown)
    heartbeat = asyncio.create_task(heartbeat_loop())
    logger.info("worker_started role=detection")
    try:
        while not stop.is_set():
            try:
                processed = await worker.run_once(stop)
            except Exception as exc:
                logger.error("Worker polling failed (%s); retrying", type(exc).__name__)
                if once:
                    raise SystemExit(
                        "Worker polling failed; check PostgreSQL and migrations"
                    ) from None
                processed = 0
            if once:
                logger.info("Processed %s jobs", processed)
                break
            if not processed:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        await stop_heartbeat(heartbeat)
        await engine.dispose()
        logger.info("Detection worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Process one bounded batch and exit")
    configure_logging()
    asyncio.run(run(parser.parse_args().once))


if __name__ == "__main__":
    main()
