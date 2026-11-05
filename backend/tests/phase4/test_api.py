import asyncio
from uuid import UUID, uuid4

import pytest
from conftest import Sandbox
from httpx import AsyncClient
from sqlalchemy import select, text

from app.models import Role
from app.models.resolution import (
    ResolutionApproval,
    ResolutionEvent,
    ResolutionJob,
    ResolutionProposal,
)
from phase4.conftest import create, payload, transition

pytestmark = pytest.mark.integration


async def test_catalog_requires_auth_and_publishes_schemas(client: AsyncClient, sandbox: Sandbox):
    assert (await client.get("/api/v1/action-catalog")).status_code == 401
    rows = (await client.get("/api/v1/action-catalog", headers=sandbox.headers[Role.VIEWER])).json()
    assert len(rows) == 5 and all(row["approval_required"] for row in rows)
    assert (
        await client.get("/api/v1/action-catalog/os.system", headers=sandbox.headers[Role.ADMIN])
    ).status_code == 422


async def test_proposal_idempotency_and_conflicting_reuse(client, sandbox, incident, state):
    body = payload({"version": 1}, state)
    first = await create(client, sandbox, incident.id, body)
    replay = await create(client, sandbox, incident.id, body)
    assert replay == first
    body["action_id"] = "inspect_service_health"
    assert (
        await client.post(
            f"/api/v1/incidents/{incident.id}/resolution-proposals",
            headers=sandbox.headers[Role.ENGINEER],
            json=body,
        )
    ).status_code == 409
    events = (
        await client.get(
            f"/api/v1/resolution-proposals/{first['id']}/events",
            headers=sandbox.headers[Role.VIEWER],
        )
    ).json()
    assert events["total"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"action_id": "os.system"},
        {"parameters": {"command": "restart real database"}},
        {"expected_incident_version": 0},
        {"evidence_refs": ["log:invented"]},
    ],
)
async def test_invalid_payloads(client, sandbox, incident, state, change):
    body = payload({"version": 1}, state, **change)
    assert (
        await client.post(
            f"/api/v1/incidents/{incident.id}/resolution-proposals",
            headers=sandbox.headers[Role.ENGINEER],
            json=body,
        )
    ).status_code == 422


async def test_invalid_incident_and_target(client, sandbox, state, incident):
    body = payload({"version": 1}, state)
    assert (
        await client.post(
            f"/api/v1/incidents/{uuid4()}/resolution-proposals",
            headers=sandbox.headers[Role.ENGINEER],
            json=body,
        )
    ).status_code == 404
    body["parameters"]["state_id"] = str(uuid4())
    assert (
        await client.post(
            f"/api/v1/incidents/{incident.id}/resolution-proposals",
            headers=sandbox.headers[Role.ENGINEER],
            json=body,
        )
    ).status_code == 409


@pytest.mark.parametrize("operation", ["submit", "approve", "reject", "cancel"])
async def test_viewer_cannot_mutate(client, sandbox, pending, operation):
    body = {"expected_version": pending["version"]}
    if operation in {"approve", "reject"}:
        body["reason"] = "Not authorized to review."
    response = await client.post(
        f"/api/v1/resolution-proposals/{pending['id']}/{operation}",
        headers=sandbox.headers[Role.VIEWER],
        json=body,
    )
    assert response.status_code == 403
    assert (
        await client.get(
            f"/api/v1/resolution-proposals/{pending['id']}", headers=sandbox.headers[Role.VIEWER]
        )
    ).status_code == 200


async def test_viewer_cannot_create_or_reset(client, sandbox, incident, state):
    assert (
        await client.post(
            f"/api/v1/incidents/{incident.id}/resolution-proposals",
            headers=sandbox.headers[Role.VIEWER],
            json=payload({"version": 1}, state),
        )
    ).status_code == 403
    for role in (Role.VIEWER, Role.ENGINEER):
        assert (
            await client.post(
                f"/api/v1/services/{sandbox.service.id}/synthetic-state",
                headers=sandbox.headers[role],
                json={"expected_version": 1, "scenario": "healthy"},
            )
        ).status_code == 403


async def test_separation_of_duties_and_atomic_queue(client, sandbox, pending):
    denied = await client.post(
        f"/api/v1/resolution-proposals/{pending['id']}/approve",
        headers=sandbox.headers[Role.ENGINEER],
        json={
            "expected_version": pending["version"],
            "reason": "I authored this synthetic mutation.",
        },
    )
    assert denied.status_code == 403
    queued = await transition(client, sandbox, pending, "approve", Role.ADMIN)
    assert queued["status"] == "queued" and queued["version"] == 4
    async with sandbox.sessions() as session:
        assert await session.scalar(
            select(ResolutionJob).where(ResolutionJob.proposal_id == UUID(queued["id"]))
        )
        approval = await session.scalar(
            select(ResolutionApproval).where(ResolutionApproval.proposal_id == UUID(queued["id"]))
        )
        assert approval.actor_id == sandbox.users[Role.ADMIN].id
        assert approval.payload_hash == queued["payload_hash"]


async def test_read_only_self_review_allowed(client, sandbox, incident, state):
    proposal = await create(
        client,
        sandbox,
        incident.id,
        payload({"version": 1}, state, action_id="inspect_service_health"),
    )
    pending = await transition(client, sandbox, proposal, "submit")
    assert (await transition(client, sandbox, pending, "approve"))["status"] == "queued"


async def test_concurrent_reviews_only_one_commits(client, sandbox, pending):
    async def review():
        return await client.post(
            f"/api/v1/resolution-proposals/{pending['id']}/approve",
            headers=sandbox.headers[Role.ADMIN],
            json={
                "expected_version": pending["version"],
                "reason": "Second reviewer approves synthetic test.",
            },
        )

    responses = await asyncio.gather(review(), review())
    assert sorted(response.status_code for response in responses) == [200, 409]
    async with sandbox.sessions() as session:
        assert len(list(await session.scalars(select(ResolutionApproval)))) == 1
        assert len(list(await session.scalars(select(ResolutionJob)))) == 1


@pytest.mark.parametrize("operation", ["approve", "reject"])
async def test_expired_review_persists_terminal_status(client, sandbox, pending, operation):
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE resolution_proposals SET approval_deadline=clock_timestamp"
                "()-interval '1 second'"
            )
        )
    result = await transition(client, sandbox, pending, operation, Role.ADMIN)
    assert result["status"] == "expired"
    async with sandbox.sessions() as session:
        assert not list(await session.scalars(select(ResolutionApproval)))
        assert not list(await session.scalars(select(ResolutionJob)))


async def test_invalid_transition_and_stale_version(client, sandbox, proposal):
    for operation in ("approve", "reject"):
        response = await client.post(
            f"/api/v1/resolution-proposals/{proposal['id']}/{operation}",
            headers=sandbox.headers[Role.ADMIN],
            json={
                "expected_version": proposal["version"],
                "reason": "Review before submission is invalid.",
            },
        )
        assert response.status_code == 409
    pending = await transition(client, sandbox, proposal, "submit")
    response = await client.post(
        f"/api/v1/resolution-proposals/{pending['id']}/submit",
        headers=sandbox.headers[Role.ENGINEER],
        json={"expected_version": 1},
    )
    assert response.status_code == 409


async def test_approval_audit_failure_rolls_everything_back(client, sandbox, pending):
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION reject_resolution_insert() RETURNS trigger LANGUA"
                "GE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test failure'; END $$"
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER reject_resolution_insert BEFORE INSERT ON resoluti"
                "on_events FOR EACH ROW EXECUTE FUNCTION reject_resolution_insert("
                ")"
            )
        )
    response = await client.post(
        f"/api/v1/resolution-proposals/{pending['id']}/approve",
        headers=sandbox.headers[Role.ADMIN],
        json={
            "expected_version": pending["version"],
            "reason": "Approval should roll back atomically.",
        },
    )
    assert response.status_code == 500
    async with sandbox.sessions() as session:
        assert (
            await session.get(ResolutionProposal, UUID(pending["id"]))
        ).status == "pending_approval"
        assert not list(await session.scalars(select(ResolutionApproval)))
        assert not list(await session.scalars(select(ResolutionJob)))
        assert len(list(await session.scalars(select(ResolutionEvent)))) == 2
