from typing import Literal, Self

from pydantic import Field, PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: PostgresDsn = Field(repr=False)
    jwt_secret: SecretStr
    jwt_access_token_minutes: int = Field(default=15, ge=1, le=30)
    environment: Literal["development", "test", "production"] = "development"
    cors_origins: list[str] = ["http://localhost:5173"]
    jwt_issuer: str = "incidentflow"
    jwt_audience: str = "incidentflow-api"
    ingestion_max_request_bytes: int = Field(default=524288, ge=1024, le=2097152)
    log_max_age_seconds: int = Field(default=86400, ge=60, le=604800)
    log_max_future_seconds: int = Field(default=120, ge=0, le=300)
    worker_batch_size: int = Field(default=10, ge=1, le=100)
    worker_poll_seconds: float = Field(default=1, ge=0.05, le=60)
    worker_lease_seconds: int = Field(default=60, ge=10, le=3600)
    worker_timeout_seconds: int = Field(default=10, ge=1, le=60)
    worker_max_attempts: int = Field(default=5, ge=1, le=10)
    worker_backoff_seconds: int = Field(default=2, ge=1, le=60)
    groq_api_key: SecretStr | None = None
    investigation_llm_provider: Literal["groq", "fake"] = "groq"
    investigation_llm_model: str = Field(default="", max_length=100, pattern=r"^[A-Za-z0-9_./:-]*$")
    investigation_llm_timeout_seconds: int = Field(default=20, ge=1, le=60)
    investigation_max_llm_calls: int = Field(default=4, ge=2, le=8)
    investigation_max_input_tokens: int = Field(default=65536, ge=1024, le=262144)
    investigation_max_output_tokens: int = Field(default=8192, ge=1024, le=16384)
    investigation_max_duration_seconds: int = Field(default=180, ge=10, le=600)
    investigation_lease_seconds: int = Field(default=90, ge=15, le=180)
    investigation_max_attempts: int = Field(default=3, ge=1, le=5)
    investigation_max_logs: int = Field(default=20, ge=2, le=50)
    investigation_max_knowledge: int = Field(default=4, ge=1, le=8)
    resolution_approval_ttl_seconds: int = Field(default=900, ge=30, le=3600)
    resolution_lease_seconds: int = Field(default=30, ge=15, le=120)
    resolution_max_attempts: int = Field(default=3, ge=1, le=5)

    @field_validator("database_url")
    @classmethod
    def async_driver(cls, value: PostgresDsn) -> PostgresDsn:
        if value.scheme != "postgresql+asyncpg":
            raise ValueError("DATABASE_URL must use postgresql+asyncpg")
        return value

    @field_validator("jwt_secret")
    @classmethod
    def strong_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < 43 or len(set(secret)) < 16:
            raise ValueError("JWT_SECRET must be a random secret with at least 43 characters")
        return value

    @field_validator("cors_origins")
    @classmethod
    def explicit_origins(cls, origins: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        for origin in origins:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
            ):
                raise ValueError("CORS origins must be explicit http(s) origins without paths")
        return origins

    @model_validator(mode="after")
    def production_origins(self) -> Self:
        if self.investigation_lease_seconds < self.investigation_llm_timeout_seconds + 10:
            raise ValueError("Investigation lease must exceed LLM timeout by at least ten seconds")
        if self.investigation_max_duration_seconds <= self.investigation_llm_timeout_seconds:
            raise ValueError("Investigation duration must exceed one LLM call timeout")
        if self.environment == "production" and self.investigation_llm_provider == "fake":
            raise ValueError("The fake investigation provider is only for development and tests")
        if self.worker_lease_seconds < self.worker_timeout_seconds + 5:
            raise ValueError("Worker lease must exceed processing timeout by at least five seconds")
        if self.environment == "production" and any(
            not origin.startswith("https://") for origin in self.cors_origins
        ):
            raise ValueError("Production CORS origins must use HTTPS (or an empty list)")
        return self
