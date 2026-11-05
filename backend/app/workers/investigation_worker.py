"""Short database transactions around a durable LangGraph investigation workflow."""

import argparse
import asyncio
import logging
import signal
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.clock import database_now
from app.db.session import make_engine, make_session_factory
from app.investigation.errors import InvestigationFailure, LostClaim
from app.investigation.graph import InvestigationGraph
from app.investigation.providers import Provider
from app.investigation.store import InvestigationClaim, InvestigationStore
from app.models.investigations import (
    Investigation,
    InvestigationJob,
    InvestigationJobStatus,
    InvestigationStatus,
    InvestigationStep,
)
from app.ops.runtime import configure_logging, heartbeat_loop, stop_heartbeat

logger = logging.getLogger("incidentflow.investigation_worker")


class InvestigationWorker:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        settings: Settings,
        provider: Provider | None = None,
    ) -> None:
        self.sessions, self.settings, self.provider = sessions, settings, provider
        self.store = InvestigationStore(sessions, settings)

    async def claim_one(self) -> InvestigationClaim | None:
        async with asyncio.timeout(10), self.sessions() as session, session.begin():
            now = await database_now(session)
            job = await session.scalar(
                select(InvestigationJob)
                .where(
                    or_(
                        and_(
                            InvestigationJob.status == InvestigationJobStatus.PENDING,
                            InvestigationJob.available_at <= now,
                        ),
                        and_(
                            InvestigationJob.status == InvestigationJobStatus.PROCESSING,
                            InvestigationJob.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(InvestigationJob.created_at, InvestigationJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            investigation = await session.scalar(
                select(Investigation)
                .where(Investigation.id == job.investigation_id)
                .with_for_update()
            )
            expired = (
                investigation.started_at is not None
                and now
                >= investigation.started_at
                + timedelta(seconds=investigation.policy["duration_seconds"])
            )
            if job.attempts >= job.max_attempts or expired:
                job.status = InvestigationJobStatus.FAILED
                job.finished_at = now
                job.lease_token = None
                job.lease_expires_at = None
                job.last_error_code = "duration_exceeded" if expired else "lease_exhausted"
                investigation.status = InvestigationStatus.FAILED
                investigation.completed_at = now
                investigation.error_code = job.last_error_code
                investigation.checkpoint = investigation.checkpoint | {
                    "status": "failed",
                    "error_code": job.last_error_code,
                }
                logger.error("Investigation %s failed: %s", investigation.id, job.last_error_code)
                return None
            job.attempts += 1
            job.status = InvestigationJobStatus.PROCESSING
            job.lease_token = uuid4()
            job.lease_expires_at = now + timedelta(seconds=investigation.policy["lease_seconds"])
            investigation.status = InvestigationStatus.RUNNING
            investigation.started_at = investigation.started_at or now
            investigation.error_code = None
            investigation.checkpoint = investigation.checkpoint | {
                "status": "running",
                "error_code": None,
            }
            return InvestigationClaim(job.id, investigation.id, job.lease_token, job.attempts)

    async def process(self, claim: InvestigationClaim) -> str:
        try:
            async with self.sessions() as session, session.begin():
                investigation = await session.get(Investigation, claim.investigation_id)
                duration = investigation.policy["duration_seconds"]
                now = await database_now(session)
                remaining = (
                    investigation.started_at + timedelta(seconds=duration) - now
                ).total_seconds()
            async with asyncio.timeout(max(0, remaining)):
                state = await InvestigationGraph(self.store, claim, self.provider).run()
            logger.info("Investigation %s finished: %s", claim.investigation_id, state["status"])
            return state["status"]
        except LostClaim:
            return "stale_claim"
        except InvestigationFailure as exc:
            return await self.failure(
                claim,
                exc.code,
                exc.retryable,
                diagnostics=exc.diagnostics,
                retry_after=exc.retry_after,
            )
        except TimeoutError:
            return await self.failure(claim, "duration_exceeded", False)
        except Exception as exc:
            logger.error(
                "Investigation %s processing error (%s)", claim.investigation_id, type(exc).__name__
            )
            return await self.failure(claim, "processing_error", True)

    async def failure(
        self,
        claim: InvestigationClaim,
        code: str,
        retryable: bool,
        *,
        diagnostics: dict | None = None,
        retry_after: float | None = None,
    ) -> str:
        logger.error("Investigation %s attempt %s: %s", claim.investigation_id, claim.attempt, code)
        async with asyncio.timeout(10), self.sessions() as session, session.begin():
            job = await session.scalar(
                select(InvestigationJob)
                .where(
                    InvestigationJob.id == claim.job_id,
                    InvestigationJob.lease_token == claim.token,
                    InvestigationJob.status == InvestigationJobStatus.PROCESSING,
                )
                .with_for_update()
            )
            if job is None:
                return "stale_claim"
            investigation = await session.scalar(
                select(Investigation)
                .where(Investigation.id == claim.investigation_id)
                .with_for_update()
            )
            now = await database_now(session)
            delay = max(min(30, 2**job.attempts), retry_after or 0)
            deadline = investigation.started_at + timedelta(
                seconds=investigation.policy["duration_seconds"]
            )
            budget_exhausted = (
                investigation.llm_calls >= investigation.policy["max_calls"]
                or investigation.output_tokens_reserved + 512
                > investigation.policy["max_output_tokens"]
            ) and investigation.stage in {"hypotheses", "recommendations"}
            terminal = (
                not retryable
                or job.attempts >= job.max_attempts
                or now + timedelta(seconds=delay) >= deadline
                or budget_exhausted
            )
            details = {
                "error_code": code,
                "failed_at": now.isoformat(),
                "retryable": retryable,
                "retry_scheduled": not terminal,
                "diagnostics": diagnostics or {},
                "calls_reserved": investigation.llm_calls,
                "input_tokens_reserved": investigation.input_tokens_reserved,
                "output_tokens_reserved": investigation.output_tokens_reserved,
            }
            if retryable:
                details["retry_delay_seconds"] = delay
            if budget_exhausted:
                details["retry_blocked"] = "llm_budget_exhausted"
            elif retryable and now + timedelta(seconds=delay) >= deadline:
                details["retry_blocked"] = "duration_limit"
            job.lease_token = None
            job.lease_expires_at = None
            job.last_error_code = code
            investigation.error_code = code
            investigation.checkpoint = investigation.checkpoint | {
                "status": "failed" if terminal else "queued",
                "error_code": code,
            }
            if diagnostics and diagnostics.get("condition"):
                investigation.checkpoint = investigation.checkpoint | {
                    "generation_feedback": {
                        key: diagnostics[key] for key in ("condition", "collection", "field")
                    }
                }
            await session.execute(
                insert(InvestigationStep)
                .values(
                    investigation_id=investigation.id,
                    attempt=claim.attempt,
                    stage=investigation.stage,
                    status="failed",
                    details=details,
                )
                .on_conflict_do_update(
                    index_elements=["investigation_id", "attempt", "stage"],
                    set_={
                        "status": "failed",
                        "details": details,
                    },
                )
            )
            if terminal:
                investigation.status = InvestigationStatus.FAILED
                investigation.completed_at = now
                job.status = InvestigationJobStatus.FAILED
                job.finished_at = now
                return "failed"
            investigation.status = InvestigationStatus.QUEUED
            job.status = InvestigationJobStatus.PENDING
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


async def run(once: bool) -> None:
    settings = Settings()
    engine = make_engine(settings)
    worker = InvestigationWorker(make_session_factory(engine), settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def shutdown(signum: int, frame: object) -> None:
        loop.call_soon_threadsafe(stop.set)

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, shutdown)
    heartbeat = asyncio.create_task(heartbeat_loop())
    logger.info("worker_started role=investigation")
    try:
        while not stop.is_set():
            try:
                processed = await worker.run_once(stop)
            except Exception as exc:
                logger.error("Investigation polling failed (%s)", type(exc).__name__)
                if once:
                    raise SystemExit(
                        "Investigation worker polling failed; check PostgreSQL"
                    ) from None
                processed = 0
            if once:
                break
            if not processed:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
                except TimeoutError:
                    pass
    finally:
        await stop_heartbeat(heartbeat)
        await engine.dispose()
        logger.info("Investigation worker stopped")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Attempt one bounded batch and exit")
    configure_logging()
    # Hosted SDK debug logging is deliberately disabled in this worker.
    logging.getLogger("groq").setLevel(logging.WARNING)
    asyncio.run(run(parser.parse_args().once))


if __name__ == "__main__":
    main()
