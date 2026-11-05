import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from conftest import Sandbox
from httpx import AsyncClient
from sqlalchemy import func, select, text

from app.core.security import create_token, verify_password
from app.models import Incident, IncidentEvent, Role, User
from app.schemas.auth import BootstrapInput
from app.services.auth import AuthService

pytestmark = pytest.mark.integration
BASE = "/api/v1"


async def test_health_readiness_and_swagger(client: AsyncClient) -> None:
    assert (await client.get(f"{BASE}/health")).json() == {"status": "ok"}
    assert (await client.get(f"{BASE}/ready")).json() == {"status": "ready"}
    assert (await client.get("/docs")).status_code == 200
    spec = (await client.get("/openapi.json")).json()
    assert spec["components"]["securitySchemes"]["OAuth2PasswordBearer"]
    assert spec["paths"][f"{BASE}/incidents"]["post"]["responses"]["201"]


async def test_readiness_rejects_unmigrated_database(
    client: AsyncClient,
    sandbox: Sandbox,
) -> None:
    async with sandbox.engine.begin() as connection:
        await connection.execute(text("DROP TABLE alembic_version"))
    response = await client.get(f"{BASE}/ready")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "not_ready"
    assert (await client.get(f"{BASE}/health")).status_code == 200


async def test_readiness_rejects_unavailable_database(
    client: AsyncClient,
    sandbox: Sandbox,
) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    unavailable = create_async_engine("postgresql+asyncpg://unused:unused@127.0.0.1:1/unused")
    sandbox.app.state.engine = unavailable
    try:
        assert (await client.get(f"{BASE}/ready")).status_code == 503
    finally:
        sandbox.app.state.engine = sandbox.engine
        await unavailable.dispose()


async def test_login_and_me(client: AsyncClient, sandbox: Sandbox, password: str) -> None:
    response = await client.post(
        f"{BASE}/auth/token", data={"username": "ENGINEER@example.com", "password": password}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["expires_in"] == 900
    me = await client.get(
        f"{BASE}/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"}
    )
    assert me.status_code == 200
    assert me.json()["id"] == str(sandbox.users[Role.ENGINEER].id)
    assert "password" not in me.text
    assert verify_password(password, sandbox.users[Role.ENGINEER].password_hash)
    assert sandbox.users[Role.ENGINEER].password_hash.startswith("$argon2id$")


@pytest.mark.parametrize("email", ["engineer@example.com", "unknown@example.com"])
async def test_bad_login_is_generic(client: AsyncClient, email: str) -> None:
    response = await client.post(
        f"{BASE}/auth/token", data={"username": email, "password": "wrong-password"}
    )
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["message"] == "Incorrect email or password"


@pytest.mark.parametrize(
    "kind",
    [
        "missing",
        "malformed",
        "expired",
        "wrong_key",
        "deleted_user",
        "wrong_audience",
        "wrong_issuer",
        "wrong_type",
        "missing_exp",
        "wrong_algorithm",
    ],
)
async def test_reject_invalid_tokens(
    client: AsyncClient,
    sandbox: Sandbox,
    kind: str,
) -> None:
    token = create_token(sandbox.users[Role.ADMIN].id, sandbox.settings)
    if kind == "malformed":
        token = "broken"
    elif kind == "deleted_user":
        token = create_token(uuid4(), sandbox.settings)
    elif kind != "missing":
        claims = jwt.decode(token, options={"verify_signature": False})
        key = sandbox.settings.jwt_secret.get_secret_value()
        if kind == "expired":
            claims["exp"] = datetime.now(UTC) - timedelta(minutes=1)
        elif kind == "wrong_key":
            key = "a-different-signing-key-which-is-long-enough-for-hs256"
        elif kind == "wrong_audience":
            claims["aud"] = "another-api"
        elif kind == "wrong_issuer":
            claims["iss"] = "another-issuer"
        elif kind == "wrong_type":
            claims["type"] = "refresh"
        elif kind == "missing_exp":
            del claims["exp"]
        token = jwt.encode(claims, key, algorithm="HS384" if kind == "wrong_algorithm" else "HS256")
    headers = {} if kind == "missing" else {"Authorization": f"Bearer {token}"}
    response = await client.get(f"{BASE}/auth/me", headers=headers)
    assert response.status_code == 401


async def test_roles_are_read_from_database(client: AsyncClient, sandbox: Sandbox) -> None:
    async with sandbox.sessions() as session, session.begin():
        user = await session.get(User, sandbox.users[Role.ADMIN].id)
        user.role = Role.VIEWER
    response = await client.post(
        f"{BASE}/services",
        headers=sandbox.headers[Role.ADMIN],
        json={"name": "new", "environment": "staging"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(
    "operation",
    [
        "service_create",
        "service_update",
        "incident_create",
        "incident_update",
        "assign",
        "transition",
    ],
)
async def test_authorization_matrix(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
    incident_payload: dict[str, str],
    role: Role,
    operation: str,
) -> None:
    operations = {
        "service_create": ("POST", f"{BASE}/services", {"name": "worker", "environment": "dev"}),
        "service_update": (
            "PATCH",
            f"{BASE}/services/{sandbox.service.id}",
            {"description": "new"},
        ),
        "incident_create": ("POST", f"{BASE}/incidents", incident_payload),
        "incident_update": (
            "PATCH",
            f"{BASE}/incidents/{incident.id}",
            {"expected_version": 1, "title": "Changed"},
        ),
        "assign": (
            "PUT",
            f"{BASE}/incidents/{incident.id}/assignee",
            {"expected_version": 1, "assignee_id": str(sandbox.users[Role.ADMIN].id)},
        ),
        "transition": (
            "POST",
            f"{BASE}/incidents/{incident.id}/transitions",
            {"expected_version": 1, "status": "investigating", "reason": "On call"},
        ),
    }
    method, url, payload = operations[operation]
    response = await client.request(method, url, headers=sandbox.headers[role], json=payload)
    allowed = role == Role.ADMIN if operation.startswith("service_") else role != Role.VIEWER
    expected = (201 if operation.endswith("create") else 200) if allowed else 403
    assert response.status_code == expected
    if not allowed:
        current = (
            await client.get(f"{BASE}/incidents/{incident.id}", headers=sandbox.headers[Role.ADMIN])
        ).json()
        assert current["version"] == 1
        async with sandbox.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 1


@pytest.mark.parametrize("role", list(Role))
async def test_all_roles_can_read(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
    role: Role,
) -> None:
    for path in (
        "auth/me",
        "services",
        f"services/{sandbox.service.id}",
        "incidents",
        f"incidents/{incident.id}",
        f"incidents/{incident.id}/events",
    ):
        assert (
            await client.get(f"{BASE}/{path}", headers=sandbox.headers[role])
        ).status_code == 200


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "services"),
        ("GET", "incidents"),
        ("POST", "services"),
        ("POST", "incidents"),
        ("PATCH", "incidents/{id}"),
        ("PUT", "incidents/{id}/assignee"),
        ("POST", "incidents/{id}/transitions"),
        ("GET", "incidents/{id}/events"),
    ],
)
async def test_unauthenticated_access(
    client: AsyncClient,
    incident: Incident,
    method: str,
    path: str,
) -> None:
    response = await client.request(method, f"{BASE}/{path.format(id=incident.id)}", json={})
    assert response.status_code == 401


async def test_services_create_list_retrieve_update(client: AsyncClient, sandbox: Sandbox) -> None:
    headers = sandbox.headers[Role.ADMIN]
    response = await client.post(
        f"{BASE}/services",
        headers=headers,
        json={"name": "payments", "environment": "staging", "description": "Payments service"},
    )
    assert response.status_code == 201
    service_id = response.json()["id"]
    assert response.headers["location"].endswith(service_id)
    assert (await client.get(f"{BASE}/services/{service_id}", headers=headers)).json()[
        "name"
    ] == "payments"
    updated = await client.patch(
        f"{BASE}/services/{service_id}", headers=headers, json={"description": "Updated"}
    )
    assert updated.status_code == 200
    assert updated.json()["description"] == "Updated"
    page = (await client.get(f"{BASE}/services?limit=1&offset=1", headers=headers)).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert (
        await client.post(
            f"{BASE}/services", headers=headers, json={"name": "payments", "environment": "prod"}
        )
    ).status_code == 409
    assert (await client.get(f"{BASE}/services/{uuid4()}", headers=headers)).status_code == 404
    assert (
        await client.delete(f"{BASE}/services/{service_id}", headers=headers)
    ).status_code == 405


async def test_incident_creation_retrieval_and_audit(
    client: AsyncClient,
    sandbox: Sandbox,
    incident_payload: dict[str, str],
) -> None:
    headers = sandbox.headers[Role.ENGINEER]
    response = await client.post(f"{BASE}/incidents", headers=headers, json=incident_payload)
    assert response.status_code == 201
    body = response.json()
    assert body["incident_number"] == "INC-000001"
    assert body["version"] == 1 and body["status"] == "triggered"
    assert body["resolved_at"] is None and body["closed_at"] is None
    assert (await client.get(response.headers["location"], headers=headers)).json() == body
    events = (await client.get(f"{BASE}/incidents/{body['id']}/events", headers=headers)).json()
    assert events["total"] == 1
    event = events["items"][0]
    assert event["event_type"] == "created" and event["incident_version"] == 1
    assert event["actor_id"] == str(sandbox.users[Role.ENGINEER].id)
    assert event["details"]["title"] == incident_payload["title"]


async def test_incident_update_assignment_and_audit(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
) -> None:
    headers = sandbox.headers[Role.ENGINEER]
    path = f"{BASE}/incidents/{incident.id}"
    response = await client.patch(
        path, headers=headers, json={"expected_version": 1, "severity": "critical"}
    )
    assert response.status_code == 200 and response.json()["version"] == 2
    assigned = await client.put(
        f"{path}/assignee",
        headers=headers,
        json={"expected_version": 2, "assignee_id": str(sandbox.users[Role.ENGINEER].id)},
    )
    assert assigned.status_code == 200 and assigned.json()["version"] == 3
    unassigned = await client.put(
        f"{path}/assignee", headers=headers, json={"expected_version": 3, "assignee_id": None}
    )
    assert unassigned.json()["version"] == 4 and unassigned.json()["assignee_id"] is None
    events = (await client.get(f"{path}/events", headers=headers)).json()["items"]
    assert [event["event_type"] for event in events] == [
        "created",
        "updated",
        "assigned",
        "assigned",
    ]
    assert events[1]["details"]["changes"]["severity"] == {"from": "high", "to": "critical"}
    assert [event["incident_version"] for event in events] == [1, 2, 3, 4]
    page = (await client.get(f"{path}/events?limit=1&offset=2", headers=headers)).json()
    assert page["total"] == 4 and page["items"][0]["incident_version"] == 3


async def test_assignment_rejects_viewer_and_missing_user(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
) -> None:
    path = f"{BASE}/incidents/{incident.id}/assignee"
    for assignee, expected in [(sandbox.users[Role.VIEWER].id, 422), (uuid4(), 404)]:
        response = await client.put(
            path,
            headers=sandbox.headers[Role.ADMIN],
            json={"expected_version": 1, "assignee_id": str(assignee)},
        )
        assert response.status_code == expected


async def test_missing_incident_and_service(
    client: AsyncClient,
    sandbox: Sandbox,
    incident_payload: dict[str, str],
) -> None:
    headers = sandbox.headers[Role.ADMIN]
    assert (await client.get(f"{BASE}/incidents/{uuid4()}", headers=headers)).status_code == 404
    assert (
        await client.get(f"{BASE}/incidents/{uuid4()}/events", headers=headers)
    ).status_code == 404
    response = await client.post(
        f"{BASE}/incidents", headers=headers, json={**incident_payload, "service_id": str(uuid4())}
    )
    assert response.status_code == 404
    async with sandbox.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Incident)) == 0


async def test_pagination_filters_and_sorting(
    client: AsyncClient,
    sandbox: Sandbox,
    incident_payload: dict[str, str],
) -> None:
    headers = sandbox.headers[Role.ADMIN]
    start = datetime.now(UTC) - timedelta(seconds=1)
    other = await client.post(
        f"{BASE}/services", headers=headers, json={"name": "worker", "environment": "prod"}
    )
    created = []
    for index, severity in enumerate(["low", "critical", "medium", "high"]):
        body = {**incident_payload, "title": f"Incident {index}", "severity": severity}
        if index == 1:
            body["service_id"] = other.json()["id"]
            body["assignee_id"] = str(sandbox.users[Role.ADMIN].id)
        response = await client.post(f"{BASE}/incidents", headers=headers, json=body)
        assert response.status_code == 201
        created.append(response.json())
    response = await client.post(
        f"{BASE}/incidents/{created[1]['id']}/transitions",
        headers=headers,
        json={"expected_version": 1, "status": "investigating", "reason": "triage"},
    )
    assert response.status_code == 200
    cases = [
        ({"severity": "critical"}, 1),
        ({"status": "triggered"}, 3),
        ({"service_id": other.json()["id"]}, 1),
        ({"assignee_id": str(sandbox.users[Role.ADMIN].id)}, 1),
        ({"unassigned": True}, 3),
        ({"created_from": start.isoformat(), "created_to": datetime.now(UTC).isoformat()}, 4),
        ({"created_to": start.isoformat()}, 0),
        ({"severity": "low", "service_id": other.json()["id"]}, 0),
    ]
    for params, total in cases:
        response = await client.get(f"{BASE}/incidents", headers=headers, params=params)
        assert response.status_code == 200, response.text
        assert response.json()["total"] == total
    response = await client.get(
        f"{BASE}/incidents",
        headers=headers,
        params={"sort_by": "severity", "sort_order": "desc", "limit": 2, "offset": 1},
    )
    page = response.json()
    assert page["total"] == 4 and [item["severity"] for item in page["items"]] == ["high", "medium"]
    response = await client.get(
        f"{BASE}/incidents?sort_by=incident_number&sort_order=asc", headers=headers
    )
    assert [item["id"] for item in response.json()["items"]] == [item["id"] for item in created]


@pytest.mark.parametrize("status", ["mitigated", "resolved", "closed", "triggered"])
async def test_invalid_transition_leaves_no_changes(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
    status: str,
) -> None:
    path = f"{BASE}/incidents/{incident.id}"
    headers = sandbox.headers[Role.ADMIN]
    response = await client.post(
        f"{path}/transitions",
        headers=headers,
        json={"expected_version": 1, "status": status, "reason": "Invalid jump"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_transition"
    assert (await client.get(path, headers=headers)).json()["version"] == 1
    assert (await client.get(f"{path}/events", headers=headers)).json()["total"] == 1


@pytest.mark.parametrize("with_mitigation", [True, False])
async def test_lifecycle_and_reopen(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
    with_mitigation: bool,
) -> None:
    path = f"{BASE}/incidents/{incident.id}"
    headers = sandbox.headers[Role.ENGINEER]
    states = (
        ["investigating"]
        + (["mitigated"] if with_mitigation else [])
        + [
            "resolved",
            "closed",
            "investigating",
            "resolved",
            "investigating",
        ]
    )
    version = 1
    for state in states:
        response = await client.post(
            f"{path}/transitions",
            headers=headers,
            json={"expected_version": version, "status": state, "reason": "Operator evidence"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        version += 1
        assert body["version"] == version and body["status"] == state
        assert (body["resolved_at"] is not None) == (state in {"resolved", "closed"})
        assert (body["closed_at"] is not None) == (state == "closed")
    events = (await client.get(f"{path}/events", headers=headers)).json()
    assert events["total"] == version
    assert sum(event["event_type"] == "reopened" for event in events["items"]) == 2
    assert events["items"][-1]["details"]["reason"] == "Operator evidence"


async def test_noop_and_stale_update(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
) -> None:
    path = f"{BASE}/incidents/{incident.id}"
    headers = sandbox.headers[Role.ADMIN]
    noop = await client.patch(
        path, headers=headers, json={"expected_version": 1, "title": incident.title}
    )
    assert noop.status_code == 409 and noop.json()["error"]["code"] == "no_change"
    assert (
        await client.put(
            f"{path}/assignee", headers=headers, json={"expected_version": 1, "assignee_id": None}
        )
    ).status_code == 409
    assert (
        await client.patch(path, headers=headers, json={"expected_version": 1, "title": "New"})
    ).status_code == 200
    stale = await client.patch(path, headers=headers, json={"expected_version": 1, "title": "Lost"})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "version_conflict"


async def test_parallel_http_updates_have_one_winner(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
) -> None:
    async def update(title: str):
        return await client.patch(
            f"{BASE}/incidents/{incident.id}",
            headers=sandbox.headers[Role.ENGINEER],
            json={"expected_version": 1, "title": title},
        )

    results = await asyncio.gather(update("Winner A"), update("Winner B"))
    assert sorted(response.status_code for response in results) == [200, 409]
    async with sandbox.sessions() as session:
        current = await session.get(Incident, incident.id)
        assert current.version == 2
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 2


@pytest.mark.parametrize("operation", ["create", "update", "assign", "transition"])
async def test_audit_insert_failure_rolls_back_mutation(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
    incident_payload: dict[str, str],
    operation: str,
) -> None:
    # Force a REAL PostgreSQL audit INSERT failure, after the incident INSERT/UPDATE flushed.
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text("""
            CREATE FUNCTION reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'test audit rejection' USING ERRCODE = '23514'; END $$
        """)
        )
        await connection.execute(
            text("""
            CREATE TRIGGER reject_audit BEFORE INSERT ON incident_events
            FOR EACH ROW EXECUTE FUNCTION reject_audit()
        """)
        )
    path = f"{BASE}/incidents/{incident.id}"
    requests = {
        "create": ("POST", f"{BASE}/incidents", incident_payload),
        "update": ("PATCH", path, {"expected_version": 1, "title": "Must rollback"}),
        "assign": (
            "PUT",
            f"{path}/assignee",
            {"expected_version": 1, "assignee_id": str(sandbox.users[Role.ADMIN].id)},
        ),
        "transition": (
            "POST",
            f"{path}/transitions",
            {"expected_version": 1, "status": "investigating", "reason": "Must rollback"},
        ),
    }
    method, url, payload = requests[operation]
    response = await client.request(method, url, headers=sandbox.headers[Role.ADMIN], json=payload)
    assert response.status_code == 409, response.text
    async with sandbox.sessions() as session:
        current = await session.get(Incident, incident.id)
        assert current.title == incident.title and current.status == "triggered"
        assert current.version == 1 and current.assignee_id is None
        assert await session.scalar(select(func.count()).select_from(Incident)) == 1
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 1


async def test_public_history_has_no_mutation_endpoints(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
) -> None:
    path = f"{BASE}/incidents/{incident.id}/events"
    for method in ("POST", "PATCH", "PUT", "DELETE"):
        response = await client.request(method, path, headers=sandbox.headers[Role.ADMIN], json={})
        assert response.status_code == 405


async def test_bootstrap_creates_secure_user_and_rejects_duplicates(
    sandbox: Sandbox,
    password: str,
) -> None:
    data = BootstrapInput(
        email="NEW@example.com", password=password, display_name="New user", role="engineer"
    )
    async with sandbox.sessions() as session, session.begin():
        created = await AuthService(session, sandbox.settings).bootstrap(data)
        assert created.email == "new@example.com"
        assert verify_password(password, created.password_hash)
    from app.core.errors import DomainError

    with pytest.raises(DomainError, match="already exists"):
        async with sandbox.sessions() as session, session.begin():
            await AuthService(session, sandbox.settings).bootstrap(data)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"title": " "},
        {"severity": "urgent"},
        {"service_id": "invalid"},
        {"status": "resolved"},
        {"title": "x" * 201},
        {"assignee_id": "invalid"},
    ],
)
async def test_incident_input_validation(
    client: AsyncClient,
    sandbox: Sandbox,
    incident_payload: dict[str, str],
    payload: dict,
) -> None:
    body = {} if payload == {} else {**incident_payload, **payload}
    response = await client.post(
        f"{BASE}/incidents", headers=sandbox.headers[Role.ADMIN], json=body
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "query",
    [
        "limit=0",
        "limit=101",
        "offset=-1",
        "status=unknown",
        "sort_by=title;drop",
        "sort_order=invalid",
        "created_from=2026-01-01T00:00:00",
        "created_from=2026-02-01T00:00:00Z&created_to=2026-01-01T00:00:00Z",
        "unassigned=true&assignee_id=00000000-0000-0000-0000-000000000001",
        "unknown_filter=true",
    ],
)
async def test_filter_validation(client: AsyncClient, sandbox: Sandbox, query: str) -> None:
    response = await client.get(f"{BASE}/incidents?{query}", headers=sandbox.headers[Role.ADMIN])
    assert response.status_code == 422


@pytest.mark.parametrize(
    "path,payload",
    [
        ("", {"expected_version": 1}),
        ("", {"expected_version": 0, "title": "new"}),
        ("", {"expected_version": 1, "title": None}),
        ("", {"title": "new"}),
        ("/assignee", {"expected_version": 1}),
        ("/transitions", {"expected_version": 1, "status": "investigating", "reason": " "}),
    ],
)
async def test_mutation_validation(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
    path: str,
    payload: dict,
) -> None:
    method = "PUT" if path == "/assignee" else "POST" if path else "PATCH"
    response = await client.request(
        method,
        f"{BASE}/incidents/{incident.id}{path}",
        headers=sandbox.headers[Role.ADMIN],
        json=payload,
    )
    assert response.status_code == 422


async def test_validation_does_not_echo_password(
    client: AsyncClient, password: str, caplog
) -> None:
    response = await client.post(
        f"{BASE}/auth/token", data={"username": "not-an-email", "password": password}
    )
    assert response.status_code == 422
    assert password not in response.text and password not in caplog.text
    assert "not-an-email" not in response.text


async def test_cors_allows_only_explicit_origin(client: AsyncClient) -> None:
    for origin, allowed in [("http://localhost:5173", True), ("https://untrusted.example", False)]:
        response = await client.options(
            f"{BASE}/incidents",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert (response.headers.get("access-control-allow-origin") == origin) == allowed


async def test_unexpected_exception_never_logs_secret_text(
    client: AsyncClient,
    sandbox: Sandbox,
    password: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = sandbox.settings.jwt_secret.get_secret_value()

    @sandbox.app.get("/test-only-error")
    async def fail() -> None:
        raise RuntimeError(f"sensitive driver text {password} {secret}")

    response = await client.get("/test-only-error")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "RuntimeError" in caplog.text
    assert password not in response.text + caplog.text
    assert secret not in response.text + caplog.text


async def test_commit_failure_is_rolled_back_before_response(
    client: AsyncClient,
    sandbox: Sandbox,
    incident: Incident,
) -> None:
    async with sandbox.engine.begin() as connection:
        await connection.execute(
            text("""
            CREATE FUNCTION reject_at_commit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN RAISE EXCEPTION 'deferred test rejection' USING ERRCODE = '23514'; END $$
        """)
        )
        await connection.execute(
            text("""
            CREATE CONSTRAINT TRIGGER deferred_audit_failure
            AFTER INSERT ON incident_events DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION reject_at_commit()
        """)
        )
    response = await client.patch(
        f"{BASE}/incidents/{incident.id}",
        headers=sandbox.headers[Role.ADMIN],
        json={"expected_version": 1, "title": "Must not commit"},
    )
    assert response.status_code == 409, response.text
    async with sandbox.sessions() as session:
        current = await session.get(Incident, incident.id)
        assert current.title == incident.title and current.version == 1
        assert await session.scalar(select(func.count()).select_from(IncidentEvent)) == 1
