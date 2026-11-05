from typing import TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: list[dict[str, str | list[str | int]]] = []


class ErrorResponse(BaseModel):
    error: ErrorDetail
