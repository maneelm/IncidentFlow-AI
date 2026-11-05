import pytest

from app.models import Role

pytestmark = pytest.mark.integration


async def test_overview_uses_database_counts_and_bounded_records(client, sandbox, incident):
    response = await client.get("/api/v1/dashboard/overview", headers=sandbox.headers[Role.VIEWER])
    assert response.status_code == 200, response.text
    body = response.json()
    assert (
        body["total_incidents"] == 1
        and body["active_incidents"] == 1
        and body["total_services"] == 1
    )
    assert body["statuses"] == [{"name": "triggered", "count": 1}]
    assert body["recent_activity"][0]["incident_id"] == str(incident.id)
    assert body["pending_approvals"] == 0 and body["approval_queue"] == []
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard/overview",
        "/dashboard/investigations",
        "/dashboard/resolution-proposals",
        "/directory/users",
    ],
)
async def test_dashboard_reads_require_auth(client, path):
    assert (await client.get("/api/v1" + path)).status_code == 401


async def test_directory_excludes_viewers_and_private_fields(client, sandbox):
    response = await client.get(
        "/api/v1/directory/users?limit=1", headers=sandbox.headers[Role.ENGINEER]
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2 and len(body["items"]) == 1
    assert set(body["items"][0]) == {"id", "display_name", "role"}


async def test_empty_overview_and_activity_pages(client, sandbox):
    headers = sandbox.headers[Role.VIEWER]
    response = await client.get("/api/v1/dashboard/overview", headers=headers)
    assert response.json()["total_incidents"] == 0
    for resource in ("investigations", "resolution-proposals"):
        body = (await client.get(f"/api/v1/dashboard/{resource}?limit=1", headers=headers)).json()
        assert body == {"items": [], "total": 0, "limit": 1, "offset": 0}
    assert (
        await client.get("/api/v1/dashboard/investigations?limit=101", headers=headers)
    ).status_code == 422


async def test_global_activity_filters_reflect_real_requests(client, sandbox, incident):
    sandbox.settings.investigation_llm_provider = "fake"
    sandbox.settings.investigation_llm_model = "fake-v1"
    writer = sandbox.headers[Role.ENGINEER]
    reader = sandbox.headers[Role.VIEWER]
    created = await client.post(
        f"/api/v1/incidents/{incident.id}/investigations", headers=writer, json={}
    )
    assert created.status_code == 202
    response = await client.get(
        "/api/v1/dashboard/investigations",
        headers=reader,
        params={"incident_id": str(incident.id), "status": "queued", "limit": 1},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == created.json()["id"]
    assert (
        await client.get("/api/v1/dashboard/investigations?status=completed", headers=reader)
    ).json()["total"] == 0
    overview = (await client.get("/api/v1/dashboard/overview", headers=reader)).json()
    assert overview["recent_investigations"][0]["id"] == created.json()["id"]


async def test_incident_detection_read_model_is_scoped(client, sandbox, incident):
    from uuid import uuid4

    reader = sandbox.headers[Role.VIEWER]
    response = await client.get(f"/api/v1/incidents/{incident.id}/detections", headers=reader)
    assert response.status_code == 200 and response.json()["total"] == 0
    assert (
        await client.get(f"/api/v1/incidents/{uuid4()}/detections", headers=reader)
    ).status_code == 404
    assert (await client.get(f"/api/v1/incidents/{incident.id}/detections")).status_code == 401
