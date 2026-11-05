from uuid import uuid4

import pytest

from app.models import Role
from phase4.conftest import create, payload, transition

pytestmark = pytest.mark.integration


async def test_report_provenance_and_explicit_readonly_mapping(
    client, sandbox, detected, state, investigation, ai_worker, worker
):
    assert await ai_worker.run_once() == 1
    report = (
        await client.get(
            f"/api/v1/investigations/{investigation['id']}/report",
            headers=sandbox.headers[Role.ENGINEER],
        )
    ).json()
    recommendation = report["recommended_actions"][0]
    body = payload(
        detected,
        state,
        action_id="inspect_database_connections",
        origin="investigation",
        investigation_id=investigation["id"],
        recommendation_index=0,
        evidence_refs=recommendation["evidence_ids"],
    )
    proposal = await create(client, sandbox, detected["id"], body)
    assert proposal["provenance"]["recommendation"] == recommendation
    assert len(proposal["provenance"]["report_hash"]) == 64
    pending = await transition(client, sandbox, proposal, "submit")
    await transition(client, sandbox, pending, "approve", Role.ADMIN)
    assert await worker.run_once() == 1
    assert (
        await client.get(
            f"/api/v1/investigations/{investigation['id']}/report",
            headers=sandbox.headers[Role.ENGINEER],
        )
    ).json() == report
    items = []
    cursor = None
    while True:
        params = {"limit": 3}
        if cursor:
            params["cursor"] = cursor
        response = await client.get(
            f"/api/v1/incidents/{detected['id']}/timeline",
            headers=sandbox.headers[Role.VIEWER],
            params=params,
        )
        assert response.status_code == 200, response.text
        page = response.json()
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert {item["source_type"] for item in items} == {
        "incident_event",
        "detection",
        "investigation",
        "resolution_event",
    }
    keys = [
        (item["occurred_at"], item["source_type"], item["source_id"], item["event_type"])
        for item in items
    ]
    assert keys == sorted(set(keys))


@pytest.mark.parametrize("fault", ["foreign_incident", "fabricated_ref", "out_of_range"])
async def test_reject_invalid_investigation_provenance(
    client, sandbox, incident, detected, state, investigation, ai_worker, fault
):
    await ai_worker.run_once()
    report = (
        await client.get(
            f"/api/v1/investigations/{investigation['id']}/report",
            headers=sandbox.headers[Role.ENGINEER],
        )
    ).json()
    body = payload(
        detected,
        state,
        action_id="inspect_database_connections",
        origin="investigation",
        investigation_id=investigation["id"],
        recommendation_index=0,
        evidence_refs=report["recommended_actions"][0]["evidence_ids"],
    )
    target = detected["id"]
    if fault == "foreign_incident":
        target = incident.id
    elif fault == "fabricated_ref":
        body["evidence_refs"] = [f"log:{uuid4()}"]
    else:
        body["recommendation_index"] = 4
    assert (
        await client.post(
            f"/api/v1/incidents/{target}/resolution-proposals",
            headers=sandbox.headers[Role.ENGINEER],
            json=body,
        )
    ).status_code == 422


async def test_invalid_timeline_cursor(client, sandbox, incident):
    assert (
        await client.get(
            f"/api/v1/incidents/{incident.id}/timeline",
            headers=sandbox.headers[Role.VIEWER],
            params={"cursor": "invalid!"},
        )
    ).status_code == 422
