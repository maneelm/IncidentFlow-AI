import asyncio
from uuid import UUID

import pytest
from sqlalchemy import select, text

from app.models import Role
from app.models.resolution import (
    ResolutionEvent,
    ResolutionExecution,
    ResolutionJob,
    ResolutionProposal,
    SyntheticServiceState,
)
from phase4.conftest import create, payload, transition

pytestmark = pytest.mark.integration


async def test_recovery_atomic_result_audit_and_incident_unchanged(
    client, sandbox, queued, worker, incident
):
    before = (
        await client.get(f"/api/v1/incidents/{incident.id}", headers=sandbox.headers[Role.ADMIN])
    ).json()
    assert await worker.run_once() == 1
    result = (
        await client.get(
            f"/api/v1/resolution-proposals/{queued['id']}/result",
            headers=sandbox.headers[Role.VIEWER],
        )
    ).json()
    assert result["result"]["synthetic"] and result["verification"]["contract_satisfied"]
    assert result["after_version"] == result["before_version"] + 1
    assert result["result"]["after"]["waiting_requests"] == 0
    assert result["verification"]["incident_resolution_confirmed"] is False
    assert (
        await client.get(f"/api/v1/incidents/{incident.id}", headers=sandbox.headers[Role.ADMIN])
    ).json() == before
    assert await worker.run_once() == 0
    async with sandbox.sessions() as session:
        assert len(list(await session.scalars(select(ResolutionExecution)))) == 1
        events = list(
            await session.scalars(
                select(ResolutionEvent).order_by(ResolutionEvent.proposal_version)
            )
        )
        assert [event.proposal_version for event in events] == list(range(1, 8))
        assert (
            events[-2].event_type == "verification_completed"
            and events[-1].event_type == "execution_succeeded"
        )


@pytest.mark.parametrize(
    "action", ["inspect_service_health", "inspect_database_connections", "inspect_request_latency"]
)
async def test_readonly_diagnostics_do_not_mutate(client, sandbox, incident, state, worker, action):
    proposal = await create(
        client, sandbox, incident.id, payload({"version": 1}, state, action_id=action)
    )
    pending = await transition(client, sandbox, proposal, "submit")
    queued = await transition(client, sandbox, pending, "approve")
    assert await worker.run_once() == 1
    result = (
        await client.get(
            f"/api/v1/resolution-proposals/{queued['id']}/result",
            headers=sandbox.headers[Role.ADMIN],
        )
    ).json()
    assert result["before_version"] == result["after_version"] == 1
    assert result["result"]["before"] == result["result"]["after"]


@pytest.mark.parametrize("terminal", ["reject", "cancel"])
async def test_terminal_proposals_never_execute(client, sandbox, pending, worker, terminal):
    await transition(
        client, sandbox, pending, terminal, Role.ADMIN if terminal == "reject" else Role.ENGINEER
    )
    assert await worker.run_once() == 0
    async with sandbox.sessions() as session:
        assert not list(await session.scalars(select(ResolutionExecution)))


async def test_pending_expiry_is_swept_without_a_job(sandbox, pending, worker):
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE resolution_proposals SET approval_deadline=clock_timestamp"
                "()-interval '1 second'"
            )
        )
    assert await worker.expire_one()
    async with sandbox.sessions() as session:
        assert (await session.get(ResolutionProposal, UUID(pending["id"]))).status == "expired"


@pytest.mark.parametrize("invalidate", ["expiry", "incident", "state"])
async def test_execution_rechecks_authorized_context(sandbox, queued, worker, invalidate):
    if invalidate == "expiry":
        sql = (
            "UPDATE resolution_proposals SET approval_deadline=clock_timestamp"
            "()-interval '1 second'"
        )
    elif invalidate == "incident":
        sql = "UPDATE incidents SET version=version+1"
    else:
        sql = "UPDATE synthetic_service_states SET version=version+1"
    async with sandbox.engine.begin() as connection:
        await connection.execute(text(sql))
    await worker.run_once()
    async with sandbox.sessions() as session:
        assert (await session.get(ResolutionProposal, UUID(queued["id"]))).status == "expired"
        assert not list(await session.scalars(select(ResolutionExecution)))


async def test_concurrent_claims_and_replay_are_fenced(sandbox, queued, worker):
    claims = await asyncio.gather(worker.claim_one(), worker.claim_one())
    assert sum(claim is not None for claim in claims) == 1
    claim = next(claim for claim in claims if claim is not None)
    assert await worker.process(claim) == "succeeded"
    assert await worker.process(claim) == "stale_claim"
    async with sandbox.sessions() as session:
        assert len(list(await session.scalars(select(ResolutionExecution)))) == 1
        assert (await session.scalar(select(SyntheticServiceState))).version == 2


async def test_expired_claim_recovered_and_old_token_fenced(sandbox, queued, worker):
    first = await worker.claim_one()
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE resolution_jobs SET lease_expires_at=clock_timestamp()-interval '1 second'"
            )
        )
    second = await worker.claim_one()
    assert second.token != first.token and second.attempt == 2
    assert await worker.process(first) == "stale_claim"
    assert await worker.process(second) == "succeeded"


async def test_cancel_running_prevents_effect(client, sandbox, queued, worker):
    claim = await worker.claim_one()
    current = (
        await client.get(
            f"/api/v1/resolution-proposals/{queued['id']}", headers=sandbox.headers[Role.ENGINEER]
        )
    ).json()
    await transition(client, sandbox, current, "cancel")
    assert await worker.process(claim) == "stale_claim"
    async with sandbox.sessions() as session:
        assert (await session.scalar(select(SyntheticServiceState))).version == 1


async def test_effect_and_result_roll_back_then_retry_once(sandbox, queued, worker):
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION reject_execution_insert() RETURNS trigger LANGUAG"
                "E plpgsql AS $$ BEGIN RAISE EXCEPTION 'test failure'; END $$"
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER reject_execution_insert BEFORE INSERT ON resolutio"
                "n_executions FOR EACH ROW EXECUTE FUNCTION reject_execution_inser"
                "t()"
            )
        )
    claim = await worker.claim_one()
    assert await worker.process(claim) == "retry_pending"
    async with sandbox.sessions() as session:
        assert (await session.scalar(select(SyntheticServiceState))).version == 1
        assert not list(await session.scalars(select(ResolutionExecution)))
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text("DROP TRIGGER reject_execution_insert ON resolution_executions")
        )
        await connection.execute(text("UPDATE resolution_jobs SET available_at=clock_timestamp()"))
    assert await worker.run_once() == 1
    async with sandbox.sessions() as session:
        assert (await session.scalar(select(SyntheticServiceState))).version == 2
        assert len(list(await session.scalars(select(ResolutionExecution)))) == 1


async def test_retry_budget_exhaustion(sandbox, queued, worker):
    for attempt in range(1, 4):
        claim = await worker.claim_one()
        assert claim.attempt == attempt
        assert await worker.failure(claim) == ("failed" if attempt == 3 else "retry_pending")
        async with sandbox.engine.begin() as connection:
            await connection.execute(
                text("UPDATE resolution_jobs SET available_at=clock_timestamp()")
            )
    async with sandbox.sessions() as session:
        assert (await session.scalar(select(ResolutionJob))).status == "failed"
        assert (await session.scalar(select(SyntheticServiceState))).version == 1


async def test_verification_failure_has_result_without_false_success(
    client, sandbox, incident, state, worker
):
    reset = (
        await client.post(
            f"/api/v1/services/{sandbox.service.id}/synthetic-state",
            headers=sandbox.headers[Role.ADMIN],
            json={"scenario": "dependency_outage", "expected_version": state["version"]},
        )
    ).json()
    proposal = await create(client, sandbox, incident.id, payload({"version": 1}, reset))
    pending = await transition(client, sandbox, proposal, "submit")
    queued = await transition(client, sandbox, pending, "approve", Role.ADMIN)
    assert await worker.run_once() == 1
    async with sandbox.sessions() as session:
        assert (await session.get(ResolutionProposal, UUID(queued["id"]))).status == "failed"
        result = await session.scalar(select(ResolutionExecution))
        assert result.verification["contract_satisfied"] is False
        assert result.before_version == result.after_version


async def test_stop_flag_claims_nothing(queued, worker):
    stop = asyncio.Event()
    stop.set()
    assert await worker.run_once(stop) == 0
