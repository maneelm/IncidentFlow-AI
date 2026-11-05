from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import InputModel, ReadModel


class ServiceCreate(InputModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    description: str = Field(default="", max_length=10000)
    environment: str = Field(min_length=1, max_length=50)


class ServiceUpdate(InputModel):
    name: str | None = Field(
        default=None, min_length=1, max_length=100, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )
    description: str | None = Field(default=None, max_length=10000)
    environment: str | None = Field(default=None, min_length=1, max_length=50)

    @model_validator(mode="after")
    def nonempty_patch(self) -> Self:
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("Provide at least one non-null service field")
        return self


class ServiceRead(ReadModel):
    id: UUID
    name: str
    description: str
    environment: str
    created_at: datetime
    updated_at: datetime
