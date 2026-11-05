from datetime import datetime
from typing import Any, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.models import EventType, IncidentStatus, Severity
from app.schemas.common import InputModel, ReadModel


class IncidentCreate(InputModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=50000)
    severity: Severity
    service_id: UUID
    assignee_id: UUID | None = None


class IncidentUpdate(InputModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=50000)
    severity: Severity | None = None
    service_id: UUID | None = None

    @model_validator(mode="after")
    def nonempty_patch(self) -> Self:
        fields = self.model_fields_set - {"expected_version"}
        if not fields or any(getattr(self, field) is None for field in fields):
            raise ValueError("Provide at least one non-null incident field to update")
        return self


class AssignmentInput(InputModel):
    expected_version: int = Field(ge=1)
    assignee_id: UUID | None  # Explicit null unassigns; omission is an error.


class TransitionInput(InputModel):
    expected_version: int = Field(ge=1)
    status: IncidentStatus
    reason: str = Field(min_length=1, max_length=2000)


class IncidentRead(ReadModel):
    id: UUID
    incident_number: str
    title: str
    description: str
    severity: Severity
    status: IncidentStatus
    service_id: UUID
    assignee_id: UUID | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None
    version: int


class EventRead(ReadModel):
    id: UUID
    incident_id: UUID
    event_type: EventType
    actor_id: UUID | None
    system_actor: str | None
    occurred_at: datetime
    incident_version: int
    details: dict[str, Any]


class IncidentFilters(InputModel):
    status: IncidentStatus | None = None
    severity: Severity | None = None
    service_id: UUID | None = None
    assignee_id: UUID | None = None
    unassigned: bool = False
    created_from: AwareDatetime | None = None
    created_to: AwareDatetime | None = None
    sort_by: Literal["created_at", "updated_at", "severity", "incident_number"] = "created_at"
    sort_order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def compatible_filters(self) -> Self:
        if self.created_from and self.created_to and self.created_from > self.created_to:
            raise ValueError("created_from must be at or before created_to")
        if self.unassigned and self.assignee_id:
            raise ValueError("unassigned and assignee_id cannot be combined")
        return self
