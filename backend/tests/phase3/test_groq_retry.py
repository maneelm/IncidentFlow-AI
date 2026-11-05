"""Groq SDK + real graph/checkpoints/PostgreSQL; all HTTP is mocked."""

import json

import groq
import httpx
import pytest
from conftest import Sandbox
from pydantic import SecretStr
from sqlalchemy import select, text

from app.investigation import providers
from app.investigation.graph import STAGES
from app.investigation.providers import FakeProvider, GroqProvider, ProviderResponse
from app.models.investigations import (
    Investigation,
    InvestigationJob,
    InvestigationReport,
    InvestigationStep,
)
from app.schemas.investigations import ActionOutput, HypothesisOutput
from app.workers.investigation_worker import InvestigationWorker

pytestmark = pytest.mark.integration


def sdk_provider(monkeypatch, settings, handler):
    actual = groq.AsyncGroq

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return actual(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

    monkeypatch.setattr(providers, "AsyncGroq", factory)
    configured = settings.model_copy(update={"groq_api_key": SecretStr("synthetic-test-key")})
    return GroqProvider(configured, "openai/gpt-oss-120b", 5)


async def success(body, rationale=None):
    payload = json.loads(body["messages"][1]["content"])
    schema = HypothesisOutput if payload["task"] == "hypotheses" else ActionOutput
    response = await FakeProvider().generate(payload["task"], "", payload, schema, 2048)
    content = json.loads(response.content)
    if rationale:
        content["actions"][0]["rationale"] = rationale
    return httpx.Response(
        200,
        headers={"x-request-id": "req_test_success"},
        json={
            "id": "synthetic",
            "created": 0,
            "object": "chat.completion",
            "model": "openai/gpt-oss-120b",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": json.dumps(content)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
        },
    )


async def test_observed_failure_sequence_reuses_hypotheses_and_records_exact_causes(
    sandbox: Sandbox,
    investigation: dict,
    monkeypatch,
):
    tasks = []
    cited = []

    async def handle(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        task = payload["task"]
        tasks.append(task)
        if task == "hypotheses":
            return await success(body)
        if tasks.count("actions") == 1:
            result = await success(body, "The cause is definitely confirmed.")
            cited.extend(
                json.loads(result.json()["choices"][0]["message"]["content"])["actions"][0][
                    "evidence_ids"
                ]
            )
            return result
        assert payload["validation_feedback"]["condition"] == "assertive_claim"
        return httpx.Response(
            429,
            headers={"retry-after": "45", "x-request-id": "req_rate_limit"},
            json={"error": {"message": "private provider body"}},
        )

    worker = InvestigationWorker(
        sandbox.sessions, sandbox.settings, sdk_provider(monkeypatch, sandbox.settings, handle)
    )
    first = await worker.claim_one()
    assert await worker.process(first) == "retry_pending"
    async with sandbox.sessions() as session:
        row = await session.get(Investigation, first.investigation_id)
        assert row.checkpoint["completed_stages"] == list(STAGES[:5])
        step = await session.scalar(
            select(InvestigationStep).where(
                InvestigationStep.attempt == 1, InvestigationStep.stage == "recommendations"
            )
        )
        assert step.details["diagnostics"]["condition"] == "assertive_claim"
        assert step.details["diagnostics"]["field"] == "rationale"
        assert step.details["diagnostics"]["evidence_refs"] == sorted(cited)
        assert step.details["diagnostics"]["request_id"] == "req_test_success"
        assert row.actual_input_tokens == 200 and row.actual_output_tokens == 400
    for attempt in (2, 3):
        async with sandbox.engine.begin() as connection:
            await connection.execute(
                text("UPDATE investigation_jobs SET available_at=clock_timestamp()")
            )
        claim = await worker.claim_one()
        assert await worker.process(claim) == ("retry_pending" if attempt == 2 else "failed")
        async with sandbox.sessions() as session:
            job = await session.get(InvestigationJob, claim.job_id)
            step = await session.scalar(
                select(InvestigationStep).where(
                    InvestigationStep.attempt == attempt,
                    InvestigationStep.stage == "recommendations",
                )
            )
            details = step.details
            assert details["error_code"] == "provider_rate_limited"
            assert details["diagnostics"]["exception_class"] == "RateLimitError"
            assert details["diagnostics"]["http_status"] == 429
            assert details["diagnostics"]["request_id"] == "req_rate_limit"
            assert details["retry_delay_seconds"] == 45
            if attempt == 2:
                assert 44 <= (job.available_at - step.created_at).total_seconds() <= 47
            else:
                assert details["retry_blocked"] == "llm_budget_exhausted"
    assert tasks == ["hypotheses", "actions", "actions", "actions"]
    async with sandbox.sessions() as session:
        row = await session.get(Investigation, first.investigation_id)
        assert row.llm_calls == 4 and row.output_tokens_reserved == 8192
        assert row.actual_input_tokens == 200 and row.actual_output_tokens == 400
        assert await session.get(InvestigationReport, row.id) is None


async def test_negated_claim_completes_full_graph_with_real_sdk(
    sandbox: Sandbox,
    investigation: dict,
    monkeypatch,
):
    calls = []

    async def handle(request):
        body = json.loads(request.content)
        payload = json.loads(body["messages"][1]["content"])
        calls.append(payload["task"])
        return await success(
            body, "The root cause is not confirmed." if payload["task"] == "actions" else None
        )

    worker = InvestigationWorker(
        sandbox.sessions, sandbox.settings, sdk_provider(monkeypatch, sandbox.settings, handle)
    )
    claim = await worker.claim_one()
    assert await worker.process(claim) == "completed"
    assert calls == ["hypotheses", "actions"]
    async with sandbox.sessions() as session:
        assert await session.get(InvestigationReport, claim.investigation_id) is not None


@pytest.mark.parametrize("status", [400, 401, 429])
async def test_permanent_or_out_of_deadline_errors_stop_without_retry(
    sandbox: Sandbox,
    investigation: dict,
    monkeypatch,
    status: int,
):
    calls = []

    def handle(request):
        calls.append(1)
        return httpx.Response(
            status,
            headers={"retry-after": "300"},
            json={
                "error": {"message": "sensitive data"},
            },
        )

    worker = InvestigationWorker(
        sandbox.sessions, sandbox.settings, sdk_provider(monkeypatch, sandbox.settings, handle)
    )
    claim = await worker.claim_one()
    assert await worker.process(claim) == "failed"
    assert await worker.claim_one() is None
    assert len(calls) == 1
    async with sandbox.sessions() as session:
        job = await session.get(InvestigationJob, claim.job_id)
        assert job.attempts == 1 and job.status == "failed"


class TruncatedProvider(FakeProvider):
    async def generate(self, *args, **kwargs):
        return ProviderResponse("{", 100, 2048, finish_reason="length")


async def test_truncation_counts_usage_and_does_not_repeat_identical_cap(
    sandbox: Sandbox,
    investigation: dict,
):
    worker = InvestigationWorker(sandbox.sessions, sandbox.settings, TruncatedProvider())
    claim = await worker.claim_one()
    assert await worker.process(claim) == "failed"
    async with sandbox.sessions() as session:
        row = await session.get(Investigation, claim.investigation_id)
        assert row.actual_input_tokens == 100 and row.actual_output_tokens == 2048
        assert row.error_code == "incomplete_model_output" and row.llm_calls == 1
