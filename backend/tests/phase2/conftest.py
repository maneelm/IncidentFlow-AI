from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import Sandbox
from httpx import AsyncClient

from app.models import Role
from app.workers.detection_worker import DetectionWorker


def make_log(source_id: str = "event-0001", **changes: Any) -> dict[str, Any]:
    return {
        "source_event_id": source_id,
        "level": "error",
        "message": "SYNTHETIC database connection timeout",
        "timestamp": datetime.now(UTC).isoformat(),
        "error_code": "DB_CONNECTION_TIMEOUT",
        "trace_id": "trace-1",
        "request_id": "request-1",
        "metadata": {"synthetic": True},
    } | changes


@pytest.fixture
async def credential(client: AsyncClient, sandbox: Sandbox) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/ingestion-credentials",
        headers=sandbox.headers[Role.ADMIN],
        json={"service_id": str(sandbox.service.id), "name": "test producer"},
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
async def rule(client: AsyncClient, sandbox: Sandbox) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/detection-rules",
        headers=sandbox.headers[Role.ADMIN],
        json={
            "service_id": str(sandbox.service.id),
            "name": "Database failures",
            "level": "error",
            "error_code": "DB_CONNECTION_TIMEOUT",
            "threshold": 3,
            "window_seconds": 60,
            "incident_severity": "high",
            "cooldown_seconds": 0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def worker(sandbox: Sandbox) -> DetectionWorker:
    return DetectionWorker(sandbox.sessions, sandbox.settings)


async def ingest(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict[str, Any],
    logs: list[dict[str, Any]],
) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": credential["token"]},
        json={"service_id": str(sandbox.service.id), "logs": logs},
    )
    assert response.status_code == 202, response.text
    return response.json()


async def drain(worker: DetectionWorker, max_cycles: int = 100) -> int:
    total = 0
    for _ in range(max_cycles):
        count = await worker.run_once()
        total += count
        if count == 0:
            return total
    raise AssertionError("Worker did not drain bounded ready work")
