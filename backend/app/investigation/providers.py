import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Literal, Protocol

from groq import APIConnectionError, APIStatusError, APITimeoutError, AsyncGroq, RateLimitError
from pydantic import BaseModel

from app.core.config import Settings
from app.investigation.errors import InvestigationFailure
from app.investigation.safety import Sanitizer

Task = Literal["hypotheses", "actions"]


@dataclass(frozen=True)
class ProviderResponse:
    content: str
    input_tokens: int = 0
    output_tokens: int = 0
    diagnostics: dict = field(default_factory=dict)
    finish_reason: str = "stop"


def retry_delay(value: str | None) -> float | None:
    """Parse only Retry-After, with finite bounds; never persist raw headers."""
    if not value or len(value) > 100:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return min(86400, max(0, seconds)) if math.isfinite(seconds) else None


class Provider(Protocol):
    async def generate(
        self, task: Task, system: str, payload: dict, schema: type[BaseModel], max_tokens: int
    ) -> ProviderResponse: ...


def provider_available(provider: str, model: str, settings: Settings) -> bool:
    if provider == "fake":
        return settings.environment != "production"
    return bool(settings.groq_api_key and settings.groq_api_key.get_secret_value() and model)


class GroqProvider:
    def __init__(self, settings: Settings, model: str, timeout: int) -> None:
        if not provider_available("groq", model, settings):
            raise InvestigationFailure("provider_unavailable")
        self.settings, self.model, self.timeout = settings, model, timeout
        for name in ("groq", "groq._base_client", "httpx", "httpcore", "httpx2", "httpcore2"):
            logging.getLogger(name).setLevel(logging.WARNING)

    def metadata(self, headers, **values) -> dict:
        request_id = headers.get("x-request-id", "")
        sanitizer = Sanitizer.from_settings(self.settings)
        if (
            re.fullmatch(r"req_[A-Za-z0-9_-]{1,100}", request_id)
            and sanitizer.text(request_id, len(request_id)) == request_id
        ):
            values["request_id"] = request_id
        return values

    async def generate(
        self, task: Task, system: str, payload: dict, schema: type[BaseModel], max_tokens: int
    ) -> ProviderResponse:
        try:
            async with AsyncGroq(
                api_key=self.settings.groq_api_key.get_secret_value(),
                timeout=self.timeout,
                max_retries=0,
                base_url="https://api.groq.com",
            ) as client:
                # GPT-OSS supports constrained schema decoding. Other configurable
                # models retain JSON mode. Neither replaces local grounding checks.
                options = {}
                response_format = {"type": "json_object"}
                if self.model in {"openai/gpt-oss-120b", "openai/gpt-oss-20b"}:
                    response_format = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": schema.__name__,
                            "strict": True,
                            "schema": schema.model_json_schema(),
                        },
                    }
                    options = {"reasoning_effort": "low", "include_reasoning": False}
                raw = await client.chat.completions.with_raw_response.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": json.dumps(payload, ensure_ascii=True)},
                    ],
                    response_format=response_format,
                    tool_choice="none",
                    max_completion_tokens=max_tokens,
                    temperature=0,
                    **options,
                )
                response = await raw.parse()
                metadata = self.metadata(raw.headers, http_status=raw.status_code)
            if not response.choices:
                raise InvestigationFailure(
                    "incomplete_model_output",
                    retryable=True,
                    diagnostics=metadata | {"finish_reason": "missing"},
                )
            content = response.choices[0].message.content or ""
            if len(content.encode("utf-8")) > 16384:
                raise InvestigationFailure("output_size_exceeded")
            usage = response.usage
            return ProviderResponse(
                content,
                usage.prompt_tokens if usage else 0,
                usage.completion_tokens if usage else 0,
                metadata,
                response.choices[0].finish_reason,
            )
        except APITimeoutError:
            raise InvestigationFailure(
                "provider_timeout",
                retryable=True,
                diagnostics={"exception_class": "APITimeoutError"},
            ) from None
        except APIConnectionError as exc:
            cause = type(exc.__cause__).__name__
            raise InvestigationFailure(
                "provider_connection_error",
                retryable=True,
                diagnostics={
                    "exception_class": "APIConnectionError",
                    "connection_cause": cause
                    if cause in {"ConnectError", "ReadError", "WriteError", "RemoteProtocolError"}
                    else "unknown",
                },
            ) from None
        except APIStatusError as exc:
            status = exc.status_code
            diagnostics = self.metadata(
                exc.response.headers, exception_class=type(exc).__name__, http_status=status
            )
            retryable = status in {408, 409, 429, 498} or status >= 500
            code = {
                401: "provider_authentication_failed",
                403: "provider_permission_denied",
                404: "provider_model_not_found",
                413: "provider_request_too_large",
                429: "provider_rate_limited",
            }.get(status, "provider_request_failed")
            if isinstance(exc, RateLimitError):
                # Groq can return 429 for a single prompt larger than the TPM
                # allowance. Waiting cannot make the same request fit.
                body = exc.body if isinstance(exc.body, dict) else {}
                error = body.get("error", body)
                if isinstance(error, dict):
                    if str(error.get("message", "")).lower().startswith("request too large"):
                        code, retryable = "provider_request_too_large", False
                    elif isinstance(error.get("code"), str) and error["code"] in {
                        "insufficient_quota",
                        "billing_hard_limit_reached",
                    }:
                        code, retryable = "provider_quota_exhausted", False
            delay = retry_delay(exc.response.headers.get("retry-after")) if retryable else None
            if status == 429 and retryable and delay is None:
                delay = 30
            raise InvestigationFailure(
                code, retryable=retryable, diagnostics=diagnostics, retry_after=delay
            ) from None


class FakeProvider:
    """Explicit deterministic development/test adapter, never presented as hosted AI."""

    async def generate(
        self, task: Task, system: str, payload: dict, schema: type[BaseModel], max_tokens: int
    ) -> ProviderResponse:
        logs = [
            item
            for item in payload["untrusted_evidence"]
            if item["source_type"] == "log" and not item["quarantined"]
        ]
        supporting = [item["reference"] for item in logs[:2]]
        if not supporting:
            supporting = [payload["untrusted_evidence"][0]["reference"]]
        knowledge = [
            item["reference"]
            for item in payload["untrusted_evidence"]
            if item["source_type"] == "runbook_chunk" and not item["quarantined"]
        ]
        refs = supporting + knowledge[:1]
        codes = set(payload.get("aggregations", {}).get("error_codes", {}))
        if "DB_CONNECTION_TIMEOUT" in codes:
            title, explanation, check = (
                "Possible database connection pressure",
                "Connection timeout evidence may indicate pool saturation or unava"
                "ilable database capacity.",
                "Inspect active connection counts, pool sizing and potential conne"
                "ction leaks without changing configuration.",
            )
        elif "LATENCY_BREACH" in codes:
            title, explanation, check = (
                "Possible dependency or query latency",
                "The latency breach events suggest a slow dependency or expensive "
                "request path; no percentile is established.",
                "Inspect request traces and query durations using approved read-on"
                "ly observability views.",
            )
        else:
            title, explanation, check = (
                "Possible backend dependency failure",
                "The selected error records may reflect a dependency failure or an"
                " application exception.",
                "Inspect correlated request traces and backend exception logs with"
                "out changing runtime state.",
            )
        if task == "hypotheses":
            content = {
                "hypotheses": [
                    {
                        "title": title,
                        "explanation": explanation,
                        "evidence_ids": refs,
                        "missing_or_contradicting_evidence": [
                            "Dependency health, capacity and full traces are not available."
                        ],
                        "confidence": "low",
                        "verification_step": check,
                    },
                    {
                        "title": "Possible independent application or network fault",
                        "explanation": (
                            "A different application path or network interruption could "
                            "produce similar symptoms; alternatives remain open."
                        ),
                        "evidence_ids": supporting,
                        "missing_or_contradicting_evidence": [
                            "A causal sequence has not been verified."
                        ],
                        "confidence": "low",
                        "verification_step": (
                            "Inspect correlated traces and timing before selecting a cause."
                        ),
                    },
                ]
            }
        else:
            content = {
                "actions": [
                    {
                        "description": check,
                        "rationale": (
                            "Read-only inspection may distinguish the unverified alternatives."
                        ),
                        "evidence_ids": refs,
                        "risk": "read_only",
                        "requires_human_review": True,
                    }
                ]
            }
        return ProviderResponse(json.dumps(content), 0, 0)


def build_provider(provider: str, model: str, settings: Settings, timeout: int) -> Provider:
    if not provider_available(provider, model, settings):
        raise InvestigationFailure("provider_unavailable")
    return FakeProvider() if provider == "fake" else GroqProvider(settings, model, timeout)
