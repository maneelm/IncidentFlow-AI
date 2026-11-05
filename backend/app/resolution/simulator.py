from datetime import datetime

from app.resolution.catalog import ActionResult, ScenarioId, SyntheticSnapshot, Verification


def scenario_snapshot(scenario: ScenarioId) -> SyntheticSnapshot:
    data = SyntheticSnapshot(
        scenario=scenario,
        health="healthy",
        request_error_rate=0.0,
        dependency_condition="healthy",
        pool_size=20,
        active_connections=4,
        waiting_requests=0,
        timeout_indicator=False,
        request_count=1000,
        p50_ms=30,
        p95_ms=80,
        dependency_delay_ms=10,
    )
    if scenario == "database_exhaustion":
        data = data.model_copy(
            update=dict(
                health="degraded",
                request_error_rate=0.4,
                dependency_condition="database_pressure",
                active_connections=20,
                waiting_requests=50,
                timeout_indicator=True,
                p50_ms=800,
                p95_ms=4000,
                dependency_delay_ms=500,
            )
        )
    elif scenario == "service_unhealthy":
        data = data.model_copy(update=dict(health="unavailable", request_error_rate=0.8))
    elif scenario == "latency_degradation":
        data = data.model_copy(
            update=dict(
                health="degraded",
                dependency_condition="slow",
                p50_ms=500,
                p95_ms=3000,
                dependency_delay_ms=450,
            )
        )
    elif scenario == "dependency_outage":
        data = data.model_copy(
            update=dict(
                health="unavailable",
                request_error_rate=1.0,
                dependency_condition="unavailable",
                timeout_indicator=True,
            )
        )
    return SyntheticSnapshot.model_validate(data.model_dump())


def simulate(
    action_id: str, before: SyntheticSnapshot, now: datetime
) -> tuple[ActionResult, Verification]:
    """Pure bounded computation: no I/O, clock reads, tools or executable input."""
    after = before
    if action_id == "inspect_service_health":
        findings = [
            f"Synthetic health: {before.health}; dependency: {before.dependency_condition}."
        ]
    elif action_id == "inspect_database_connections":
        findings = [
            f"Synthetic pool {before.active_connections}/{before.pool_size}; "
            f"waiting {before.waiting_requests}."
        ]
    elif action_id == "inspect_request_latency":
        findings = [
            f"Synthetic {before.request_count} requests; p50 {before.p50_ms}ms; "
            f"p95 {before.p95_ms}ms."
        ]
    elif action_id in {"simulate_service_recovery", "simulate_connection_pool_recovery"}:
        if before.dependency_condition != "unavailable":
            after = scenario_snapshot("healthy").model_copy(update={"scenario": before.scenario})
        findings = [
            ("Applied the deterministic synthetic recovery contract; no real service was changed.")
        ]
    else:
        raise ValueError("Unknown trusted action definition")
    mutating = action_id.startswith("simulate_")
    satisfied = not mutating or (after.health == "healthy" and not after.timeout_indicator)
    verification = Verification(
        contract_satisfied=satisfied,
        improved=before != after,
        summary="Synthetic contract satisfied."
        if satisfied
        else "Synthetic dependency remains unavailable; contract failed.",
    )
    return ActionResult(
        action_id=action_id,
        before=before,
        after=after,
        findings=findings,
        observed_at=now.isoformat(),
    ), verification
