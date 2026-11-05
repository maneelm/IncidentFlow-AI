import json
from secrets import token_urlsafe

import groq
import httpx
import pytest

from app.core.config import Settings
from app.investigation import providers
from app.investigation.errors import InvestigationFailure
from app.investigation.providers import GroqProvider
from app.schemas.investigations import HypothesisOutput


@pytest.mark.parametrize(
    "status,expected",
    [
        (200, None),
        (401, "provider_authentication_failed"),
        (429, "provider_rate_limited"),
        (500, "provider_request_failed"),
    ],
)
async def test_real_groq_sdk_transport_without_network(
    monkeypatch: pytest.MonkeyPatch, status: int, expected: str | None
) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://test@localhost/test",
        jwt_secret=token_urlsafe(48),
        groq_api_key="gsk_synthetic_adapter_key_12345",
    )
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "configured-model" and body["max_completion_tokens"] == 1000
        assert body["response_format"] == {"type": "json_object"}
        if status != 200:
            return httpx.Response(
                status, json={"error": {"message": "not retained", "type": "invalid_request_error"}}
            )
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "created": 0,
                "object": "chat.completion",
                "model": "configured-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "{}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
            },
        )

    actual = groq.AsyncGroq

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0 and kwargs["timeout"] == 5
        return actual(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        )

    monkeypatch.setattr(providers, "AsyncGroq", factory)
    adapter = GroqProvider(settings, "configured-model", 5)
    if expected:
        with pytest.raises(InvestigationFailure, match=expected) as failure:
            await adapter.generate("hypotheses", "JSON instructions", {}, HypothesisOutput, 1000)
        assert failure.value.diagnostics["http_status"] == status
        assert failure.value.retryable == (status in {429, 500})
    else:
        response = await adapter.generate(
            "hypotheses", "JSON instructions", {}, HypothesisOutput, 1000
        )
        assert response.input_tokens == 10 and response.output_tokens == 20
    assert len(requests) == 1
