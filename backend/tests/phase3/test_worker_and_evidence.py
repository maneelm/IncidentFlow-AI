import asyncio
import json
import os
import subprocess
import sys
from uuid import UUID, uuid4

import pytest
from conftest import Sandbox
from httpx import AsyncClient
from phase2.conftest import ingest, make_log
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.investigation.errors import InvestigationFailure
from app.investigation.graph import STAGES
from app.investigation.providers import FakeProvider, ProviderResponse
from app.models import Role
from app.models.investigations import (
    Investigation,
    InvestigationCitation,
    InvestigationEvidence,
    InvestigationJob,
    InvestigationReport,
)
from app.workers.investigation_worker import InvestigationWorker

pytestmark = pytest.mark.integration


class FailingProvider(FakeProvider):
    def __init__(self, kind: str):
        self.kind = kind

    async def generate(self, task, system, payload, schema, max_tokens):
        if self.kind == "unavailable":
            raise InvestigationFailure("provider_unavailable", retryable=True)
        if self.kind == "timeout":
            await asyncio.sleep(2)
        if self.kind == "malformed":
            return ProviderResponse("not json")
        response = await super().generate(task, system, payload, schema, max_tokens)
        data = json.loads(response.content)
        if task == "hypotheses":
            data["hypotheses"][0]["evidence_ids"] = [f"log:{uuid4()}"]
        return ProviderResponse(json.dumps(data))


async def due(sandbox: Sandbox) -> None:
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text("UPDATE investigation_jobs SET available_at=clock_timestamp()")
        )


@pytest.mark.parametrize(
    "kind,code",
    [
        ("unavailable", "provider_unavailable"),
        ("malformed", "invalid_structured_output"),
        ("bad_ref", "invalid_evidence_reference"),
        ("timeout", "provider_timeout"),
    ],
)
async def test_provider_failure_retry_exhaustion(
    sandbox: Sandbox, investigation: dict, kind: str, code: str
) -> None:
    if kind == "timeout":
        async with sandbox.sessions() as session, session.begin():
            row = await session.get(Investigation, UUID(investigation["id"]))
            row.policy = row.policy | {"timeout_seconds": 1}
    worker = InvestigationWorker(sandbox.sessions, sandbox.settings, FailingProvider(kind))
    for attempt in range(3):
        claim = await worker.claim_one()
        assert claim is not None
        assert await worker.process(claim) == ("retry_pending" if attempt < 2 else "failed")
        await due(sandbox)
    async with sandbox.sessions() as session:
        row = await session.get(Investigation, UUID(investigation["id"]))
        assert row.status == "failed" and row.error_code == code
        assert row.llm_calls == 3
        assert await session.get(InvestigationReport, row.id) is None


class BlockingProvider(FakeProvider):
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def generate(self, *args, **kwargs):
        self.entered.set()
        await self.release.wait()
        return await super().generate(*args, **kwargs)


async def test_remote_wait_has_no_open_transaction_and_cancellation_fences(
    client: AsyncClient,
    sandbox: Sandbox,
    investigation: dict,
) -> None:
    provider = BlockingProvider()
    worker = InvestigationWorker(sandbox.sessions, sandbox.settings, provider)
    claim = await worker.claim_one()
    work = asyncio.create_task(worker.process(claim))
    async with asyncio.timeout(15):
        await provider.entered.wait()
    async with sandbox.sessions() as session, session.begin():
        # Real NOWAIT locks prove the worker holds neither lifecycle nor claim locks.
        await session.scalar(
            select(InvestigationJob)
            .where(InvestigationJob.id == claim.job_id)
            .with_for_update(nowait=True)
        )
        await session.scalar(
            select(Investigation)
            .where(Investigation.id == claim.investigation_id)
            .with_for_update(nowait=True)
        )
        count = await session.scalar(
            text(
                "SELECT count(*) FROM pg_stat_activity WHERE "
                "datname=current_database() AND state='idle in transaction' AND "
                "query ILIKE '%investigation%'"
            )
        )
        assert count == 0
    cancelled = await client.post(
        f"/api/v1/investigations/{investigation['id']}/cancel",
        headers=sandbox.headers[Role.ENGINEER],
    )
    assert cancelled.status_code == 200
    provider.release.set()
    assert await work == "stale_claim"
    async with sandbox.sessions() as session:
        assert (await session.get(Investigation, claim.investigation_id)).status == "cancelled"
        assert await session.get(InvestigationReport, claim.investigation_id) is None


async def test_competing_claims_recovery_and_stale_owner(
    sandbox: Sandbox, investigation: dict, ai_worker: InvestigationWorker
) -> None:
    other = InvestigationWorker(sandbox.sessions, sandbox.settings)
    results = await asyncio.gather(ai_worker.claim_one(), other.claim_one())
    assert sum(item is not None for item in results) == 1
    old = results[0] or results[1]
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE investigation_jobs SET "
                "lease_expires_at=clock_timestamp()-interval '1 second'"
            )
        )
    new = await other.claim_one()
    assert new.token != old.token
    assert await ai_worker.process(old) == "stale_claim"
    assert await other.process(new) == "completed"
    assert await ai_worker.process(new) == "stale_claim"
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(InvestigationReport)) == 1


async def test_final_report_rollback_retry_uses_checkpoint_without_new_llm_calls(
    sandbox: Sandbox, investigation: dict, ai_worker: InvestigationWorker
) -> None:
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION reject_report() RETURNS trigger LANGUAGE plpgsql "
                "AS $$ BEGIN RAISE EXCEPTION 'sensitive error' USING "
                "ERRCODE='23514'; END $$"
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER reject_report BEFORE INSERT ON "
                "investigation_reports FOR EACH ROW EXECUTE FUNCTION "
                "reject_report()"
            )
        )
    claim = await ai_worker.claim_one()
    assert await ai_worker.process(claim) == "retry_pending"
    async with sandbox.sessions() as session:
        row = await session.get(Investigation, claim.investigation_id)
        assert row.llm_calls == 2 and row.checkpoint["completed_stages"] == list(STAGES[:-1])
        assert await session.scalar(select(func.count()).select_from(InvestigationCitation)) == 0
    async with sandbox.engine.begin() as connection:
        await connection.execute(text("DROP TRIGGER reject_report ON investigation_reports"))
    await due(sandbox)
    assert await ai_worker.process(await ai_worker.claim_one()) == "completed"
    async with sandbox.sessions() as session:
        assert (await session.get(Investigation, claim.investigation_id)).llm_calls == 2
        assert await session.scalar(select(func.count()).select_from(InvestigationReport)) == 1


async def test_input_and_call_budgets_are_durable(sandbox: Sandbox, investigation: dict) -> None:
    async with sandbox.sessions() as session, session.begin():
        row = await session.get(Investigation, UUID(investigation["id"]))
        row.policy = row.policy | {"max_input_tokens": 1024}
    worker = InvestigationWorker(sandbox.sessions, sandbox.settings)
    claim = await worker.claim_one()
    assert await worker.process(claim) == "failed"
    async with sandbox.sessions() as session:
        row = await session.get(Investigation, claim.investigation_id)
        assert row.error_code == "input_budget_exceeded" and row.llm_calls == 0


async def test_witness_context_and_rag_selection(
    client: AsyncClient,
    sandbox: Sandbox,
    detected: dict,
    credential: dict,
    runbook: dict,
    ai_settings,
    ai_worker: InvestigationWorker,
) -> None:
    await ingest(
        client,
        sandbox,
        credential,
        [
            make_log(
                "additional-context",
                level="info",
                error_code=None,
                message="Additional request context",
            )
        ],
    )
    response = await client.post(
        f"/api/v1/incidents/{detected['id']}/investigations",
        headers=sandbox.headers[Role.ENGINEER],
        json={},
    )
    assert await ai_worker.run_once() == 1
    evidence = (
        await client.get(
            f"/api/v1/investigations/{response.json()['id']}/evidence?limit=100",
            headers=sandbox.headers[Role.VIEWER],
        )
    ).json()["items"]
    witnesses = [item for item in evidence if item["role"] == "original_witness"]
    assert len(witnesses) == 3
    assert any(
        item["role"] == "context_log" and item["excerpt"] == "Additional request context"
        for item in evidence
    )
    assert any(
        item["source_type"] == "runbook_chunk" and item["provenance"]["document_version"] == 1
        for item in evidence
    )
    assert (
        len([item for item in evidence if item["source_type"] == "log"])
        <= sandbox.settings.investigation_max_logs
    )


async def test_nonexistent_evidence_and_report_citations_are_rejected(
    sandbox: Sandbox, investigation: dict
) -> None:
    with pytest.raises(IntegrityError):
        async with sandbox.sessions() as session, session.begin():
            missing = uuid4()
            session.add(
                InvestigationEvidence(
                    investigation_id=UUID(investigation["id"]),
                    reference=f"log:{missing}",
                    source_type="log",
                    log_id=missing,
                    label="missing",
                    excerpt="missing",
                    role="context_log",
                    provenance={},
                    quarantined=False,
                )
            )
            await session.flush()
    worker = InvestigationWorker(sandbox.sessions, sandbox.settings)
    assert await worker.process(await worker.claim_one()) == "completed"
    with pytest.raises(IntegrityError):
        async with sandbox.sessions() as session, session.begin():
            session.add(
                InvestigationCitation(
                    investigation_id=UUID(investigation["id"]), reference=f"log:{uuid4()}"
                )
            )
            await session.flush()


async def test_lease_exhaustion_is_visible(
    sandbox: Sandbox, investigation: dict, ai_worker: InvestigationWorker
) -> None:
    async with sandbox.engine.begin() as connection:
        await connection.execute(text("UPDATE investigation_jobs SET max_attempts=1"))
    await ai_worker.claim_one()
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE investigation_jobs SET "
                "lease_expires_at=clock_timestamp()-interval '1 second'"
            )
        )
    assert await ai_worker.claim_one() is None
    async with sandbox.sessions() as session:
        row = await session.get(Investigation, UUID(investigation["id"]))
        assert row.status == "failed" and row.error_code == "lease_exhausted"


async def test_crashed_process_claim_is_recoverable(
    sandbox: Sandbox, investigation: dict, ai_worker: InvestigationWorker
) -> None:
    async with sandbox.engine.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    program = """
import asyncio,os
from secrets import token_urlsafe
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.config import Settings
from app.db.session import make_session_factory
from app.workers.investigation_worker import InvestigationWorker
async def crash():
    url=os.environ['CRASH_URL']
    engine=create_async_engine(url,connect_args={
        'server_settings':{'search_path':os.environ['CRASH_SCHEMA']}
    })
    settings=Settings(_env_file=None,database_url=url,jwt_secret=token_urlsafe(48))
    assert await InvestigationWorker(make_session_factory(engine),settings).claim_one()
    os._exit(19)
asyncio.run(crash())
"""
    environment = os.environ | {
        "PYTHONPATH": "backend",
        "CRASH_URL": str(sandbox.settings.database_url),
        "CRASH_SCHEMA": schema,
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        program,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    async with asyncio.timeout(15):
        assert await process.wait() == 19
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE investigation_jobs SET "
                "lease_expires_at=clock_timestamp()-interval '1 second'"
            )
        )
    assert await ai_worker.process(await ai_worker.claim_one()) == "completed"


async def test_stop_does_not_claim(
    sandbox: Sandbox, investigation: dict, ai_worker: InvestigationWorker
) -> None:
    stop = asyncio.Event()
    stop.set()
    assert await ai_worker.run_once(stop) == 0
