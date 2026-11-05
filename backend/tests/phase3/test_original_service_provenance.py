import pytest
from conftest import Sandbox
from httpx import AsyncClient

from app.models import Role
from app.workers.investigation_worker import InvestigationWorker

pytestmark = pytest.mark.integration


async def test_original_witnesses_survive_manual_incident_service_change(
    client: AsyncClient,
    sandbox: Sandbox,
    detected: dict,
    ai_settings,
    ai_worker: InvestigationWorker,
) -> None:
    headers = sandbox.headers[Role.ADMIN]
    other = (
        await client.post(
            "/api/v1/services",
            headers=headers,
            json={"name": "other-service", "environment": "test"},
        )
    ).json()
    response = await client.patch(
        f"/api/v1/incidents/{detected['id']}",
        headers=headers,
        json={"expected_version": 1, "service_id": other["id"]},
    )
    assert response.status_code == 200
    created = await client.post(
        f"/api/v1/incidents/{detected['id']}/investigations", headers=headers, json={}
    )
    assert created.status_code == 202
    assert await ai_worker.run_once() == 1
    evidence = (
        await client.get(
            f"/api/v1/investigations/{created.json()['id']}/evidence?limit=100", headers=headers
        )
    ).json()["items"]
    witnesses = [item for item in evidence if item["role"] == "original_witness"]
    assert len(witnesses) == 3
    assert all(item["provenance"]["service_id"] == str(sandbox.service.id) for item in witnesses)
