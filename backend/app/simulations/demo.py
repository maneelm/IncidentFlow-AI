"""Provision, ingest, observe, and replay synthetic failures using only HTTP APIs."""

import argparse
import json
import os
import time
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from app.simulations.scenarios import SCENARIOS, api_url, scenario_batch


def checked(response: httpx.Response, expected: int) -> dict[str, Any]:
    if response.status_code != expected:
        raise RuntimeError(
            f"Demo request failed (HTTP {response.status_code}); inspect API readiness and role"
        )
    return response.json()


def wait_for_jobs(client: httpx.Client, service_id: str, timeout: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = checked(
            client.get("/api/v1/detection-jobs", params={"service_id": service_id, "limit": 100}),
            200,
        )
        if any(job["status"] == "failed" for job in jobs["items"]):
            raise RuntimeError("Detection job failed; inspect the admin detection-jobs endpoint")
        if jobs["total"] and all(job["status"] == "completed" for job in jobs["items"]):
            return jobs
        time.sleep(0.2)
    raise RuntimeError("Detection jobs did not finish; start the separate detection worker")


def demonstrate(client: httpx.Client, scenario: str, seed: int, timeout: float) -> dict[str, Any]:
    definition = SCENARIOS[scenario]
    run_id = uuid4().hex[:12]
    service = checked(
        client.post(
            "/api/v1/services",
            json={
                "name": f"sim-{definition['service']}-{run_id}",
                "description": "SYNTHETIC Phase 2 demonstration",
                "environment": "simulation",
            },
        ),
        201,
    )
    rule = checked(
        client.post(
            "/api/v1/detection-rules",
            json={
                "service_id": service["id"],
                "name": definition["name"],
                "level": definition["level"],
                "error_code": definition["error_code"],
                "threshold": definition["threshold"],
                "window_seconds": 60,
                "incident_severity": definition["incident_severity"],
                "cooldown_seconds": 60,
            },
        ),
        201,
    )
    credential = checked(
        client.post(
            "/api/v1/ingestion-credentials",
            json={
                "service_id": service["id"],
                "name": "temporary synthetic producer",
                "expires_in_days": 1,
            },
        ),
        201,
    )
    try:
        batch = scenario_batch(scenario, UUID(service["id"]), run_id, seed, datetime.now(UTC))
        payload = batch.model_dump(mode="json")
        machine_headers = {"X-Ingestion-Key": credential["token"]}
        first = checked(
            client.post("/api/v1/logs/ingest", headers=machine_headers, json=payload), 202
        )
        if first["accepted"] != len(batch.logs):
            raise RuntimeError("Unexpected first-delivery ingestion count")
        jobs = wait_for_jobs(client, service["id"], timeout)
        detections = checked(
            client.get("/api/v1/detections", params={"service_id": service["id"]}), 200
        )
        if detections["total"] != definition["expected_incidents"]:
            raise RuntimeError("Detection result did not match the scenario fixture")
        summary: dict[str, Any] = {
            "scenario": scenario,
            "service_id": service["id"],
            "rule_id": rule["id"],
            "accepted": first["accepted"],
            "completed_jobs": jobs["total"],
            "incidents": detections["total"],
        }
        if detections["items"]:
            detection = detections["items"][0]
            incident = checked(client.get(f"/api/v1/incidents/{detection['incident_id']}"), 200)
            evidence = checked(client.get(f"/api/v1/detections/{detection['id']}/logs"), 200)
            events = checked(client.get(f"/api/v1/incidents/{incident['id']}/events"), 200)
            if events["total"] != 1 or events["items"][0]["system_actor"] != "detection_worker":
                raise RuntimeError("Automatic incident lacks its expected system audit event")
            summary |= {
                "incident_id": incident["id"],
                "incident_number": incident["incident_number"],
                "detection_id": detection["id"],
                "evidence_logs": evidence["total"],
                "audit_events": events["total"],
            }
        replay = checked(
            client.post("/api/v1/logs/ingest", headers=machine_headers, json=payload), 202
        )
        after = checked(client.get("/api/v1/detections", params={"service_id": service["id"]}), 200)
        if (
            replay["accepted"] != 0
            or replay["duplicates"] != len(batch.logs)
            or after["total"] != detections["total"]
        ):
            raise RuntimeError("Replay did not preserve source-event and incident idempotency")
        summary |= {
            "replay_accepted": replay["accepted"],
            "replay_duplicates": replay["duplicates"],
        }
        return summary
    finally:
        checked(client.post(f"/api/v1/ingestion-credentials/{credential['id']}/revoke"), 200)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["all", *SCENARIOS], default="all")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--timeout", type=int, choices=range(5, 61), default=30)
    args = parser.parse_args()
    email, password = os.environ.get("DEMO_ADMIN_EMAIL"), os.environ.get("DEMO_ADMIN_PASSWORD")
    if not email or not password:
        raise SystemExit("Set DEMO_ADMIN_EMAIL and DEMO_ADMIN_PASSWORD for an existing admin")
    try:
        with httpx.Client(base_url=api_url(), timeout=10) as client:
            token = checked(
                client.post("/api/v1/auth/token", data={"username": email, "password": password}),
                200,
            )
            client.headers["Authorization"] = f"Bearer {token['access_token']}"
            me = checked(client.get("/api/v1/auth/me"), 200)
            if me["role"] != "admin":
                raise RuntimeError("The complete demo requires an administrator")
            for scenario in SCENARIOS if args.scenario == "all" else [args.scenario]:
                print(json.dumps(demonstrate(client, scenario, args.seed, args.timeout)))
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        # Fixed messages above and exception class only for HTTP/validation failures.
        message = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        raise SystemExit(f"Demo failed: {message}") from None


if __name__ == "__main__":
    main()
