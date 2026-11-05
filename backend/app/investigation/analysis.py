import json
import re
from collections import Counter

from pydantic import ValidationError

from app.investigation.errors import InvestigationFailure
from app.investigation.safety import INJECTION, Sanitizer
from app.schemas.investigations import ActionOutput, HypothesisOutput

ASSERTIVE = re.compile(
    r"\b(?:confirmed|definitely|proven|certainly)\b|root cause is|\d+(?:\.\d+)?\s*%", re.I
)
MUTATION = re.compile(
    r"\b(?:drop|truncate|delete|restart|reboot|kill|terminate|alter|update|insert|deploy|scale|kubectl|curl|wget|sudo)\b|\brm\s+-|```",
    re.I,
)
MEASUREMENT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:milliseconds|ms|seconds|connections|requests)\b", re.I
)
UUID_TEXT = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
NEGATED_CERTAINTY = re.compile(
    r"\b(?:not|never|cannot|can't)\s+"
    r"(?:(?:yet|been|be|fully|independently|definitively)\s+){0,3}"
    r"(?:confirmed|proven)\b|\b(?:unconfirmed|unproven)\b",
    re.I,
)
UNCERTAIN_CAUSE = re.compile(
    r"\broot cause is\s+(?:(?:still|currently)\s+)?"
    r"(?:not\s+(?:(?:yet|fully)\s+)?(?:confirmed|proven|known|established|determined)"
    r"|unknown|unconfirmed|unproven|uncertain|unverified)\b",
    re.I,
)


def assertive_claim(text: str) -> bool:
    # Ignore only the matched negated certainty phrase, not its surrounding
    # sentence. A subsequent affirmative claim must still fail.
    text = UNCERTAIN_CAUSE.sub("uncertain", text)
    return bool(ASSERTIVE.search(NEGATED_CERTAINTY.sub("uncertain", text)))


def aggregate(evidence: list[dict]) -> dict:
    logs = [item for item in evidence if item["source_type"] == "log" and not item["quarantined"]]
    return {
        "selected_logs": len(logs),
        "levels": dict(sorted(Counter(item["provenance"]["level"] for item in logs).items())),
        "error_codes": dict(
            sorted(
                Counter(
                    item["provenance"]["error_code"]
                    for item in logs
                    if item["provenance"].get("error_code")
                ).items()
            )
        ),
        "trace_ids": dict(
            sorted(
                Counter(
                    item["provenance"]["trace_id"]
                    for item in logs
                    if item["provenance"].get("trace_id")
                ).items()
            )
        ),
        "affected_services": len({item["provenance"]["service_id"] for item in logs}),
        "first_source_timestamp": min(
            (item["provenance"]["source_timestamp"] for item in logs), default=None
        ),
        "last_source_timestamp": max(
            (item["provenance"]["source_timestamp"] for item in logs), default=None
        ),
        "quarantined_sources": sum(item["quarantined"] for item in evidence),
        "sufficient": any(
            item["provenance"]["level"] in {"warning", "error", "critical"} for item in logs
        ),
    }


def validate_generation(
    output: HypothesisOutput | ActionOutput, evidence: list[dict], sanitizer: Sanitizer
) -> None:
    allowed = {item["reference"] for item in evidence if not item["quarantined"]}
    known_ids = {value.lower() for value in UUID_TEXT.findall(json.dumps(evidence))}
    items = output.hypotheses if isinstance(output, HypothesisOutput) else output.actions
    for index, item in enumerate(items):

        def reject(code: str, condition: str, field: str, *, item=item, index=index) -> None:
            raise InvestigationFailure(
                code,
                retryable=True,
                diagnostics={
                    "condition": condition,
                    "collection": "hypotheses"
                    if isinstance(output, HypothesisOutput)
                    else "actions",
                    "item_index": index,
                    "field": field,
                    # Only persisted references, never arbitrary strings from the model.
                    "evidence_refs": sorted(set(item.evidence_ids) & allowed),
                    "invalid_reference_count": len(set(item.evidence_ids) - allowed),
                },
            )

        if not set(item.evidence_ids) <= allowed:
            reject("invalid_evidence_reference", "citation_membership", "evidence_ids")
        for key, value in item.model_dump().items():
            if key == "evidence_ids":
                continue
            texts = value if isinstance(value, list) else [value]
            for text in texts:
                if not isinstance(text, str):
                    continue
                if not {value.lower() for value in UUID_TEXT.findall(text)} <= known_ids:
                    reject("unsupported_model_claim", "unknown_uuid", key)
                for condition, failed in (
                    ("assertive_claim", assertive_claim(text)),
                    ("numerical_measurement", bool(MEASUREMENT.search(text))),
                    ("instruction_like_output", bool(INJECTION.search(text))),
                    ("output_requires_redaction", sanitizer.text(text, len(text)) != text),
                ):
                    if failed:
                        reject("unsupported_model_claim", condition, key)
                if isinstance(output, ActionOutput) and MUTATION.search(text):
                    reject("unsafe_recommendation", "mutating_recommendation", key)


def parse_output(schema: type, content: str):
    try:
        return schema.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        # Pydantic errors can include raw input and attacker-controlled extra keys.
        # Retain only the count, not errors(), repr() or validation input.
        raise InvestigationFailure(
            "invalid_structured_output",
            retryable=True,
            diagnostics={"validation_error_count": exc.error_count()}
            if isinstance(exc, ValidationError)
            else {},
        ) from None
