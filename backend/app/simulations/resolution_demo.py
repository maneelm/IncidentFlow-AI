"""HTTP-only Phase 2 → fake investigation → second-person approval → synthetic recovery."""

import argparse
import json
import os
import time
from contextlib import ExitStack
from uuid import uuid4

import httpx

from app.simulations.demo import checked, demonstrate
from app.simulations.investigation_demo import wait_for_report
from app.simulations.scenarios import api_url


def login(client: httpx.Client, role: str) -> dict:
    email = os.environ.get(f"DEMO_{role.upper()}_EMAIL")
    password = os.environ.get(f"DEMO_{role.upper()}_PASSWORD")
    if not email or not password:
        raise RuntimeError(f"Set DEMO_{role.upper()}_EMAIL and DEMO_{role.upper()}_PASSWORD")
    token = checked(
        client.post("/api/v1/auth/token", data={"username": email, "password": password}), 200
    )
    client.headers["Authorization"] = f"Bearer {token['access_token']}"
    actor = checked(client.get("/api/v1/auth/me"), 200)
    if actor["role"] != role:
        raise RuntimeError("Demo credentials must have their documented roles")
    return actor


def run_demo(
    admin: httpx.Client, engineer: httpx.Client, viewer: httpx.Client, timeout: int
) -> dict:
    capability = checked(admin.get("/api/v1/investigation-capabilities"), 200)
    if capability["provider"] != "fake" or not capability["configured"]:
        raise RuntimeError(
            "Resolution demo requires the explicitly configured fake investigation provider"
        )
    context = demonstrate(admin, "database", 42, timeout)
    incident_id, service_id = context["incident_id"], context["service_id"]
    state = checked(
        admin.post(
            f"/api/v1/services/{service_id}/synthetic-state",
            json={"scenario": "database_exhaustion", "expected_version": 0},
        ),
        200,
    )
    checked(
        admin.post(
            "/api/v1/runbooks",
            json={
                "title": "Synthetic pool recovery review",
                "service_id": service_id,
                "content": (
                    "DB_CONNECTION_TIMEOUT: inspect synthetic active connections, wait"
                    "ing requests and pool capacity before requesting a human-approved"
                    " simulated recovery."
                ),
            },
        ),
        201,
    )
    investigation = checked(
        engineer.post(f"/api/v1/incidents/{incident_id}/investigations", json={}), 202
    )
    report = wait_for_report(engineer, investigation["id"], timeout)
    if not report["metadata"]["synthetic_provider"]:
        raise RuntimeError("Demo must not call a hosted provider")
    before = checked(engineer.get(f"/api/v1/incidents/{incident_id}"), 200)
    body = {
        "client_request_id": str(uuid4()),
        "expected_incident_version": before["version"],
        "action_id": "simulate_connection_pool_recovery",
        "parameters": {"state_id": state["id"], "expected_state_version": state["version"]},
        "origin": "human",
        "investigation_id": investigation["id"],
        "evidence_refs": report["recommended_actions"][0]["evidence_ids"],
        "explanation": (
            "I reviewed the investigation and explicitly propose isolated synt"
            "hetic connection-pool recovery; this is a human-authored action."
        ),
    }
    proposal = checked(
        engineer.post(f"/api/v1/incidents/{incident_id}/resolution-proposals", json=body), 201
    )
    replay = checked(
        engineer.post(f"/api/v1/incidents/{incident_id}/resolution-proposals", json=body), 201
    )
    if replay["id"] != proposal["id"]:
        raise RuntimeError("Proposal replay created a duplicate")
    path = f"/api/v1/resolution-proposals/{proposal['id']}"
    pending = checked(
        engineer.post(f"{path}/submit", json={"expected_version": proposal["version"]}), 200
    )
    review = {
        "expected_version": pending["version"],
        "reason": "Second human reviewed the evidence and synthetic target.",
    }
    if viewer.post(f"{path}/approve", json=review).status_code != 403:
        raise RuntimeError("Viewer approval was not denied")
    if engineer.post(f"{path}/approve", json=review).status_code != 403:
        raise RuntimeError("Self-approval of mutation was not denied")
    if (
        viewer.post(f"{path}/submit", json={"expected_version": pending["version"]}).status_code
        != 403
    ):
        raise RuntimeError("Viewer execution eligibility mutation was not denied")
    queued = checked(admin.post(f"{path}/approve", json=review), 200)
    if queued["status"] != "queued":
        raise RuntimeError("Proposal became stale before approval; repeat with fresh state")
    if admin.post(f"{path}/approve", json=review).status_code != 409:
        raise RuntimeError("Replayed approval was not rejected")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = checked(engineer.get(path), 200)
        if current["status"] == "succeeded":
            break
        if current["status"] in {"failed", "expired", "cancelled"}:
            raise RuntimeError("Resolution did not succeed; inspect safe workflow error/status")
        time.sleep(0.2)
    else:
        raise RuntimeError("Resolution did not finish; start the resolution worker")
    result = checked(engineer.get(f"{path}/result"), 200)
    if (
        not result["verification"]["contract_satisfied"]
        or result["verification"]["incident_resolution_confirmed"]
    ):
        raise RuntimeError("Invalid synthetic verification contract")
    if checked(engineer.get(f"/api/v1/incidents/{incident_id}"), 200) != before:
        raise RuntimeError("Synthetic execution changed incident lifecycle")
    replay = checked(
        engineer.post(f"/api/v1/incidents/{incident_id}/resolution-proposals", json=body), 201
    )
    if (
        replay["id"] != proposal["id"]
        or checked(engineer.get(f"{path}/result"), 200)["id"] != result["id"]
    ):
        raise RuntimeError("Replay duplicated execution")
    # Only the original Phase 1 endpoint may resolve the incident, by explicit human request.
    investigating = checked(
        engineer.post(
            f"/api/v1/incidents/{incident_id}/transitions",
            json={
                "expected_version": before["version"],
                "status": "investigating",
                "reason": "Human reviewed synthetic action evidence.",
            },
        ),
        200,
    )
    resolved = checked(
        engineer.post(
            f"/api/v1/incidents/{incident_id}/transitions",
            json={
                "expected_version": investigating["version"],
                "status": "resolved",
                "reason": "Human explicitly resolves the synthetic demo after verification.",
            },
        ),
        200,
    )
    timeline = []
    cursor = None
    while True:
        params = {"limit": 10}
        if cursor:
            params["cursor"] = cursor
        page = checked(viewer.get(f"/api/v1/incidents/{incident_id}/timeline", params=params), 200)
        timeline.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    if {item["source_type"] for item in timeline} != {
        "incident_event",
        "detection",
        "investigation",
        "resolution_event",
    }:
        raise RuntimeError("Timeline did not include all four sources")
    # These are actual records, with source IDs and timestamps; no inferred execution events.
    print(json.dumps({"timeline": timeline}), flush=True)
    return {
        "synthetic": True,
        "incident_id": incident_id,
        "investigation_id": investigation["id"],
        "proposal_id": proposal["id"],
        "execution_id": result["id"],
        "verified": True,
        "unauthorized_approval_denied": True,
        "self_approval_denied": True,
        "replay_single_execution": True,
        "incident_unchanged_by_worker": True,
        "explicit_human_incident_status": resolved["status"],
        "timeline_events": len(timeline),
        "before_state_version": result["before_version"],
        "after_state_version": result["after_version"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, choices=range(10, 181), default=120)
    args = parser.parse_args()
    try:
        with ExitStack() as stack:
            clients = [
                stack.enter_context(httpx.Client(base_url=api_url(), timeout=10)) for _ in range(3)
            ]
            users = [
                login(client, role)
                for client, role in zip(clients, ("admin", "engineer", "viewer"), strict=True)
            ]
            if len({user["id"] for user in users}) != 3:
                raise RuntimeError("Use three distinct human demo accounts")
            print(json.dumps(run_demo(*clients, args.timeout)), flush=True)
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        raise SystemExit(f"Resolution demo failed: {message}") from None


if __name__ == "__main__":
    main()
