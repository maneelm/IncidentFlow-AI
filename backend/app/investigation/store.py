import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.clock import database_now
from app.investigation.errors import InvestigationFailure, LostClaim
from app.models.investigations import (
    Investigation,
    InvestigationCitation,
    InvestigationEvidence,
    InvestigationJob,
    InvestigationJobStatus,
    InvestigationReport,
    InvestigationStatus,
    InvestigationStep,
)
from app.schemas.investigations import EvidenceRead, ReportMetadata, ReportRead


@dataclass(frozen=True)
class InvestigationClaim:
    job_id: UUID
    investigation_id: UUID
    token: UUID
    attempt: int


class InvestigationStore:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.sessions, self.settings = sessions, settings

    @asynccontextmanager
    async def fenced(
        self, claim: InvestigationClaim
    ) -> AsyncIterator[tuple[AsyncSession, Investigation, InvestigationJob]]:
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
                raise LostClaim()
            investigation = await session.scalar(
                select(Investigation)
                .where(Investigation.id == claim.investigation_id)
                .with_for_update()
            )
            now = await database_now(session)
            if investigation.status != InvestigationStatus.RUNNING or job.lease_expires_at <= now:
                raise LostClaim()
            if now >= investigation.started_at + timedelta(
                seconds=investigation.policy["duration_seconds"]
            ):
                raise InvestigationFailure("duration_exceeded")
            job.lease_expires_at = now + timedelta(seconds=investigation.policy["lease_seconds"])
            yield session, investigation, job

    async def load(self, claim: InvestigationClaim) -> dict:
        async with self.fenced(claim) as (_, investigation, __):
            return json.loads(json.dumps(investigation.checkpoint))

    async def begin_stage(self, claim: InvestigationClaim, stage: str) -> None:
        async with self.fenced(claim) as (session, investigation, _):
            investigation.stage = stage
            now = await database_now(session)
            await session.execute(
                insert(InvestigationStep)
                .values(
                    investigation_id=investigation.id,
                    attempt=claim.attempt,
                    stage=stage,
                    status="running",
                    details={"started_at": now.isoformat()},
                )
                .on_conflict_do_nothing(index_elements=["investigation_id", "attempt", "stage"])
            )

    async def checkpoint(
        self,
        claim: InvestigationClaim,
        stage: str,
        state: dict,
        evidence: list[InvestigationEvidence] | None = None,
    ) -> dict:
        state = json.loads(json.dumps(state))
        if len(json.dumps(state).encode()) > 60000:
            raise InvestigationFailure("state_size_exceeded")
        async with self.fenced(claim) as (session, investigation, _):
            if evidence:
                session.add_all(evidence)
                await session.flush()
            completed = list(dict.fromkeys([*state.get("completed_stages", []), stage]))
            state |= {"completed_stages": completed, "current_stage": stage}
            investigation.checkpoint = state
            step = await session.scalar(
                select(InvestigationStep).where(
                    InvestigationStep.investigation_id == investigation.id,
                    InvestigationStep.attempt == claim.attempt,
                    InvestigationStep.stage == stage,
                )
            )
            step.status = "completed"
            step.details = step.details | {
                "completed_at": (await database_now(session)).isoformat(),
                "evidence_count": len(state.get("evidence_refs", [])),
            }
        return state

    async def evidence(self, investigation_id: UUID) -> list[dict]:
        async with asyncio.timeout(10), self.sessions() as session, session.begin():
            rows = await session.scalars(
                select(InvestigationEvidence)
                .where(InvestigationEvidence.investigation_id == investigation_id)
                .order_by(InvestigationEvidence.reference)
                .limit(100)
            )
            return [EvidenceRead.model_validate(row).model_dump(mode="json") for row in rows]

    async def reserve(self, claim: InvestigationClaim, input_tokens: int) -> tuple[int, dict]:
        async with self.fenced(claim) as (_, investigation, __):
            policy = investigation.policy
            remaining = policy["max_output_tokens"] - investigation.output_tokens_reserved
            max_output = min(2048, remaining)
            if investigation.llm_calls >= policy["max_calls"] or max_output < 512:
                raise InvestigationFailure("llm_budget_exceeded")
            if investigation.input_tokens_reserved + input_tokens > policy["max_input_tokens"]:
                raise InvestigationFailure("input_budget_exceeded")
            investigation.llm_calls += 1
            investigation.input_tokens_reserved += input_tokens
            investigation.output_tokens_reserved += max_output
            return max_output, policy.copy()

    async def usage(
        self,
        claim: InvestigationClaim,
        input_tokens: int,
        output_tokens: int,
        reserved_input: int,
        reserved_output: int,
    ) -> None:
        if input_tokens > reserved_input or output_tokens > reserved_output:
            raise InvestigationFailure("provider_usage_exceeded")
        async with self.fenced(claim) as (_, investigation, __):
            investigation.actual_input_tokens += max(0, input_tokens)
            investigation.actual_output_tokens += max(0, output_tokens)

    async def finish(self, claim: InvestigationClaim, state: dict) -> dict:
        async with self.fenced(claim) as (session, investigation, job):
            now = await database_now(session)
            status = "completed" if state["aggregations"]["sufficient"] else "inconclusive"
            rows = list(
                await session.scalars(
                    select(InvestigationEvidence).where(
                        InvestigationEvidence.investigation_id == investigation.id
                    )
                )
            )
            allowed = {row.reference for row in rows}
            report = ReportRead(
                summary=(
                    f"Selected {state['aggregations']['selected_logs']} usable logs for "
                    f"incident {investigation.incident_id}. "
                    "Hypotheses are suggestions requiring human verification."
                    + (
                        " Evidence is insufficient for causal reasoning."
                        if status == "inconclusive"
                        else ""
                    )
                ),
                observed_evidence=state["observations"],
                hypotheses=state["hypotheses"],
                recommended_actions=state["actions"],
                limitations=[
                    "Root causes are unverified hypotheses, not incident resolution.",
                    "Evidence selection, excerpts and time windows are bounded; counts"
                    " describe selected logs only.",
                    "Full traces, database health and independent measurements may be missing.",
                    "Retrieved historical incidents and runbooks are supporting contex"
                    "t, not proof.",
                    f"Quarantined source count: {state['aggregations']['quarantined_sources']}.",
                    "Fake provider output is synthetic test data."
                    if investigation.provider == "fake"
                    else "Hosted model output may contain semantic errors despite valid citations.",
                ],
                metadata=ReportMetadata(
                    investigation_id=investigation.id,
                    incident_id=investigation.incident_id,
                    started_at=investigation.started_at,
                    completed_at=now,
                    provider=investigation.provider,
                    model=investigation.model,
                    status=status,
                    evidence_count=len(rows),
                    log_count=state["aggregations"]["selected_logs"],
                    synthetic_provider=investigation.provider == "fake",
                    calls_reserved=investigation.llm_calls,
                ),
            )
            references = set()
            for group in (report.observed_evidence, report.hypotheses, report.recommended_actions):
                for item in group:
                    references.update(item.evidence_ids)
            if not references <= allowed:
                raise InvestigationFailure("invalid_evidence_reference")
            payload = report.model_dump(mode="json")
            if len(json.dumps(payload).encode()) > 60000:
                raise InvestigationFailure("report_size_exceeded")
            session.add(InvestigationReport(investigation_id=investigation.id, payload=payload))
            await session.flush()
            session.add_all(
                [
                    InvestigationCitation(investigation_id=investigation.id, reference=reference)
                    for reference in sorted(references)
                ]
            )
            investigation.status = InvestigationStatus(status)
            investigation.completed_at = now
            investigation.error_code = None
            state |= {
                "status": status,
                "current_stage": "final_report",
                "completed_stages": [*state["completed_stages"], "final_report"],
            }
            investigation.checkpoint = state
            investigation.stage = "final_report"
            job.status = InvestigationJobStatus.COMPLETED
            job.finished_at = now
            job.lease_token = None
            job.lease_expires_at = None
            step = await session.scalar(
                select(InvestigationStep).where(
                    InvestigationStep.investigation_id == investigation.id,
                    InvestigationStep.attempt == claim.attempt,
                    InvestigationStep.stage == "final_report",
                )
            )
            step.status = "completed"
            step.details = step.details | {"completed_at": now.isoformat(), "status": status}
        return state
