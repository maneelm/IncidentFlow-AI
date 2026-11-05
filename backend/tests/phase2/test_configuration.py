from datetime import UTC, datetime
from secrets import token_urlsafe
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.actors import SystemActor
from app.core.config import Settings
from app.core.log_validation import validate_metadata
from app.simulations.scenarios import api_url, scenario_batch


@pytest.mark.parametrize(
    "changes",
    [
        {"ingestion_max_request_bytes": 0},
        {"log_max_age_seconds": -1},
        {"log_max_future_seconds": 301},
        {"worker_max_attempts": 0},
        {"worker_max_attempts": 11},
        {"worker_batch_size": 101},
        {"worker_poll_seconds": 0},
        {"worker_timeout_seconds": 61},
        {"worker_lease_seconds": 10, "worker_timeout_seconds": 10},
        {"worker_backoff_seconds": 0},
    ],
)
def test_phase2_settings_validation(changes: dict) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+asyncpg://test@localhost/test",
            jwt_secret=token_urlsafe(48),
            **changes,
        )


@pytest.mark.parametrize("name", ["", "x" * 121, "system actor", "Admin", "system\nactor"])
def test_system_actor_names_are_bounded(name: str) -> None:
    with pytest.raises(ValueError):
        SystemActor(name)
    assert SystemActor("detection_worker").name == "detection_worker"


@pytest.mark.parametrize(
    "field",
    [
        "PASSWORD",
        "access_token",
        "refreshToken",
        "api-key",
        "nested_secret",
        "Authorization",
        "Cookie",
        "credentials",
    ],
)
def test_credential_metadata_fields_rejected_at_all_depths(field: str) -> None:
    with pytest.raises(ValueError):
        validate_metadata({"safe": [{field: "not-persisted"}]})


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example",
        "https://user:secret@example.com",
        "https://example.com/path",
        "file:///tmp",
        "https://example.com?token=secret",
    ],
)
def test_simulator_does_not_send_credentials_to_unsafe_origins(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("SIM_API_URL", url)
    with pytest.raises(ValueError):
        api_url()


def test_simulator_refuses_naive_anchor_time() -> None:
    with pytest.raises(ValidationError):
        scenario_batch("database", uuid4(), "demo", 42, datetime(2026, 11, 2))
    assert len(scenario_batch("database", uuid4(), "demo", 42, datetime.now(UTC)).logs) == 10
