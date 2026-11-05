from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.models.resolution import ProposalStatus
from app.resolution.catalog import (
    ActionId,
    ActionParameters,
    ActionResult,
    ScenarioId,
    SyntheticSnapshot,
    Verification,
)
from app.schemas.common import InputModel, ReadModel


class ProposalCreate(InputModel):
    client_request_id: UUID
    expected_incident_version: int = Field(ge=1)
    action_id: ActionId
    parameters: ActionParameters
    explanation: str = Field(min_length=10, max_length=2000)
    origin: Literal["human", "investigation"] = "human"
    investigation_id: UUID | None = None
    recommendation_index: int | None = Field(default=None, ge=0, le=4)
    evidence_refs: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def source_consistent(self):
        if len(set(self.evidence_refs)) != len(self.evidence_refs) or any(
            len(ref) > 80 for ref in self.evidence_refs
        ):
            raise ValueError("Evidence references must be unique and bounded")
        if self.origin == "investigation" and (
            self.investigation_id is None or self.recommendation_index is None
        ):
            raise ValueError("Select an investigation recommendation explicitly")
        if self.origin == "human" and self.recommendation_index is not None:
            raise ValueError("Human proposals cannot select AI recommendation provenance")
        if (self.investigation_id is None) != (not self.evidence_refs):
            raise ValueError(
                "Investigation proposals require evidence; unlinked proposals cannot cite it"
            )
        if self.action_id.startswith("simulate_") and self.origin != "human":
            raise ValueError("Synthetic recovery must be explicitly human authored")
        return self


class VersionInput(InputModel):
    expected_version: int = Field(ge=1)


class ReviewInput(VersionInput):
    reason: str = Field(min_length=5, max_length=1000)


class ScenarioInput(InputModel):
    scenario: ScenarioId
    expected_version: int = Field(ge=0)


class SyntheticStateRead(ReadModel):
    id: UUID
    service_id: UUID
    snapshot: SyntheticSnapshot
    version: int
    initialized_by: UUID
    created_at: datetime
    updated_at: datetime


class ProposalRead(ReadModel):
    id: UUID
    incident_id: UUID
    investigation_id: UUID | None
    service_id: UUID
    state_id: UUID
    incident_version: int
    state_version: int
    action_id: str
    action_fingerprint: str
    parameters: ActionParameters
    provenance: dict[str, Any]
    explanation: str
    origin: str
    risk: str
    requested_by: UUID
    policy: dict[str, Any]
    payload_hash: str
    client_request_id: UUID
    status: ProposalStatus
    version: int
    approval_deadline: datetime | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class ApprovalRead(ReadModel):
    id: UUID
    proposal_id: UUID
    actor_id: UUID
    decision: str
    payload_hash: str
    snapshot: dict[str, Any]
    reason: str
    created_at: datetime


class ExecutionRead(ReadModel):
    id: UUID
    proposal_id: UUID
    job_id: UUID
    attempt: int
    before_version: int
    after_version: int
    result: ActionResult
    verification: Verification
    created_at: datetime


class ResolutionEventRead(ReadModel):
    id: UUID
    proposal_id: UUID
    actor_id: UUID | None
    system_actor: str | None
    event_type: str
    proposal_version: int
    correlation_id: UUID | None
    occurred_at: datetime
    details: dict[str, Any]


class ResolutionFilters(InputModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)
    status: ProposalStatus | None = None


class CatalogRead(InputModel):
    id: ActionId
    version: int
    description: str
    risk: str
    approval_required: Literal[True]
    distinct_approver_required: bool
    mutates_synthetic_state: bool
    timeout_seconds: int
    parameter_schema: dict
    result_schema: dict
    verification_schema: dict


class JobRead(ReadModel):
    id: UUID
    proposal_id: UUID
    status: str
    attempts: int
    max_attempts: int
    available_at: datetime
    last_error_code: str | None
    created_at: datetime
    finished_at: datetime | None
