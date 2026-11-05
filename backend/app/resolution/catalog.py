import hashlib
import json
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.core.errors import DomainError
from app.schemas.common import InputModel

ActionId = Literal[
    "inspect_service_health",
    "inspect_database_connections",
    "inspect_request_latency",
    "simulate_service_recovery",
    "simulate_connection_pool_recovery",
]
ScenarioId = Literal[
    "database_exhaustion",
    "service_unhealthy",
    "latency_degradation",
    "healthy",
    "dependency_outage",
]


def digest(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ActionParameters(InputModel):
    state_id: UUID
    expected_state_version: int = Field(ge=1)


class SyntheticSnapshot(InputModel):
    synthetic: Literal[True] = True
    scenario: ScenarioId
    health: Literal["healthy", "degraded", "unavailable"]
    request_error_rate: float = Field(ge=0, le=1, allow_inf_nan=False)
    dependency_condition: Literal["healthy", "database_pressure", "slow", "unavailable"]
    pool_size: int = Field(ge=1, le=1000)
    active_connections: int = Field(ge=0, le=1000)
    waiting_requests: int = Field(ge=0, le=10000)
    timeout_indicator: bool
    request_count: int = Field(ge=0, le=100000)
    p50_ms: int = Field(ge=0, le=60000)
    p95_ms: int = Field(ge=0, le=60000)
    endpoint: Literal["/synthetic/payments"] = "/synthetic/payments"
    dependency_delay_ms: int = Field(ge=0, le=60000)


class ActionResult(InputModel):
    synthetic: Literal[True] = True
    action_id: ActionId
    before: SyntheticSnapshot
    after: SyntheticSnapshot
    findings: list[str] = Field(min_length=1, max_length=5)
    observed_at: str


class Verification(InputModel):
    synthetic: Literal[True] = True
    contract_satisfied: bool
    improved: bool
    summary: str = Field(max_length=500)
    incident_resolution_confirmed: Literal[False] = False


@dataclass(frozen=True)
class ActionDefinition:
    id: ActionId
    description: str
    mutates_synthetic_state: bool = False
    version: int = 1
    timeout_seconds: int = 5

    def public(self) -> dict:
        return {
            "id": self.id,
            "version": self.version,
            "description": self.description,
            "risk": "synthetic_mutation" if self.mutates_synthetic_state else "read_only",
            "approval_required": True,
            "distinct_approver_required": self.mutates_synthetic_state,
            "mutates_synthetic_state": self.mutates_synthetic_state,
            "timeout_seconds": self.timeout_seconds,
            "parameter_schema": ActionParameters.model_json_schema(),
            "result_schema": ActionResult.model_json_schema(),
            "verification_schema": Verification.model_json_schema(),
        }

    @property
    def fingerprint(self) -> str:
        return digest(self.public())


# This literal registry is the entire dispatch boundary. No dynamic imports or callables from input.
CATALOG = {
    item.id: item
    for item in (
        ActionDefinition("inspect_service_health", "Inspect a synthetic service health snapshot."),
        ActionDefinition(
            "inspect_database_connections", "Inspect synthetic connection-pool pressure."
        ),
        ActionDefinition("inspect_request_latency", "Inspect synthetic request latency summaries."),
        ActionDefinition(
            "simulate_service_recovery", "Recover isolated synthetic service state.", True
        ),
        ActionDefinition(
            "simulate_connection_pool_recovery", "Correct isolated synthetic pool pressure.", True
        ),
    )
}


def action_definition(action_id: str) -> ActionDefinition:
    if action_id not in CATALOG:
        raise DomainError(422, "unknown_action", "Select an available catalog action")
    return CATALOG[action_id]
