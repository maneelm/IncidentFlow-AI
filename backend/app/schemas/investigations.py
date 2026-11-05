from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field

from app.models.investigations import InvestigationStatus
from app.schemas.common import InputModel, ReadModel


class InvestigationCreate(InputModel):
    client_request_id: UUID = Field(default_factory=uuid4)


class InvestigationCapability(ReadModel):
    provider: str
    model: str
    configured: bool
    synthetic: bool


class InvestigationRead(ReadModel):
    id: UUID
    incident_id: UUID
    requested_by: UUID
    client_request_id: UUID
    status: InvestigationStatus
    provider: str
    model: str
    stage: str
    llm_calls: int
    input_tokens_reserved: int
    output_tokens_reserved: int
    actual_input_tokens: int
    actual_output_tokens: int
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_by: UUID | None
    error_code: str | None


class InvestigationPage(InputModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class EvidenceRead(ReadModel):
    reference: str
    source_type: str
    label: str
    excerpt: str
    role: str
    provenance: dict[str, Any]
    quarantined: bool


class StepRead(ReadModel):
    attempt: int
    stage: str
    status: str
    details: dict[str, Any]
    created_at: datetime


class Observation(InputModel):
    statement: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(min_length=1, max_length=64)


class Hypothesis(InputModel):
    title: str = Field(min_length=1, max_length=120)
    explanation: str = Field(min_length=1, max_length=800)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    missing_or_contradicting_evidence: list[str] = Field(min_length=1, max_length=5)
    confidence: Literal["low", "medium"]
    verification_step: str = Field(min_length=1, max_length=400)


class HypothesisOutput(InputModel):
    hypotheses: list[Hypothesis] = Field(min_length=2, max_length=4)


class RecommendedAction(InputModel):
    description: str = Field(min_length=1, max_length=400)
    rationale: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    risk: Literal["read_only"]
    requires_human_review: Literal[True]


class ActionOutput(InputModel):
    actions: list[RecommendedAction] = Field(min_length=1, max_length=5)


class ReportMetadata(InputModel):
    investigation_id: UUID
    incident_id: UUID
    started_at: datetime
    completed_at: datetime
    provider: str
    model: str
    status: Literal["completed", "inconclusive"]
    evidence_count: int
    log_count: int
    synthetic_provider: bool
    calls_reserved: int


class ReportRead(InputModel):
    summary: str = Field(min_length=1, max_length=1000)
    observed_evidence: list[Observation] = Field(min_length=1, max_length=12)
    hypotheses: list[Hypothesis] = Field(max_length=4)
    recommended_actions: list[RecommendedAction] = Field(min_length=1, max_length=5)
    limitations: list[str] = Field(min_length=1, max_length=12)
    metadata: ReportMetadata
