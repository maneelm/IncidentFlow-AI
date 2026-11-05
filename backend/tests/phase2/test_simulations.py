from datetime import UTC, datetime

import pytest
from conftest import Sandbox
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models import Incident, LogEntry, Role
from app.simulations.scenarios import SCENARIOS, scenario_batch
from app.workers.detection_worker import DetectionWorker

from .conftest import drain

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("scenario", list(SCENARIOS))
async def test_fixture_scenarios_use_real_ingestion_and_detection(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    worker: DetectionWorker,
    scenario: str,
) -> None:
    definition = SCENARIOS[scenario]
    response = await client.post(
        "/api/v1/detection-rules",
        headers=sandbox.headers[Role.ADMIN],
        json={
            "service_id": str(sandbox.service.id),
            "name": definition["name"],
            "level": definition["level"],
            "error_code": definition["error_code"],
            "threshold": definition["threshold"],
            "window_seconds": 60,
            "incident_severity": definition["incident_severity"],
        },
    )
    assert response.status_code == 201
    anchor = datetime.now(UTC)
    batch = scenario_batch(scenario, sandbox.service.id, "fixture-run", 42, anchor)
    assert batch == scenario_batch(scenario, sandbox.service.id, "fixture-run", 42, anchor)
    assert all(log.metadata["synthetic"] for log in batch.logs)
    if scenario == "latency":
        assert all(log.metadata["latency_ms"] >= 1000 for log in batch.logs)
    for replay in range(2):
        result = await client.post(
            "/api/v1/logs/ingest",
            headers={"X-Ingestion-Key": credential["token"]},
            json=batch.model_dump(mode="json"),
        )
        assert result.status_code == 202
        assert result.json()["accepted"] == (len(batch.logs) if replay == 0 else 0)
        await drain(worker)
    async with sandbox.sessions() as session:
        assert (
            await session.scalar(select(func.count()).select_from(Incident))
            == definition["expected_incidents"]
        )
        assert await session.scalar(select(func.count()).select_from(LogEntry)) == len(batch.logs)
        if definition["expected_incidents"]:
            incident = await session.scalar(select(Incident))
            assert incident.severity == definition["incident_severity"]
