"""Seeded synthetic application logs, delivered through the real ingestion API."""

import argparse
import json
import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx

from app.schemas.logs import LogBatch

SCENARIOS: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "scenarios.json").read_text(encoding="utf-8")
)


def scenario_batch(
    name: str, service_id: UUID, run_id: str, seed: int, anchor: datetime
) -> LogBatch:
    definition = SCENARIOS[name]
    generator = random.Random(seed)
    logs = []
    for index in range(definition["count"]):
        metadata = {"synthetic": True, "scenario": name, "seed": seed, "sequence": index}
        if name == "latency":
            metadata["latency_ms"] = generator.randint(1500, 2500)
            metadata["breach_threshold_ms"] = 1000
        elif name == "api-errors":
            metadata["http_status"] = 500
        else:
            metadata["pool_size"] = 20
            metadata["connections_in_use"] = 20
        logs.append(
            {
                "source_event_id": str(uuid5(NAMESPACE_URL, f"{run_id}/{name}/{seed}/{index}")),
                "level": definition["level"],
                "message": f"SYNTHETIC {definition['name']}: request {index} failed",
                "timestamp": anchor
                - timedelta(milliseconds=index * 300 + generator.randint(0, 100)),
                "error_code": definition["error_code"],
                "trace_id": f"synthetic-{run_id}-{index}",
                "request_id": f"request-{index}",
                "metadata": metadata,
            }
        )
    if name == "normal":
        for index in range(20):
            logs.append(
                {
                    "source_event_id": str(uuid5(NAMESPACE_URL, f"{run_id}/normal/info/{index}")),
                    "level": "info",
                    "message": "SYNTHETIC request completed successfully",
                    "timestamp": anchor - timedelta(milliseconds=index * 100),
                    "metadata": {"synthetic": True, "scenario": name, "http_status": 200},
                }
            )
    return LogBatch(service_id=service_id, logs=logs)


def api_url() -> str:
    value = os.environ.get("SIM_API_URL", "http://127.0.0.1:8000").rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
        or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})
    ):
        raise ValueError(
            "SIM_API_URL must be a local HTTP or explicit HTTPS origin without credentials"
        )
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=list(SCENARIOS), required=True)
    parser.add_argument("--service-id", type=UUID, required=True)
    parser.add_argument(
        "--run-id", default=None, help="New run by default; reuse only with identical data"
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=1)
    parser.add_argument("--anchor-time", type=datetime.fromisoformat, default=None)
    args = parser.parse_args()
    token = os.environ.get("INGESTION_KEY")
    if not token:
        raise SystemExit("Set INGESTION_KEY to a scoped service credential; it will not be printed")
    try:
        batch = scenario_batch(
            args.scenario,
            args.service_id,
            args.run_id or uuid4().hex[:12],
            args.seed,
            args.anchor_time or datetime.now(UTC),
        )
        with httpx.Client(base_url=api_url(), timeout=10) as client:
            for _ in range(args.repeat):
                response = client.post(
                    "/api/v1/logs/ingest",
                    headers={"X-Ingestion-Key": token},
                    json=batch.model_dump(mode="json"),
                )
                if response.status_code != 202:
                    raise SystemExit(
                        f"Ingestion rejected the scenario (HTTP {response.status_code})"
                    )
                print(json.dumps(response.json()))
    except (ValueError, httpx.HTTPError):
        raise SystemExit(
            "Scenario failed; check API availability, configuration and timestamp tolerance"
        ) from None


if __name__ == "__main__":
    main()
