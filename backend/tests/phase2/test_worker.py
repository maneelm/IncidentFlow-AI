import asyncio
import os
import subprocess
import sys
from uuid import UUID

import pytest
from conftest import Sandbox
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.models import DetectionJob, DetectionOccurrence, Incident, IncidentEvent, Role
from app.models.enums import JobStatus
from app.workers.detection_worker import DetectionWorker

from .conftest import drain, ingest, make_log

pytestmark = pytest.mark.integration


async def test_concurrent_workers_create_one_current_detection(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    await ingest(client, sandbox, credential, [make_log(f"event-{i}") for i in range(20)])
    other = DetectionWorker(sandbox.sessions, sandbox.settings)
    first, second = await asyncio.gather(worker.claim_one(), other.claim_one())
    assert sum(claim is not None for claim in [first, second]) == 1
    await worker.process(first or second)
    await asyncio.gather(drain(worker), drain(other))
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Incident)) == 1
        assert await session.scalar(select(func.count()).select_from(DetectionOccurrence)) == 1
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 1
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DetectionJob)
                .where(DetectionJob.status == "completed")
            )
            == 20
        )


async def test_skip_locked_allows_another_service_to_progress(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    first = await ingest(client, sandbox, credential, [make_log("first")])
    headers = sandbox.headers[Role.ADMIN]
    service = (
        await client.post(
            "/api/v1/services", headers=headers, json={"name": "second", "environment": "test"}
        )
    ).json()
    key = (
        await client.post(
            "/api/v1/ingestion-credentials",
            headers=headers,
            json={"service_id": service["id"], "name": "other"},
        )
    ).json()
    second = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": key["token"]},
        json={"service_id": service["id"], "logs": [make_log("second")]},
    )
    assert second.status_code == 202
    # One connection holds a REAL row lock while a second worker claims another service.
    async with sandbox.sessions() as locking, locking.begin():
        await locking.scalar(
            select(DetectionJob)
            .where(DetectionJob.id == UUID(first["items"][0]["job_id"]))
            .with_for_update()
        )
        async with asyncio.timeout(3):
            claimed = await worker.claim_one()
        assert str(claimed.job_id) == second.json()["items"][0]["job_id"]
    assert await worker.process(claimed) == "completed"


@pytest.mark.parametrize(
    "table", ["incident_events", "detection_occurrences", "detection_evidence"]
)
async def test_atomic_effects_rollback_then_retry_succeeds(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
    table: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await ingest(client, sandbox, credential, [make_log(f"event-{i}") for i in range(3)])
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text("""
            CREATE FUNCTION reject_detection() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'sensitive worker failure text' USING ERRCODE='23514'; END $$
        """)
        )
        await connection.execute(
            text(
                f"CREATE TRIGGER reject_detection BEFORE INSERT ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_detection()"
            )
        )
    claim = await worker.claim_one()
    assert await worker.process(claim) == "retry_pending"
    assert "sensitive worker failure text" not in caplog.text
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Incident)) == 0
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 0
        assert await session.scalar(select(func.count()).select_from(DetectionOccurrence)) == 0
        job = await session.get(DetectionJob, claim.job_id)
        assert job.status == JobStatus.PENDING and job.attempts == 1
        assert job.last_error_code == "constraint_error"
        assert job.available_at > job.created_at
    assert await worker.claim_one() is None  # Backoff blocks this service, preserving order.
    async with sandbox.engine.begin() as connection:
        await connection.execute(text(f"DROP TRIGGER reject_detection ON {table}"))
        await connection.execute(text("UPDATE detection_jobs SET available_at = clock_timestamp()"))
    assert await drain(worker) == 3
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Incident)) == 1
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 1
        assert (await session.get(DetectionJob, claim.job_id)).total_attempts == 2


async def test_bounded_failures_visible_and_admin_retry(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    result = await ingest(client, sandbox, credential, [make_log()])
    job_id = result["items"][0]["job_id"]
    async with sandbox.engine.begin() as connection:
        await connection.execute(text("UPDATE detection_jobs SET max_attempts = 1"))
    claim = await worker.claim_one()
    assert await worker.record_failure(claim, "processing_error") == "failed"
    assert await worker.claim_one() is None
    page = (
        await client.get(
            "/api/v1/detection-jobs?status=failed", headers=sandbox.headers[Role.ADMIN]
        )
    ).json()
    assert page["total"] == 1 and page["items"][0]["last_error_code"] == "processing_error"
    assert "lease_token" not in page["items"][0]
    retried = await client.post(
        f"/api/v1/detection-jobs/{job_id}/retry", headers=sandbox.headers[Role.ADMIN]
    )
    assert retried.status_code == 200 and retried.json()["total_attempts"] == 1
    assert retried.json()["requeues"] == 1 and retried.json()["attempts"] == 0
    assert (
        await client.post(
            f"/api/v1/detection-jobs/{job_id}/retry", headers=sandbox.headers[Role.ADMIN]
        )
    ).status_code == 409
    assert await drain(worker) == 1
    async with sandbox.sessions() as session:
        job = await session.get(DetectionJob, UUID(job_id))
        assert job.total_attempts == 2 and job.status == JobStatus.COMPLETED


async def test_expired_claim_recovery_fences_old_worker(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    await ingest(client, sandbox, credential, [make_log(f"event-{i}") for i in range(3)])
    abandoned = await worker.claim_one()
    assert await worker.claim_one() is None
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE detection_jobs SET lease_expires_at = "
                "clock_timestamp() - interval '1 second' "
                "WHERE id = :id"
            ),
            {"id": abandoned.job_id},
        )
    recovered = await worker.claim_one()
    assert recovered.job_id == abandoned.job_id and recovered.token != abandoned.token
    assert await worker.process(abandoned) == "stale_claim"
    assert await worker.process(recovered) == "completed"
    await drain(worker)
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Incident)) == 1
        assert (await session.get(DetectionJob, recovered.job_id)).attempts == 2


async def test_expired_final_attempt_becomes_failed(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    worker: DetectionWorker,
) -> None:
    await ingest(client, sandbox, credential, [make_log()])
    async with sandbox.engine.begin() as connection:
        await connection.execute(text("UPDATE detection_jobs SET max_attempts = 1"))
    abandoned = await worker.claim_one()
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE detection_jobs SET lease_expires_at = "
                "clock_timestamp() - interval '1 second'"
            )
        )
    recovered = await worker.claim_one()
    assert recovered.job_id == abandoned.job_id and recovered.token is None
    assert await worker.process(recovered) == "failed"
    async with sandbox.sessions() as session:
        assert (
            await session.get(DetectionJob, abandoned.job_id)
        ).last_error_code == "lease_expired"


async def test_real_process_crash_leaves_recoverable_committed_claim(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    await ingest(client, sandbox, credential, [make_log(f"event-{i}") for i in range(3)])
    async with sandbox.engine.connect() as connection:
        schema = await connection.scalar(text("SELECT current_schema()"))
    code = """
import asyncio, os
from secrets import token_urlsafe
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from app.core.config import Settings
from app.db.session import make_session_factory
from app.workers.detection_worker import DetectionWorker
async def crash():
    url = os.environ['CRASH_TEST_URL']
    engine = create_async_engine(url, poolclass=NullPool, connect_args={
        'server_settings': {'search_path': os.environ['CRASH_TEST_SCHEMA']}
    })
    settings = Settings(_env_file=None, database_url=url,
                        jwt_secret=token_urlsafe(48), environment='test')
    claim = await DetectionWorker(make_session_factory(engine), settings).claim_one()
    assert claim is not None
    os._exit(17)
asyncio.run(crash())
"""
    environment = os.environ | {
        "PYTHONPATH": "backend",
        "CRASH_TEST_URL": str(sandbox.settings.database_url),
        "CRASH_TEST_SCHEMA": schema,
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        code,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    async with asyncio.timeout(15):
        assert await process.wait() == 17
    async with sandbox.sessions() as session:
        crashed = await session.scalar(
            select(DetectionJob).where(DetectionJob.status == "processing")
        )
        assert crashed.attempts == 1
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE detection_jobs SET lease_expires_at = "
                "clock_timestamp() - interval '1 second' "
                "WHERE status = 'processing'"
            )
        )
    await drain(worker)
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Incident)) == 1
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 1
        assert (await session.get(DetectionJob, crashed.id)).attempts == 2


async def test_shutdown_stops_claiming_without_losing_jobs(
    client: AsyncClient, sandbox: Sandbox, credential: dict, worker: DetectionWorker
) -> None:
    await ingest(client, sandbox, credential, [make_log()])
    stop = asyncio.Event()
    stop.set()
    assert await worker.run_once(stop) == 0
    assert await drain(worker) == 1


async def test_partial_unique_constraint_rejects_duplicate_current_group(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    await ingest(client, sandbox, credential, [make_log(f"event-{i}") for i in range(3)])
    await drain(worker)
    with pytest.raises(IntegrityError):
        async with sandbox.sessions() as session, session.begin():
            original = await session.scalar(select(DetectionOccurrence))
            from app.schemas.incidents import IncidentCreate
            from app.services.incidents import IncidentService

            manual = await IncidentService(session).create(
                IncidentCreate(
                    title="Extra",
                    description="Test constraint",
                    severity="high",
                    service_id=sandbox.service.id,
                ),
                sandbox.users[Role.ADMIN],
            )
            session.add(
                DetectionOccurrence(
                    rule_id=original.rule_id,
                    service_id=original.service_id,
                    incident_id=manual.id,
                    is_current=True,
                    rule_version=1,
                    rule_snapshot=original.rule_snapshot,
                    window_start=original.window_start,
                    window_end=original.window_end,
                    matching_count=3,
                )
            )
            await session.flush()
