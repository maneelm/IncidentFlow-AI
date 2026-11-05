from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.common import InputModel, ReadModel


def valid_text(value: str) -> str:
    value.encode("utf-8")
    if "\x00" in value:
        raise ValueError("NUL characters are not accepted")
    return value


class RunbookCreate(InputModel):
    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "title": "Database connection pool troubleshooting",
                    "format": "markdown",
                    "tags": ["database"],
                    "content": "DB_CONNECTION_TIMEOUT: inspect active connections and pool "
                    "sizing using read-only tools.",
                }
            ]
        }
    }
    title: str = Field(min_length=1, max_length=200)
    service_id: UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=10)
    format: Literal["markdown", "text"] = "markdown"
    content: str = Field(min_length=1, max_length=64000)

    @field_validator("content", "title")
    @classmethod
    def bounded_utf8(cls, value: str) -> str:
        valid_text(value)
        if len(value.encode("utf-8")) > 131072:
            raise ValueError("Document exceeds its byte limit")
        return value

    @field_validator("tags")
    @classmethod
    def bounded_tags(cls, value: list[str]) -> list[str]:
        if any(not tag.strip() or len(tag) > 40 for tag in value):
            raise ValueError("Tags must contain 1-40 characters")
        return sorted({valid_text(tag.strip().lower()) for tag in value})


class RunbookUpdate(InputModel):
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    tags: list[str] | None = Field(default=None, max_length=10)
    format: Literal["markdown", "text"] | None = None
    content: str | None = Field(default=None, min_length=1, max_length=64000)

    @model_validator(mode="after")
    def valid_patch(self) -> Self:
        fields = self.model_fields_set - {"expected_version"}
        if not fields or any(getattr(self, name) is None for name in fields):
            raise ValueError("Provide non-null runbook changes")
        RunbookCreate(
            title=self.title or "validation",
            content=self.content or "validation",
            tags=self.tags or [],
        )
        return self


class ReindexInput(InputModel):
    expected_version: int = Field(ge=1)


class RunbookRead(ReadModel):
    id: UUID
    title: str
    service_id: UUID | None
    tags: list[str]
    current_version: int
    created_at: datetime
    updated_at: datetime


class RunbookDocument(RunbookRead):
    format: str
    content: str
    content_hash: str
    chunk_ids: list[UUID]


class KnowledgeSearch(InputModel):
    query: str = Field(min_length=2, max_length=200)
    service_id: UUID | None = None
    limit: int = Field(default=5, ge=1, le=20)


class SearchHit(InputModel):
    source_type: Literal["runbook_chunk", "historical_incident", "resolution_note"]
    source_id: UUID
    title: str
    excerpt: str = Field(max_length=600)
    score: float
    rank: int
    service_relevance: str
    source_timestamp: datetime
    document_version: int | None = None


class NoteCreate(InputModel):
    content: str = Field(min_length=1, max_length=8000)

    @field_validator("content")
    @classmethod
    def valid_content(cls, value: str) -> str:
        return valid_text(value)


class NoteRead(ReadModel):
    id: UUID
    incident_id: UUID
    created_by: UUID
    content: str
    created_at: datetime


class ChunkRead(ReadModel):
    id: UUID
    version_id: UUID
    ordinal: int
    content: str
    start_char: int
    end_char: int
    content_hash: str


class VersionRead(ReadModel):
    id: UUID
    runbook_id: UUID
    version: int
    title: str
    service_id: UUID | None
    tags: list[str]
    format: str
    content: str
    content_hash: str
    created_by: UUID
    created_at: datetime
