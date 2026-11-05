import pytest
from conftest import Sandbox
from httpx import AsyncClient
from phase2 import conftest as phase2_fixtures

from app.models import Role
from app.workers.detection_worker import DetectionWorker
from app.workers.investigation_worker import InvestigationWorker

credential = phase2_fixtures.credential
rule = phase2_fixtures.rule
ingest = phase2_fixtures.ingest
make_log = phase2_fixtures.make_log


@pytest.fixture
def ai_settings(sandbox: Sandbox):
    sandbox.settings.investigation_llm_provider = "fake"
    sandbox.settings.investigation_llm_model = "fake-v1"
    return sandbox.settings


@pytest.fixture
async def detected(client: AsyncClient, sandbox: Sandbox, credential: dict, rule: dict) -> dict:
    await ingest(client, sandbox, credential, [make_log(f"phase3-{i}") for i in range(8)])
    worker = DetectionWorker(sandbox.sessions, sandbox.settings)
    while await worker.run_once():
        pass
    incidents = (
        await client.get("/api/v1/incidents", headers=sandbox.headers[Role.ENGINEER])
    ).json()
    return incidents["items"][0]


@pytest.fixture
async def runbook(client: AsyncClient, sandbox: Sandbox) -> dict:
    response = await client.post(
        "/api/v1/runbooks",
        headers=sandbox.headers[Role.ADMIN],
        json={
            "title": "Database pool troubleshooting",
            "service_id": str(sandbox.service.id),
            "tags": ["database", "connections"],
            "content": "# DB_CONNECTION_TIMEOUT\nInspect active connections, pool sizing "
            "and potential connection leaks using read-only views. Do not "
            "change production configuration without review.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def investigation(
    client: AsyncClient, sandbox: Sandbox, ai_settings, detected: dict, runbook: dict
) -> dict:
    response = await client.post(
        f"/api/v1/incidents/{detected['id']}/investigations",
        headers=sandbox.headers[Role.ENGINEER],
        json={},
    )
    assert response.status_code == 202, response.text
    return response.json()


@pytest.fixture
def ai_worker(sandbox: Sandbox, ai_settings) -> InvestigationWorker:
    return InvestigationWorker(sandbox.sessions, sandbox.settings)
