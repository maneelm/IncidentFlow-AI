from uuid import UUID

import pytest
from conftest import Sandbox
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models import DetectionJob, Incident, IncidentEvent, LogEntry, Role
from app.workers.detection_worker import DetectionWorker

from .conftest import drain, ingest, make_log

pytestmark = pytest.mark.integration


async def test_threshold_pipeline(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    payload = [make_log(f"event-{i:04}") for i in range(5)]
    result = await ingest(client, sandbox, credential, payload)
    assert result["accepted"] == 5
    assert await drain(worker) == 5
    page = (await client.get("/api/v1/detections", headers=sandbox.headers[Role.VIEWER])).json()
    assert page["total"] == 1, page
    occurrence = page["items"][0]
    assert occurrence["matching_count"] == 3
    evidence = await client.get(
        f"/api/v1/detections/{occurrence['id']}/logs", headers=sandbox.headers[Role.VIEWER]
    )
    assert evidence.status_code == 200 and evidence.json()["total"] == 5
    async with sandbox.sessions() as session:
        incident = await session.get(Incident, UUID(occurrence["incident_id"]))
        assert incident.status == "triggered" and incident.version == 1
        event = await session.scalar(select(IncidentEvent))
        assert event.actor_id is None and event.system_actor == "detection_worker"
        assert event.details["automation"]["rule_id"] == rule["id"]
        assert (
            await session.scalar(
                select(func.count())
                .select_from(DetectionJob)
                .where(DetectionJob.status == "completed")
            )
            == 5
        )
    repeated = await ingest(client, sandbox, credential, payload)
    assert repeated["accepted"] == 0 and repeated["duplicates"] == 5
    assert await drain(worker) == 0
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Incident)) == 1
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(LogEntry)) == 5
