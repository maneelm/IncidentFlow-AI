from uuid import uuid4

import pytest
from conftest import Sandbox
from httpx import AsyncClient
from phase3 import conftest as phase3_fixtures

from app.models import Role
from app.workers.resolution_worker import ResolutionWorker

credential = phase3_fixtures.credential
rule = phase3_fixtures.rule
detected = phase3_fixtures.detected
ai_settings = phase3_fixtures.ai_settings
ai_worker = phase3_fixtures.ai_worker
runbook = phase3_fixtures.runbook
investigation = phase3_fixtures.investigation


@pytest.fixture
async def state(client: AsyncClient, sandbox: Sandbox) -> dict:
    response = await client.post(
        f"/api/v1/services/{sandbox.service.id}/synthetic-state",
        headers=sandbox.headers[Role.ADMIN],
        json={"scenario": "database_exhaustion", "expected_version": 0},
    )
    assert response.status_code == 200, response.text
    return response.json()


def payload(incident: dict, state: dict, **changes) -> dict:
    return {
        "client_request_id": str(uuid4()),
        "expected_incident_version": incident["version"],
        "action_id": "simulate_connection_pool_recovery",
        "parameters": {"state_id": state["id"], "expected_state_version": state["version"]},
        "explanation": "Human requests isolated synthetic pool recovery after reviewing evidence.",
        **changes,
    }


async def create(client: AsyncClient, sandbox: Sandbox, incident_id, body: dict) -> dict:
    response = await client.post(
        f"/api/v1/incidents/{incident_id}/resolution-proposals",
        headers=sandbox.headers[Role.ENGINEER],
        json=body,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def transition(
    client: AsyncClient, sandbox: Sandbox, proposal: dict, operation: str, role=Role.ENGINEER
) -> dict:
    body = {"expected_version": proposal["version"]}
    if operation in {"approve", "reject"}:
        body["reason"] = "Reviewed the synthetic target, evidence and bounded action."
    response = await client.post(
        f"/api/v1/resolution-proposals/{proposal['id']}/{operation}",
        headers=sandbox.headers[role],
        json=body,
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
async def proposal(client: AsyncClient, sandbox: Sandbox, incident, state: dict) -> dict:
    return await create(client, sandbox, incident.id, payload({"version": incident.version}, state))


@pytest.fixture
async def pending(client: AsyncClient, sandbox: Sandbox, proposal: dict) -> dict:
    return await transition(client, sandbox, proposal, "submit")


@pytest.fixture
async def queued(client: AsyncClient, sandbox: Sandbox, pending: dict) -> dict:
    result = await transition(client, sandbox, pending, "approve", Role.ADMIN)
    assert result["status"] == "queued"
    return result


@pytest.fixture
def worker(sandbox: Sandbox) -> ResolutionWorker:
    return ResolutionWorker(sandbox.sessions, sandbox.settings)
