"""Durable execution of fixed synthetic actions, entirely within PostgreSQL transactions."""

import argparse
import asyncio
import logging
import signal
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.clock import database_now
from app.db.session import make_engine, make_session_factory
from app.models import User
from app.models.resolution import (
    ProposalStatus,
    ResolutionApproval,
    ResolutionExecution,
    ResolutionJob,
    ResolutionProposal,
)
from app.ops.runtime import configure_logging, heartbeat_loop, stop_heartbeat
from app.repositories.resolution import ResolutionRepository
from app.resolution.catalog import SyntheticSnapshot, digest
from app.resolution.simulator import simulate
from app.services.resolution import (
    TERMINAL,
    ResolutionService,
    approved_payload,
    invalid_context,
    record_event,
)

logger = logging.getLogger("incidentflow.resolution_worker")


@dataclass(frozen=True)
class ResolutionClaim:
    job_id: UUID
    proposal_id: UUID
    token: UUID
    attempt: int


class ResolutionWorker:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings):
        self.sessions, self.settings = sessions, settings

    async def expire_one(self) -> bool:
        async with asyncio.timeout(10), self.sessions() as session, session.begin():
            now = await database_now(session)
            proposal = await session.scalar(
                select(ResolutionProposal)
                .where(
                    ResolutionProposal.status == ProposalStatus.PENDING_APPROVAL,
                    ResolutionProposal.approval_deadline <= now,
                )
                .order_by(ResolutionProposal.approval_deadline, ResolutionProposal.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if proposal is None:
                return False
            await ResolutionService(session, self.settings).expire(proposal)
            return True

    async def authorized(
        self, session: AsyncSession, proposal: ResolutionProposal, job: ResolutionJob
    ) -> bool:
        approval = await session.get(ResolutionApproval, job.approval_id)
        if (
            approval is None
            or approval.proposal_id != proposal.id
            or approval.decision != "approved"
        ):
            return False
        actor = await session.get(User, approval.actor_id)
        return bool(
            actor
            and actor.role in {"admin", "engineer"}
            and (not proposal.policy["distinct_approver"] or actor.id != proposal.requested_by)
            and approval.payload_hash == proposal.payload_hash
            and digest(approval.snapshot) == proposal.payload_hash
            and approval.snapshot == approved_payload(proposal)
        )

    async def claim_one(self) -> ResolutionClaim | None:
        async with asyncio.timeout(10), self.sessions() as session, session.begin():
            now = await database_now(session)
            job = await session.scalar(
                select(ResolutionJob)
                .where(
                    or_(
                        and_(ResolutionJob.status == "pending", ResolutionJob.available_at <= now),
                        and_(
                            ResolutionJob.status == "processing",
                            ResolutionJob.lease_expires_at <= now,
                        ),
                    )
                )
                .order_by(ResolutionJob.created_at, ResolutionJob.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return None
            proposal = await ResolutionRepository(session).proposal(job.proposal_id, lock=True)
            if proposal.status in TERMINAL:
                job.status = (
                    "cancelled" if proposal.status == ProposalStatus.CANCELLED else "failed"
                )
                job.lease_token = job.lease_expires_at = None
                job.finished_at = now
                return None
            if proposal.status not in {
                ProposalStatus.QUEUED,
                ProposalStatus.RUNNING,
            } or not await self.authorized(session, proposal, job):
                await self.terminal(session, proposal, job, "approval_invalid")
                return None
            if now >= proposal.approval_deadline:
                await ResolutionService(session, self.settings).expire(proposal, job)
                return None
            if job.attempts >= job.max_attempts:
                await self.terminal(session, proposal, job, "lease_exhausted")
                return None
            if job.status == "processing":
                await record_event(
                    session,
                    proposal,
                    "execution_retried",
                    details={"reason": "lease_recovered", "previous_attempt": job.attempts},
                    correlation_id=job.id,
                )
            job.status = "processing"
            job.attempts += 1
            job.lease_token = uuid4()
            job.lease_expires_at = now + timedelta(seconds=proposal.policy["lease_seconds"])
            proposal.status = ProposalStatus.RUNNING
            proposal.error_code = None
            await record_event(
                session,
                proposal,
                "execution_started",
                details={"attempt": job.attempts},
                correlation_id=job.id,
            )
            return ResolutionClaim(job.id, proposal.id, job.lease_token, job.attempts)

    async def terminal(
        self, session: AsyncSession, proposal: ResolutionProposal, job: ResolutionJob, code: str
    ) -> None:
        proposal.status = ProposalStatus.FAILED
        proposal.error_code = code
        job.status = "failed"
        job.last_error_code = code
        job.finished_at = await database_now(session)
        job.lease_token = job.lease_expires_at = None
        await record_event(
            session,
            proposal,
            "execution_failed",
            details={"error_code": code, "attempt": job.attempts},
            correlation_id=job.id,
        )

    async def execute(self, claim: ResolutionClaim) -> str:
        async with asyncio.timeout(5), self.sessions() as session, session.begin():
            await session.execute(text("SET LOCAL statement_timeout = '5s'"))
            await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            job = await session.scalar(
                select(ResolutionJob).where(ResolutionJob.id == claim.job_id).with_for_update()
            )
            now = await database_now(session)
            if (
                job is None
                or job.status != "processing"
                or job.lease_token != claim.token
                or job.lease_expires_at <= now
            ):
                return "stale_claim"
            proposal = await ResolutionRepository(session).proposal(claim.proposal_id, lock=True)
            if proposal.status != ProposalStatus.RUNNING or not await self.authorized(
                session, proposal, job
            ):
                await self.terminal(session, proposal, job, "approval_invalid")
                return "failed"
            if now >= proposal.approval_deadline:
                await ResolutionService(session, self.settings).expire(proposal, job)
                return "expired"
            if code := await invalid_context(session, proposal):
                await ResolutionService(session, self.settings).expire(proposal, job, code)
                return "expired"
            state = await ResolutionRepository(session).state(proposal.service_id, lock=True)
            before_version = state.version
            result, verification = simulate(
                proposal.action_id, SyntheticSnapshot.model_validate(state.snapshot), now
            )
            if (
                verification.contract_satisfied
                and proposal.risk == "synthetic_mutation"
                and result.after != result.before
            ):
                state.snapshot = result.after.model_dump(mode="json")
                await session.flush()
            execution = ResolutionExecution(
                id=uuid4(),
                proposal_id=proposal.id,
                job_id=job.id,
                attempt=claim.attempt,
                before_version=before_version,
                after_version=state.version,
                result=result.model_dump(mode="json"),
                verification=verification.model_dump(mode="json"),
            )
            session.add(execution)
            await record_event(
                session,
                proposal,
                "verification_completed",
                details={
                    "contract_satisfied": verification.contract_satisfied,
                    "improved": verification.improved,
                    "synthetic": True,
                },
                correlation_id=execution.id,
            )
            # The lease and approval must still be live at the commit boundary, after any lock wait.
            completed = await database_now(session)
            if completed >= job.lease_expires_at or completed >= proposal.approval_deadline:
                raise TimeoutError
            proposal.status = (
                ProposalStatus.SUCCEEDED
                if verification.contract_satisfied
                else ProposalStatus.FAILED
            )
            proposal.error_code = None if verification.contract_satisfied else "verification_failed"
            job.status = "completed" if verification.contract_satisfied else "failed"
            job.last_error_code = proposal.error_code
            job.finished_at = completed
            job.lease_token = job.lease_expires_at = None
            await record_event(
                session,
                proposal,
                "execution_succeeded" if verification.contract_satisfied else "execution_failed",
                details={"synthetic": True, "error_code": proposal.error_code},
                correlation_id=execution.id,
            )
            return proposal.status.value

    async def failure(self, claim: ResolutionClaim) -> str:
        async with asyncio.timeout(10), self.sessions() as session, session.begin():
            job = await session.scalar(
                select(ResolutionJob).where(ResolutionJob.id == claim.job_id).with_for_update()
            )
            now = await database_now(session)
            if (
                job is None
                or job.status != "processing"
                or job.lease_token != claim.token
                or job.lease_expires_at <= now
            ):
                return "stale_claim"
            proposal = await ResolutionRepository(session).proposal(claim.proposal_id, lock=True)
            if now >= proposal.approval_deadline:
                await ResolutionService(session, self.settings).expire(proposal, job)
                return "expired"
            if job.attempts >= job.max_attempts:
                await self.terminal(session, proposal, job, "execution_error")
                return "failed"
            job.status = "pending"
            job.lease_token = job.lease_expires_at = None
            job.last_error_code = "execution_error"
            job.available_at = now + timedelta(seconds=min(30, 2**job.attempts))
            proposal.status = ProposalStatus.QUEUED
            proposal.error_code = "execution_error"
            await record_event(
                session,
                proposal,
                "execution_retried",
                details={"error_code": "execution_error", "attempt": job.attempts},
                correlation_id=job.id,
            )
            return "retry_pending"

    async def process(self, claim: ResolutionClaim) -> str:
        try:
            return await self.execute(claim)
        except Exception as exc:
            logger.error(
                "Resolution %s attempt %s failed (%s)",
                claim.proposal_id,
                claim.attempt,
                type(exc).__name__,
            )
            return await self.failure(claim)

    async def run_once(self, stop: asyncio.Event | None = None) -> int:
        processed = 0
        for _ in range(self.settings.worker_batch_size):
            if stop is not None and stop.is_set():
                break
            if await self.expire_one():
                processed += 1
                continue
            claim = await self.claim_one()
            if claim is None:
                break
            await self.process(claim)
            processed += 1
        return processed


async def run(once: bool) -> None:
    settings = Settings()
    engine = make_engine(settings)
    worker = ResolutionWorker(make_session_factory(engine), settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def shutdown(signum, frame):
        loop.call_soon_threadsafe(stop.set)

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, shutdown)
    heartbeat = asyncio.create_task(heartbeat_loop())
    logger.info("worker_started role=resolution")
    try:
        while not stop.is_set():
            try:
                processed = await worker.run_once(stop)
            except Exception as exc:
                logger.error("Resolution polling failed (%s)", type(exc).__name__)
                if once:
                    raise SystemExit("Resolution worker failed; check PostgreSQL") from None
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
        logger.info("worker_stopped role=resolution")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    configure_logging()
    asyncio.run(run(parser.parse_args().once))


if __name__ == "__main__":
    main()
