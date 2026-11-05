from uuid import UUID

import pytest
from conftest import Sandbox
from httpx import AsyncClient

from app.investigation.graph import STAGES
from app.models import Incident, Role
from app.models.investigations import Investigation, InvestigationReport
from app.workers.investigation_worker import InvestigationWorker

pytestmark = pytest.mark.integration


async def test_real_langgraph_with_fake_provider(
    client: AsyncClient,
    sandbox: Sandbox,
    investigation: dict,
    runbook: dict,
    ai_worker: InvestigationWorker,
    detected: dict,
) -> None:
    assert await ai_worker.run_once() == 1
    path = f"/api/v1/investigations/{investigation['id']}"
    current = (await client.get(path, headers=sandbox.headers[Role.VIEWER])).json()
    assert current["status"] == "completed", current
    report_response = await client.get(path + "/report", headers=sandbox.headers[Role.VIEWER])
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()
    assert report["metadata"]["synthetic_provider"] is True
    assert len(report["hypotheses"]) == 2
    evidence = (
        await client.get(path + "/evidence?limit=100", headers=sandbox.headers[Role.VIEWER])
    ).json()
    ids = {item["reference"] for item in evidence["items"]}
    for item in report["hypotheses"] + report["recommended_actions"] + report["observed_evidence"]:
        assert set(item["evidence_ids"]) <= ids
    steps = (
        await client.get(path + "/steps?limit=100", headers=sandbox.headers[Role.VIEWER])
    ).json()
    assert [step["stage"] for step in steps["items"]] == list(STAGES)
    assert all(step["status"] == "completed" for step in steps["items"])
    async with sandbox.sessions() as session:
        incident = await session.get(Incident, UUID(detected["id"]))
        assert incident.version == 1 and incident.status == "triggered"
        stored = await session.get(Investigation, UUID(investigation["id"]))
        assert stored.llm_calls == 2
        assert stored.checkpoint["completed_stages"] == list(STAGES)
        assert await session.get(InvestigationReport, stored.id) is not None


async def test_missing_evidence_is_inconclusive_without_model_call(
    client: AsyncClient,
    sandbox: Sandbox,
    ai_settings,
    incident: Incident,
    ai_worker: InvestigationWorker,
) -> None:
    response = await client.post(
        f"/api/v1/incidents/{incident.id}/investigations",
        headers=sandbox.headers[Role.ENGINEER],
        json={},
    )
    assert response.status_code == 202
    assert await ai_worker.run_once() == 1
    report = (
        await client.get(
            f"/api/v1/investigations/{response.json()['id']}/report",
            headers=sandbox.headers[Role.VIEWER],
        )
    ).json()
    assert report["metadata"]["status"] == "inconclusive"
    assert report["metadata"]["calls_reserved"] == 0
    assert report["hypotheses"] == []
