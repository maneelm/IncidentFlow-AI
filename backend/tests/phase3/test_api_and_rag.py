import asyncio
from uuid import UUID, uuid4

import pytest
from conftest import Sandbox
from httpx import AsyncClient
from sqlalchemy import func, select

from app.models import Incident, Role
from app.models.investigations import InvestigationJob, RunbookChunk

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "role,expected", [(Role.ADMIN, 202), (Role.ENGINEER, 202), (Role.VIEWER, 403), (None, 401)]
)
async def test_start_permissions(
    client: AsyncClient, sandbox: Sandbox, incident: Incident, ai_settings, role, expected: int
) -> None:
    response = await client.post(
        f"/api/v1/incidents/{incident.id}/investigations",
        headers=sandbox.headers[role] if role else {},
        json={},
    )
    assert response.status_code == expected


async def test_idempotency_active_limit_cancel_and_reads(
    client: AsyncClient, sandbox: Sandbox, investigation: dict
) -> None:
    headers = sandbox.headers[Role.ENGINEER]
    path = f"/api/v1/investigations/{investigation['id']}"
    same = await client.post(
        f"/api/v1/incidents/{investigation['incident_id']}/investigations",
        headers=headers,
        json={"client_request_id": investigation["client_request_id"]},
    )
    assert same.status_code == 202 and same.json()["id"] == investigation["id"]
    assert (
        await client.post(
            f"/api/v1/incidents/{investigation['incident_id']}/investigations",
            headers=headers,
            json={},
        )
    ).status_code == 409
    assert (await client.get(path + "/report", headers=headers)).status_code == 409
    assert (
        await client.get(
            f"/api/v1/incidents/{investigation['incident_id']}/investigations?limit=1",
            headers=headers,
        )
    ).json()["total"] == 1
    for suffix in ["", "/evidence", "/steps"]:
        assert (
            await client.get(path + suffix, headers=sandbox.headers[Role.VIEWER])
        ).status_code == 200
    assert (
        await client.post(path + "/cancel", headers=sandbox.headers[Role.VIEWER])
    ).status_code == 403
    cancelled = await client.post(path + "/cancel", headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert (await client.post(path + "/cancel", headers=headers)).json() == cancelled.json()
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(InvestigationJob)) == 1


async def test_concurrent_starts_and_missing_incidents(
    client: AsyncClient, sandbox: Sandbox, incident: Incident, ai_settings
) -> None:
    async def start():
        return await client.post(
            f"/api/v1/incidents/{incident.id}/investigations",
            headers=sandbox.headers[Role.ENGINEER],
            json={},
        )

    results = await asyncio.gather(start(), start())
    assert sorted(item.status_code for item in results) == [202, 409]
    assert (
        await client.post(
            f"/api/v1/incidents/{uuid4()}/investigations",
            headers=sandbox.headers[Role.ADMIN],
            json={},
        )
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/investigations/{uuid4()}", headers=sandbox.headers[Role.VIEWER])
    ).status_code == 404
    assert (
        await client.post(
            f"/api/v1/incidents/{incident.id}/investigations",
            headers=sandbox.headers[Role.ADMIN],
            json={"provider": "fake"},
        )
    ).status_code == 422


async def test_missing_groq_key_does_not_break_normal_api(
    client: AsyncClient, sandbox: Sandbox, incident: Incident
) -> None:
    sandbox.settings.groq_api_key = None
    sandbox.settings.investigation_llm_provider = "groq"
    response = await client.post(
        f"/api/v1/incidents/{incident.id}/investigations",
        headers=sandbox.headers[Role.ENGINEER],
        json={},
    )
    assert (
        response.status_code == 503
        and response.json()["error"]["code"] == "investigation_provider_unavailable"
    )
    for route in ["health", "ready", "incidents", "services", "logs"]:
        assert (
            await client.get(f"/api/v1/{route}", headers=sandbox.headers[Role.VIEWER])
        ).status_code == 200


async def test_admission_caps(client: AsyncClient, sandbox: Sandbox, ai_settings) -> None:
    for index in range(6):
        incident = (
            await client.post(
                "/api/v1/incidents",
                headers=sandbox.headers[Role.ENGINEER],
                json={
                    "service_id": str(sandbox.service.id),
                    "title": f"incident-{index}",
                    "description": "Test admission",
                    "severity": "low",
                },
            )
        ).json()
        response = await client.post(
            f"/api/v1/incidents/{incident['id']}/investigations",
            headers=sandbox.headers[Role.ENGINEER],
            json={},
        )
        assert response.status_code == (202 if index < 5 else 429)


async def test_runbook_versions_search_reindex_and_provenance(
    client: AsyncClient, sandbox: Sandbox, runbook: dict
) -> None:
    headers = sandbox.headers[Role.ADMIN]
    path = f"/api/v1/runbooks/{runbook['id']}"
    search = await client.post(
        "/api/v1/runbooks/search",
        headers=sandbox.headers[Role.VIEWER],
        json={"query": "DB_CONNECTION_TIMEOUT", "service_id": str(sandbox.service.id)},
    )
    assert search.status_code == 200 and search.json()[0]["document_version"] == 1
    assert search.json()[0]["source_id"] in runbook["chunk_ids"]
    changed = await client.patch(
        path,
        headers=headers,
        json={
            "expected_version": 1,
            "content": "Different current content without the former code.",
        },
    )
    assert changed.status_code == 200 and changed.json()["current_version"] == 2
    assert changed.json()["content_hash"] != runbook["content_hash"]
    assert (await client.get(path + "/versions/1", headers=headers)).json()["content"] == runbook[
        "content"
    ]
    assert (
        await client.get(f"/api/v1/runbook-chunks/{runbook['chunk_ids'][0]}", headers=headers)
    ).status_code == 200
    assert (
        await client.patch(path, headers=headers, json={"expected_version": 1, "title": "stale"})
    ).status_code == 409
    reindex = await client.post(path + "/reindex", headers=headers, json={"expected_version": 2})
    assert reindex.status_code == 200 and reindex.json()["current_version"] == 3
    assert reindex.json()["chunk_ids"] != changed.json()["chunk_ids"]
    async with sandbox.sessions() as session:
        assert await session.get(RunbookChunk, UUID(runbook["chunk_ids"][0])) is not None


async def test_runbook_upload_limits_encoding_and_permissions(
    client: AsyncClient, sandbox: Sandbox
) -> None:
    headers = sandbox.headers[Role.ADMIN]
    for filename, body, mime, expected in [
        ("pool.md", b"# DB_CONNECTION_TIMEOUT\nInspect the pool.", "text/markdown", 201),
        ("raw.txt", b"\xff\xfe", "text/plain", 422),
        ("bad.py", b"print('no')", "text/plain", 422),
        ("big.txt", b"x" * 131073, "text/plain", 413),
    ]:
        response = await client.post(
            "/api/v1/runbooks/upload",
            headers=headers,
            data={"title": "Uploaded runbook"},
            files={"file": (filename, body, mime)},
        )
        assert response.status_code == expected, response.text
    assert (
        await client.post("/api/v1/runbooks", headers=headers, content=b"x" * 262145)
    ).status_code == 413
    for role in [Role.ENGINEER, Role.VIEWER]:
        assert (
            await client.post(
                "/api/v1/runbooks",
                headers=sandbox.headers[role],
                json={"title": "Forbidden", "content": "Text"},
            )
        ).status_code == 403


async def test_similar_resolved_incidents_and_notes(
    client: AsyncClient, sandbox: Sandbox, incident: Incident
) -> None:
    headers = sandbox.headers[Role.ENGINEER]
    assert (
        await client.post(
            f"/api/v1/incidents/{incident.id}/resolution-notes",
            headers=headers,
            json={"content": "too early"},
        )
    ).status_code == 409
    for version, status in [(1, "investigating"), (2, "resolved")]:
        assert (
            await client.post(
                f"/api/v1/incidents/{incident.id}/transitions",
                headers=headers,
                json={
                    "expected_version": version,
                    "status": status,
                    "reason": "DB_CONNECTION_TIMEOUT mitigated by the operator",
                },
            )
        ).status_code == 200
    response = await client.post(
        f"/api/v1/incidents/{incident.id}/resolution-notes",
        headers=headers,
        json={
            "content": "Database pool saturation was investigated; a connection leak "
            "remains unverified."
        },
    )
    assert response.status_code == 201
    other = (
        await client.post(
            "/api/v1/incidents",
            headers=headers,
            json={
                "service_id": str(sandbox.service.id),
                "title": "Database pool saturation",
                "description": "Database connections timeout",
                "severity": "high",
            },
        )
    ).json()
    hits = await client.post(
        f"/api/v1/incidents/{other['id']}/similar",
        headers=sandbox.headers[Role.VIEWER],
        json={"query": "database pool"},
    )
    assert hits.status_code == 200 and hits.json()
    assert any(item["source_type"] == "resolution_note" for item in hits.json())
    self_hits = (
        await client.post(
            f"/api/v1/incidents/{incident.id}/similar", headers=headers, json={"query": "database"}
        )
    ).json()
    assert all(item["source_id"] != str(incident.id) for item in self_hits)
