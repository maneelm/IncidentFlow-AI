import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import Sandbox
from httpx import AsyncClient
from sqlalchemy import func, select, text

from app.models import DetectionJob, IngestionCredential, LogEntry, Role

from .conftest import ingest, make_log

pytestmark = pytest.mark.integration


async def test_single_ingest_normalizes_and_persists(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
) -> None:
    result = await ingest(
        client, sandbox, credential, [make_log(level=" ERROR ", error_code="db_connection_timeout")]
    )
    assert result["accepted"] == 1 and result["duplicates"] == 0
    response = await client.get(
        f"/api/v1/logs/{result['items'][0]['log_id']}", headers=sandbox.headers[Role.VIEWER]
    )
    assert response.status_code == 200
    log = response.json()
    assert log["level"] == "error" and log["error_code"] == "DB_CONNECTION_TIMEOUT"
    assert log["metadata"] == {"synthetic": True}
    assert "content_hash" not in log and "credential_id" not in log
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(DetectionJob)) == 1


async def test_credential_returned_once_hashed_and_revocable(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
) -> None:
    headers = sandbox.headers[Role.ADMIN]
    path = f"/api/v1/ingestion-credentials/{credential['id']}"
    async with sandbox.sessions() as session:
        stored = await session.get(IngestionCredential, UUID(credential["id"]))
        assert credential["token"] != stored.secret_hash and len(stored.secret_hash) == 64
        assert credential["token"] not in repr(stored.__dict__)
    for resource in (path, "/api/v1/ingestion-credentials?limit=1"):
        response = await client.get(resource, headers=headers)
        assert response.status_code == 200
        assert "token" not in response.text and "secret_hash" not in response.text
    response = await client.post(f"{path}/revoke", headers=headers)
    assert response.status_code == 200 and response.json()["revoked_at"]
    assert (await client.post(f"{path}/revoke", headers=headers)).json() == response.json()
    rejected = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": credential["token"]},
        json={"service_id": str(sandbox.service.id), "logs": [make_log()]},
    )
    assert rejected.status_code == 401 and rejected.headers["www-authenticate"] == "ApiKey"


async def test_rotation_keeps_new_key_usable(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
) -> None:
    headers = sandbox.headers[Role.ADMIN]
    new = (
        await client.post(
            "/api/v1/ingestion-credentials",
            headers=headers,
            json={"service_id": str(sandbox.service.id), "name": "rotated"},
        )
    ).json()
    assert new["token"] != credential["token"]
    await client.post(f"/api/v1/ingestion-credentials/{credential['id']}/revoke", headers=headers)
    assert (await ingest(client, sandbox, new, [make_log()]))["accepted"] == 1


async def test_expired_credential_is_rejected(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
) -> None:
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE ingestion_credentials SET created_at = now() - interval '2 days', "
                "expires_at = now() - interval '1 day'"
            )
        )
    response = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": credential["token"]},
        json={"service_id": str(sandbox.service.id), "logs": [make_log()]},
    )
    assert response.status_code == 401


@pytest.mark.parametrize("kind", ["missing", "malformed", "forged", "human_jwt"])
async def test_ingestion_requires_machine_key(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    kind: str,
) -> None:
    headers = {}
    if kind == "malformed":
        headers["X-Ingestion-Key"] = "bad"
    elif kind == "forged":
        original = credential["token"]
        headers["X-Ingestion-Key"] = original[:-1] + ("A" if original[-1] != "A" else "B")
    elif kind == "human_jwt":
        headers = sandbox.headers[Role.ADMIN]
    response = await client.post(
        "/api/v1/logs/ingest",
        headers=headers,
        json={"service_id": str(sandbox.service.id), "logs": [make_log()]},
    )
    assert response.status_code == 401


async def test_cross_service_and_human_api_isolation(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
) -> None:
    service = (
        await client.post(
            "/api/v1/services",
            headers=sandbox.headers[Role.ADMIN],
            json={"name": "other", "environment": "test"},
        )
    ).json()
    headers = {"X-Ingestion-Key": credential["token"]}
    response = await client.post(
        "/api/v1/logs/ingest",
        headers=headers,
        json={"service_id": service["id"], "logs": [make_log()]},
    )
    assert response.status_code == 403
    assert (await client.get("/api/v1/logs", headers=headers)).status_code == 401
    assert (await client.post("/api/v1/incidents", headers=headers, json={})).status_code == 401


@pytest.mark.parametrize("role", [Role.ENGINEER, Role.VIEWER])
async def test_only_admin_manages_keys(
    client: AsyncClient, sandbox: Sandbox, credential: dict, role: Role
) -> None:
    headers = sandbox.headers[role]
    assert (
        await client.post(
            "/api/v1/ingestion-credentials",
            headers=headers,
            json={"service_id": str(sandbox.service.id), "name": "forbidden"},
        )
    ).status_code == 403
    assert (await client.get("/api/v1/ingestion-credentials", headers=headers)).status_code == 403
    assert (
        await client.post(
            f"/api/v1/ingestion-credentials/{credential['id']}/revoke", headers=headers
        )
    ).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"level": "fatal"},
        {"timestamp": "2026-11-02T00:00:00"},
        {"timestamp": "invalid"},
        {"message": " "},
        {"message": "x" * 4001},
        {"source_event_id": ""},
        {"source_event_id": "x" * 129},
        {"trace_id": "x" * 129},
        {"metadata": {"accessToken": "sensitive"}},
        {"metadata": {"nested": [{"password": "sensitive"}]}},
        {"metadata": {"nested": "Bearer confidential"}},
        {"metadata": {"nested": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"}},
        {"metadata": {"large": "x" * 8192}},
        {"metadata": {"list": list(range(257))}},
        {"metadata": {"a": {"b": {"c": {"d": {"e": {"f": 1}}}}}}},
        {"metadata": [1, 2]},
        {"password": "forbidden"},
        {"message": "bad\x00value"},
        {"message": "Bearer confidential"},
        {"metadata": {"value": "bad\x00value"}},
        {"metadata": {"bad\x00key": "value"}},
    ],
)
async def test_invalid_log_rejects_entire_batch(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    change: dict,
) -> None:
    response = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": credential["token"]},
        json={
            "service_id": str(sandbox.service.id),
            "logs": [make_log("valid"), make_log("bad", **change)],
        },
    )
    assert response.status_code == 422
    assert "sensitive" not in response.text and credential["token"] not in response.text
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(LogEntry)) == 0
        assert await session.scalar(select(func.count()).select_from(DetectionJob)) == 0


@pytest.mark.parametrize("hours", [-25, 1])
async def test_source_timestamp_tolerance(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    hours: int,
) -> None:
    response = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": credential["token"]},
        json={
            "service_id": str(sandbox.service.id),
            "logs": [make_log(timestamp=(datetime.now(UTC) + timedelta(hours=hours)).isoformat())],
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "timestamp_out_of_range"


@pytest.mark.parametrize("count", [0, 101])
async def test_batch_bounds(
    client: AsyncClient, sandbox: Sandbox, credential: dict, count: int
) -> None:
    response = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": credential["token"]},
        json={
            "service_id": str(sandbox.service.id),
            "logs": [make_log(f"event-{i}") for i in range(count)],
        },
    )
    assert response.status_code == 422


async def test_payload_limit_and_malformed_json(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
    caplog: pytest.LogCaptureFixture,
) -> None:
    headers = {"X-Ingestion-Key": credential["token"], "Content-Type": "application/json"}
    for body, expected in [(b"x" * 524289, 413), (b'{"logs":', 422), (b"[" * 1000, 422)]:
        response = await client.post("/api/v1/logs/ingest", headers=headers, content=body)
        assert response.status_code == expected

    async def chunks():
        for _ in range(9):
            yield b"x" * 65536

    response = await client.post("/api/v1/logs/ingest", headers=headers, content=chunks())
    assert response.status_code == 413
    response = await client.post(
        "/api/v1/logs/ingest", headers=headers | {"Content-Encoding": "gzip"}, content=b"bad"
    )
    assert response.status_code == 415
    assert credential["token"] not in caplog.text


async def test_idempotency_conflicts_and_concurrent_receipts(
    client: AsyncClient,
    sandbox: Sandbox,
    credential: dict,
) -> None:
    log = make_log()
    first = await ingest(client, sandbox, credential, [log, log])
    assert first["accepted"] == 1 and first["duplicates"] == 1 and len(first["items"]) == 1
    concurrent = await asyncio.gather(
        *[ingest(client, sandbox, credential, [log]) for _ in range(3)]
    )
    assert all(result["accepted"] == 0 for result in concurrent)
    # A conflict must roll back a NEW record in the same submission too.
    response = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": credential["token"]},
        json={
            "service_id": str(sandbox.service.id),
            "logs": [make_log("new"), log | {"message": "changed"}],
        },
    )
    assert (
        response.status_code == 409 and response.json()["error"]["code"] == "source_event_conflict"
    )
    response = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": credential["token"]},
        json={"service_id": str(sandbox.service.id), "logs": [log, log | {"message": "changed"}]},
    )
    assert response.status_code == 409
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(LogEntry)) == 1
        assert await session.scalar(select(func.count()).select_from(DetectionJob)) == 1


async def test_log_and_job_insertion_atomicity(
    client: AsyncClient, sandbox: Sandbox, credential: dict
) -> None:
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE FUNCTION reject_job() RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'test rejection' USING ERRCODE='23514'; END $$"
            )
        )
        await connection.execute(
            text(
                "CREATE TRIGGER reject_job BEFORE INSERT ON detection_jobs "
                "FOR EACH ROW EXECUTE FUNCTION reject_job()"
            )
        )
    response = await client.post(
        "/api/v1/logs/ingest",
        headers={"X-Ingestion-Key": credential["token"]},
        json={"service_id": str(sandbox.service.id), "logs": [make_log()]},
    )
    assert response.status_code == 409
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(LogEntry)) == 0
        assert await session.scalar(select(func.count()).select_from(DetectionJob)) == 0


async def test_log_queries_and_stable_pagination(
    client: AsyncClient, sandbox: Sandbox, credential: dict
) -> None:
    now = datetime.now(UTC)
    logs = [
        make_log(
            f"event-{i}",
            level="info" if i == 0 else "error",
            trace_id=f"trace-{i}",
            request_id=f"request-{i}",
            timestamp=(now - timedelta(seconds=i)).isoformat(),
        )
        for i in range(4)
    ]
    await ingest(client, sandbox, credential, logs)
    headers = sandbox.headers[Role.VIEWER]
    cases = [
        ({"level": "error"}, 3),
        ({"service_id": str(sandbox.service.id)}, 4),
        ({"service_id": str(uuid4())}, 0),
        ({"trace_id": "trace-2"}, 1),
        ({"request_id": "request-1"}, 1),
        ({"search": "database timeout"}, 4),
        ({"search": "unmatched"}, 0),
        (
            {
                "timestamp_from": (now - timedelta(seconds=1)).isoformat(),
                "timestamp_to": now.isoformat(),
            },
            2,
        ),
    ]
    for filters, expected in cases:
        response = await client.get("/api/v1/logs", headers=headers, params=filters)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == expected
    page = (
        await client.get("/api/v1/logs?limit=1&offset=1&sort_order=asc", headers=headers)
    ).json()
    assert page["total"] == 4 and page["items"][0]["source_event_id"] == "event-2"
    assert (await client.get(f"/api/v1/logs/{uuid4()}", headers=headers)).status_code == 404
    for query in [
        "limit=101",
        "offset=-1",
        "search=x",
        "timestamp_from=2026-01-01T00:00:00",
        "timestamp_from=2026-02-01T00:00:00Z&timestamp_to=2026-01-01T00:00:00Z",
        "unknown=x",
    ]:
        assert (await client.get(f"/api/v1/logs?{query}", headers=headers)).status_code == 422
