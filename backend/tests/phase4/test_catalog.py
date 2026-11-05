from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.resolution.catalog import CATALOG, ActionParameters
from app.resolution.simulator import scenario_snapshot, simulate
from app.schemas.resolution import ProposalCreate


def test_catalog_is_fixed_typed_and_approval_required():
    assert len(CATALOG) == 5
    for definition in CATALOG.values():
        public = definition.public()
        assert public["approval_required"] is True
        assert public["distinct_approver_required"] == definition.mutates_synthetic_state
        assert public["parameter_schema"]["additionalProperties"] is False
        assert len(definition.fingerprint) == 64


@pytest.mark.parametrize("action_id", list(CATALOG))
def test_simulator_is_reproducible_and_never_confirms_incident(action_id):
    before = scenario_snapshot("database_exhaustion")
    first = simulate(action_id, before, datetime(2026, 1, 1, tzinfo=UTC))
    assert first == simulate(action_id, before, datetime(2026, 1, 1, tzinfo=UTC))
    result, verification = first
    assert verification.contract_satisfied
    assert verification.incident_resolution_confirmed is False
    assert result.synthetic and result.before == before
    assert (result.before != result.after) == CATALOG[action_id].mutates_synthetic_state


@pytest.mark.parametrize(
    "scenario",
    [
        "database_exhaustion",
        "service_unhealthy",
        "latency_degradation",
        "healthy",
        "dependency_outage",
    ],
)
def test_recovery_contract_and_replay(scenario):
    result, verification = simulate(
        "simulate_service_recovery", scenario_snapshot(scenario), datetime.now(UTC)
    )
    assert verification.contract_satisfied == (scenario != "dependency_outage")
    replay, _ = simulate("simulate_service_recovery", result.after, datetime.now(UTC))
    assert replay.after == result.after


@pytest.mark.parametrize(
    "extra",
    [
        {"command": "rm -rf /"},
        {"script": "evil.py"},
        {"url": "http://example.com"},
        {"callable": "os.system"},
    ],
)
def test_no_executable_parameters(extra):
    with pytest.raises(ValidationError):
        ActionParameters(state_id=uuid4(), expected_state_version=1, **extra)


def test_ai_origin_cannot_author_mutation():
    with pytest.raises(ValidationError):
        ProposalCreate(
            client_request_id=uuid4(),
            expected_incident_version=1,
            action_id="simulate_service_recovery",
            parameters={"state_id": uuid4(), "expected_state_version": 1},
            explanation="Untrusted model suggestion.",
            origin="investigation",
            investigation_id=uuid4(),
            recommendation_index=0,
            evidence_refs=["log:fake"],
        )
