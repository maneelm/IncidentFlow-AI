from datetime import UTC, datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, JsonValue, field_validator, model_validator

from app.core.log_validation import SENSITIVE_VALUE, validate_metadata
from app.models.enums import LogLevel
from app.schemas.common import InputModel, ReadModel


class LogInput(InputModel):
    source_event_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$"
    )
    level: LogLevel
    message: str = Field(min_length=1, max_length=4000)
    timestamp: AwareDatetime
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    error_code: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[A-Z0-9_.:-]+$"
    )
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("message", "trace_id", "request_id")
    @classmethod
    def safe_text(cls, value: str | None) -> str | None:
        if value is not None:
            value.encode("utf-8")
            if "\x00" in value or SENSITIVE_VALUE.search(value):
                raise ValueError("Log text contains forbidden characters or credential material")
        return value

    @field_validator("level", mode="before")
    @classmethod
    def normalize_level(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("error_code", mode="before")
    @classmethod
    def normalize_error_code(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("timestamp")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)

    @field_validator("metadata")
    @classmethod
    def bounded_safe_metadata(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        return validate_metadata(value)


class LogBatch(InputModel):
    service_id: UUID
    logs: list[LogInput] = Field(min_length=1, max_length=100)


class IngestedItem(ReadModel):
    source_event_id: str
    log_id: UUID
    job_id: UUID
    created: bool


class IngestResult(ReadModel):
    accepted: int
    duplicates: int
    items: list[IngestedItem]


class LogRead(ReadModel):
    id: UUID
    source_event_id: str
    service_id: UUID
    level: LogLevel
    message: str
    timestamp: datetime
    ingested_at: datetime
    trace_id: str | None
    request_id: str | None
    error_code: str | None
    metadata: dict[str, JsonValue] = Field(validation_alias="details")


class LogFilters(InputModel):
    service_id: UUID | None = None
    level: LogLevel | None = None
    timestamp_from: AwareDatetime | None = None
    timestamp_to: AwareDatetime | None = None
    trace_id: str | None = Field(default=None, min_length=1, max_length=128)
    request_id: str | None = Field(default=None, min_length=1, max_length=128)
    incident_id: UUID | None = None
    detection_id: UUID | None = None
    search: str | None = Field(default=None, min_length=2, max_length=100)
    sort_order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=100000)

    @model_validator(mode="after")
    def valid_range_and_search(self) -> Self:
        if self.timestamp_from and self.timestamp_to and self.timestamp_from > self.timestamp_to:
            raise ValueError("timestamp_from must be at or before timestamp_to")
        if self.search and len(self.search.split()) > 10:
            raise ValueError("Search accepts at most 10 terms")
        return self
