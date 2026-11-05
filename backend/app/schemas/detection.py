from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.enums import JobStatus, LogLevel, Severity
from app.schemas.common import InputModel, ReadModel


class CredentialCreate(InputModel):
    service_id: UUID
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int = Field(default=30, ge=1, le=365)


class CredentialRead(ReadModel):
    id: UUID
    service_id: UUID
    name: str
    created_by: UUID
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class CredentialIssued(CredentialRead):
    token: str = Field(repr=False, description="Returned only on creation; store it securely")


class RuleCreate(InputModel):
    service_id: UUID
    name: str = Field(min_length=1, max_length=120)
    enabled: bool = True
    level: LogLevel = LogLevel.ERROR
    error_code: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Z0-9_.:-]+$"
    )
    threshold: int = Field(ge=1, le=1000)
    window_seconds: int = Field(ge=5, le=3600)
    incident_severity: Severity
    cooldown_seconds: int = Field(default=60, ge=0, le=86400)

    @field_validator("error_code", mode="before")
    @classmethod
    def normalize_code(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class RuleUpdate(InputModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    level: LogLevel | None = None
    error_code: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Z0-9_.:-]+$"
    )
    threshold: int | None = Field(default=None, ge=1, le=1000)
    window_seconds: int | None = Field(default=None, ge=5, le=3600)
    incident_severity: Severity | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0, le=86400)

    @field_validator("error_code", mode="before")
    @classmethod
    def normalize_code(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @model_validator(mode="after")
    def nonempty_patch(self) -> Self:
        fields = self.model_fields_set - {"expected_version"}
        if not fields or any(getattr(self, key) is None for key in fields - {"error_code"}):
            raise ValueError("Supply rule changes; only error_code may be null")
        return self


class RuleRead(ReadModel):
    id: UUID
    service_id: UUID
    name: str
    enabled: bool
    level: LogLevel
    error_code: str | None
    threshold: int
    window_seconds: int
    incident_severity: Severity
    cooldown_seconds: int
    effective_from: datetime
    eligible_after: datetime | None
    created_at: datetime
    updated_at: datetime
    version: int


class DetectionRead(ReadModel):
    id: UUID
    detection_key: str
    rule_id: UUID
    service_id: UUID
    incident_id: UUID
    is_current: bool
    rule_version: int
    rule_snapshot: dict[str, Any]
    window_start: datetime
    window_end: datetime
    matching_count: int
    created_at: datetime
    retired_at: datetime | None


class JobRead(ReadModel):
    id: UUID
    log_id: UUID
    service_id: UUID
    position: int
    status: JobStatus
    attempts: int
    total_attempts: int
    requeues: int
    max_attempts: int
    available_at: datetime
    lease_expires_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    finished_at: datetime | None


class ServicePageFilters(InputModel):
    service_id: UUID | None = None
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)


class RuleFilters(ServicePageFilters):
    enabled: bool | None = None


class DetectionFilters(ServicePageFilters):
    rule_id: UUID | None = None


class JobFilters(ServicePageFilters):
    status: JobStatus | None = None
