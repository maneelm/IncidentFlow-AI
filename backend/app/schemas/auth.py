from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, SecretStr, field_validator

from app.models import Role
from app.schemas.common import InputModel, ReadModel


class LoginInput(InputModel):
    username: EmailStr
    password: SecretStr = Field(min_length=1, max_length=1024)


class BootstrapInput(InputModel):
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=1024)
    display_name: str = Field(min_length=1, max_length=120)
    role: Role

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value: str) -> str:
        return value.lower()


class UserRead(ReadModel):
    id: UUID
    email: str
    display_name: str
    role: Role
    created_at: datetime
    updated_at: datetime


class TokenRead(ReadModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
