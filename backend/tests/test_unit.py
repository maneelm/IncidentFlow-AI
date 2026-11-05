from secrets import token_urlsafe

import pytest
from conftest import validate_test_url
from pydantic import ValidationError

from app.core.config import Settings
from app.models import IncidentStatus
from app.services.incidents import TRANSITIONS


def valid_settings(**changes: object) -> Settings:
    values = {
        "database_url": "postgresql+asyncpg://example:unused@localhost:55432/incidentflow",
        "jwt_secret": token_urlsafe(48),
        "cors_origins": ["http://localhost:5173"],
    }
    return Settings(_env_file=None, **(values | changes))


@pytest.mark.parametrize(
    "values",
    [
        {"jwt_secret": "short"},
        {"jwt_secret": "x" * 64},
        {"database_url": "postgresql://example@localhost/db"},
        {"jwt_access_token_minutes": 0},
        {"jwt_access_token_minutes": 31},
        {"cors_origins": ["*"]},
        {"cors_origins": ["http://localhost:5173/path"]},
        {"environment": "production"},
        {"cors_origins": ["https://user:password@example.com"]},
    ],
)
def test_invalid_configuration(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        valid_settings(**values)


def test_required_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)
    assert {entry["loc"][0] for entry in error.value.errors()} == {"database_url", "jwt_secret"}


def test_production_settings_hide_secrets() -> None:
    settings = valid_settings(environment="production", cors_origins=["https://app.example.com"])
    assert settings.jwt_secret.get_secret_value() not in repr(settings)
    assert "unused" not in repr(settings)
    assert valid_settings(environment="production", cors_origins=[]).cors_origins == []


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://incidentflow:unused@localhost:55432/incidentflow",
        "postgresql+asyncpg://incidentflow_test:unused@localhost:55433/incidentflow",
        "postgresql+asyncpg://incidentflow_test:unused@remote.example:55433/incidentflow_test",
        "postgresql+asyncpg://incidentflow_test:unused@localhost:5432/incidentflow_test",
        "sqlite+aiosqlite:///incidentflow_test",
    ],
)
def test_database_safety_guard(url: str) -> None:
    with pytest.raises(ValueError):
        validate_test_url(url, None)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_safety_guard_rejects_application_database(host: str) -> None:
    url = "postgresql+asyncpg://incidentflow_test:unused@localhost:55433/incidentflow_test"
    with pytest.raises(ValueError, match="must be different"):
        validate_test_url(url, url.replace("localhost", host))


def test_exact_state_machine() -> None:
    assert TRANSITIONS == {
        IncidentStatus.TRIGGERED: {IncidentStatus.INVESTIGATING},
        IncidentStatus.INVESTIGATING: {IncidentStatus.MITIGATED, IncidentStatus.RESOLVED},
        IncidentStatus.MITIGATED: {IncidentStatus.RESOLVED},
        IncidentStatus.RESOLVED: {IncidentStatus.CLOSED, IncidentStatus.INVESTIGATING},
        IncidentStatus.CLOSED: {IncidentStatus.INVESTIGATING},
    }
