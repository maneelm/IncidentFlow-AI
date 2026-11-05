import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from conftest import Sandbox
from httpx import AsyncClient
from sqlalchemy import func, select, text

from app.models import (
    DetectionEvidence,
    Incident,
    IncidentEvent,
    Role,
)
from app.workers.detection_worker import DetectionWorker

from .conftest import drain, ingest, make_log

pytestmark = pytest.mark.integration


async def count_incidents(sandbox: Sandbox) -> int:
    async with sandbox.sessions() as session:
        return await session.scalar(select(func.count()).select_from(Incident))


async def test_below_then_at_threshold(
    client: AsyncClient, sandbox: Sandbox, credential: dict, rule: dict, worker: DetectionWorker
) -> None:
    await ingest(client, sandbox, credential, [make_log("one"), make_log("two")])
    await drain(worker)
    assert await count_incidents(sandbox) == 0
    await ingest(client, sandbox, credential, [make_log("three")])
    await drain(worker)
    assert await count_incidents(sandbox) == 1


@pytest.mark.parametrize(
    "rule_change,log_change",
    [
        ({"enabled": False}, {}),
        ({}, {"level": "warning"}),
        ({}, {"error_code": "HTTP_500"}),
    ],
)
async def test_disabled_and_nonmatching_rules(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
    rule_change: dict,
    log_change: dict,
) -> None:
    if rule_change:
        response = await client.patch(
            f"/api/v1/detection-rules/{rule['id']}",
            headers=sandbox.headers[Role.ADMIN],
            json={"expected_version": rule["version"]} | rule_change,
        )
        assert response.status_code == 200
    await ingest(
        client, sandbox, credential, [make_log(f"event-{i}", **log_change) for i in range(5)]
    )
    await drain(worker)
    assert await count_incidents(sandbox) == 0


@pytest.mark.parametrize("oldest_offset,expected", [(-60, 0), (-59.999999, 1), (-61, 0)])
async def test_rolling_window_is_exclusive_lower_inclusive_upper(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
    oldest_offset: float,
    expected: int,
) -> None:
    result = await ingest(client, sandbox, credential, [make_log(f"event-{i}") for i in range(3)])
    async with sandbox.engine.begin() as connection:
        # Freeze stored receipt times after using the real ingestion pipeline.
        # No clock mocking or substitute database changes window semantics.
        base = await connection.scalar(text("SELECT clock_timestamp()"))
        await connection.execute(
            text("UPDATE detection_rules SET effective_from = :value"),
            {"value": base - timedelta(seconds=120)},
        )
        for item, offset in zip(result["items"], [oldest_offset, 0, 0], strict=True):
            await connection.execute(
                text("UPDATE log_entries SET ingested_at = :value WHERE id = :id"),
                {"value": base + timedelta(seconds=offset), "id": UUID(item["log_id"])},
            )
    await drain(worker)
    assert await count_incidents(sandbox) == expected


async def test_late_and_out_of_order_source_time_use_receipt_time(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    now = datetime.now(UTC)
    logs = [
        make_log("new", timestamp=now.isoformat()),
        make_log("late", timestamp=(now - timedelta(hours=23)).isoformat()),
        make_log("clock-skew", timestamp=(now + timedelta(seconds=90)).isoformat()),
    ]
    for log in logs:
        await ingest(client, sandbox, credential, [log])
    await drain(worker)
    assert await count_incidents(sandbox) == 1
    page = (await client.get("/api/v1/detections", headers=sandbox.headers[Role.VIEWER])).json()
    assert page["items"][0]["rule_snapshot"]["threshold"] == 3
    assert (
        await client.get(
            "/api/v1/logs",
            headers=sandbox.headers[Role.VIEWER],
            params={"timestamp_to": (now - timedelta(hours=1)).isoformat()},
        )
    ).json()["total"] == 1


async def test_fifty_errors_make_one_incident_and_evidence_is_idempotent(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    await ingest(client, sandbox, credential, [make_log(f"event-{i:03}") for i in range(50)])
    assert await drain(worker) == 50
    assert await count_incidents(sandbox) == 1
    page = (await client.get("/api/v1/detections", headers=sandbox.headers[Role.VIEWER])).json()
    detection = page["items"][0]
    response = await client.get(
        "/api/v1/logs",
        headers=sandbox.headers[Role.VIEWER],
        params={"incident_id": detection["incident_id"], "limit": 10, "offset": 10},
    )
    assert response.json()["total"] == 50 and len(response.json()["items"]) == 10
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 1
        assert await session.scalar(select(func.count()).select_from(DetectionEvidence)) == 50


async def test_independent_services_and_rules(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    headers = sandbox.headers[Role.ADMIN]
    other = (
        await client.post(
            "/api/v1/services", headers=headers, json={"name": "orders", "environment": "test"}
        )
    ).json()
    rule_data = {
        key: rule[key]
        for key in [
            "name",
            "level",
            "error_code",
            "threshold",
            "window_seconds",
            "incident_severity",
            "cooldown_seconds",
        ]
    }
    response = await client.post(
        "/api/v1/detection-rules", headers=headers, json=rule_data | {"service_id": other["id"]}
    )
    assert response.status_code == 201
    other_key = (
        await client.post(
            "/api/v1/ingestion-credentials",
            headers=headers,
            json={"service_id": other["id"], "name": "other producer"},
        )
    ).json()
    logs = [make_log(f"shared-id-{i}") for i in range(3)]
    await ingest(client, sandbox, credential, logs)
    response = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": other_key["token"]},
        json={"service_id": other["id"], "logs": logs},
    )
    assert response.status_code == 202 and response.json()["accepted"] == 3
    await asyncio.gather(drain(worker), drain(DetectionWorker(sandbox.sessions, sandbox.settings)))
    assert await count_incidents(sandbox) == 2
    occurrences = (await client.get("/api/v1/detections", headers=headers)).json()["items"]
    assert len({item["detection_key"] for item in occurrences}) == 2
    assert {item["service_id"] for item in occurrences} == {other["id"], str(sandbox.service.id)}


@pytest.mark.parametrize("terminal", ["resolved", "closed"])
async def test_new_detection_after_manual_terminal_state_never_reopens_old_incident(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
    terminal: str,
) -> None:
    await ingest(client, sandbox, credential, [make_log(f"first-{i}") for i in range(3)])
    await drain(worker)
    headers = sandbox.headers[Role.ENGINEER]
    first = (await client.get("/api/v1/incidents", headers=headers)).json()["items"][0]
    states = ["investigating", "resolved"] + (["closed"] if terminal == "closed" else [])
    version = first["version"]
    for state in states:
        response = await client.post(
            f"/api/v1/incidents/{first['id']}/transitions",
            headers=headers,
            json={"expected_version": version, "status": state, "reason": "Operator resolution"},
        )
        assert response.status_code == 200
        version = response.json()["version"]
    await ingest(client, sandbox, credential, [make_log("new-1"), make_log("new-2")])
    await drain(worker)
    assert await count_incidents(sandbox) == 1
    await ingest(client, sandbox, credential, [make_log("new-3")])
    await drain(worker)
    assert await count_incidents(sandbox) == 2
    old = (await client.get(f"/api/v1/incidents/{first['id']}", headers=headers)).json()
    assert old["status"] == terminal and old["version"] == version
    events = (await client.get(f"/api/v1/incidents/{first['id']}/events", headers=headers)).json()
    assert all(event["event_type"] != "reopened" for event in events["items"])


async def test_cooldown_excludes_logs_received_before_gate(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    headers = sandbox.headers[Role.ADMIN]
    response = await client.patch(
        f"/api/v1/detection-rules/{rule['id']}",
        headers=headers,
        json={"expected_version": 1, "cooldown_seconds": 60, "threshold": 1},
    )
    assert response.status_code == 200
    await ingest(client, sandbox, credential, [make_log("first")])
    await drain(worker)
    incident = (await client.get("/api/v1/incidents", headers=headers)).json()["items"][0]
    for version, state in [(1, "investigating"), (2, "resolved")]:
        assert (
            await client.post(
                f"/api/v1/incidents/{incident['id']}/transitions",
                headers=headers,
                json={"expected_version": version, "status": state, "reason": "Done"},
            )
        ).status_code == 200
    await ingest(client, sandbox, credential, [make_log("during-cooldown")])
    await drain(worker)
    assert await count_incidents(sandbox) == 1
    # Advance the stored cooldown boundary, without waiting a minute in the test.
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text("UPDATE detection_rules SET eligible_after = clock_timestamp()")
        )
    await ingest(client, sandbox, credential, [make_log("after-cooldown")])
    await drain(worker)
    assert await count_incidents(sandbox) == 2


async def test_pending_old_logs_cannot_trigger_again_after_resolution(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    await ingest(client, sandbox, credential, [make_log(f"event-{i}") for i in range(5)])
    first_claim = await worker.claim_one()
    assert await worker.process(first_claim) == "completed"
    headers = sandbox.headers[Role.ADMIN]
    incident = (await client.get("/api/v1/incidents", headers=headers)).json()["items"][0]
    for version, state in [(1, "investigating"), (2, "resolved")]:
        await client.post(
            f"/api/v1/incidents/{incident['id']}/transitions",
            headers=headers,
            json={
                "expected_version": version,
                "status": state,
                "reason": "Resolved before backlog",
            },
        )
    await drain(worker)
    assert await count_incidents(sandbox) == 1


async def test_admin_rule_control_and_viewer_permissions(
    client: AsyncClient,
    sandbox: Sandbox,
    rule: dict,
) -> None:
    path = f"/api/v1/detection-rules/{rule['id']}"
    for role in [Role.ENGINEER, Role.VIEWER]:
        headers = sandbox.headers[role]
        assert (await client.get(path, headers=headers)).status_code == 200
        assert (await client.get("/api/v1/detection-rules", headers=headers)).status_code == 200
        assert (
            await client.patch(
                path, headers=headers, json={"expected_version": 1, "enabled": False}
            )
        ).status_code == 403
        assert (
            await client.post("/api/v1/detection-rules", headers=headers, json={})
        ).status_code == 403
        assert (await client.get("/api/v1/detection-jobs", headers=headers)).status_code == 403
    headers = sandbox.headers[Role.ADMIN]
    disabled = await client.patch(
        path, headers=headers, json={"expected_version": 1, "enabled": False}
    )
    assert disabled.status_code == 200 and disabled.json()["version"] == 2
    assert (
        await client.patch(path, headers=headers, json={"expected_version": 1, "enabled": True})
    ).status_code == 409
    assert (await client.get("/api/v1/detection-rules?enabled=false", headers=headers)).json()[
        "total"
    ] == 1
    assert (await client.delete(path, headers=headers)).status_code == 405


@pytest.mark.parametrize(
    "change",
    [
        {"threshold": 0},
        {"threshold": 1001},
        {"window_seconds": 4},
        {"window_seconds": 3601},
        {"cooldown_seconds": -1},
        {"level": "fatal"},
        {"incident_severity": "urgent"},
        {"name": " "},
    ],
)
async def test_invalid_rules(client: AsyncClient, sandbox: Sandbox, change: dict) -> None:
    data = {
        "service_id": str(sandbox.service.id),
        "name": "test",
        "threshold": 3,
        "window_seconds": 60,
        "incident_severity": "high",
    } | change
    assert (
        await client.post("/api/v1/detection-rules", headers=sandbox.headers[Role.ADMIN], json=data)
    ).status_code == 422


async def test_rule_edits_do_not_backfill_and_active_criteria_are_protected(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    rule: dict,
    worker: DetectionWorker,
) -> None:
    headers = sandbox.headers[Role.ADMIN]
    await ingest(client, sandbox, credential, [make_log(f"old-{i}") for i in range(3)])
    changed = await client.patch(
        f"/api/v1/detection-rules/{rule['id']}",
        headers=headers,
        json={"expected_version": 1, "threshold": 1},
    )
    assert changed.status_code == 200
    await drain(worker)
    assert await count_incidents(sandbox) == 0
    await ingest(client, sandbox, credential, [make_log("new")])
    await drain(worker)
    assert await count_incidents(sandbox) == 1
    response = await client.patch(
        f"/api/v1/detection-rules/{rule['id']}",
        headers=headers,
        json={"expected_version": 2, "threshold": 2},
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "active_detection"


async def test_rule_limit_is_enforced(client: AsyncClient, sandbox: Sandbox) -> None:
    for index in range(26):
        response = await client.post(
            "/api/v1/detection-rules",
            headers=sandbox.headers[Role.ADMIN],
            json={
                "service_id": str(sandbox.service.id),
                "name": f"rule-{index}",
                "threshold": 1,
                "window_seconds": 5,
                "incident_severity": "low",
            },
        )
        assert response.status_code == (201 if index < 25 else 409)
