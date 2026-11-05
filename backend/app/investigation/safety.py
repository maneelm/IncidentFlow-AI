import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.core.log_validation import SENSITIVE_KEY, SENSITIVE_VALUE

INJECTION = re.compile(
    r"ignore.{0,60}(?:instructions|previous|system)|(?:reveal|print|expose).{0,40}(?:secret|api.?key)|"
    r"system prompt|developer message|act as (?:an? )?(?:admin|system)",
    re.I | re.S,
)
ASSIGNMENT = re.compile(
    r"""(?i)\b(password|passwd|api[_-]?key|access[_-]?token|secret)\b["']?\s*[=:]\s*(?:"[^"]*"|'[^']*'|[^\s,;"}]+)"""
)
AUTH_HEADER = re.compile(r"(?i)\bauthorization[\"']?\s*[:=]\s*(?:bearer\s+|basic\s+)?[^\s,;\"}]+")
TOKEN = re.compile(r"\b(?:gsk_|sk-)[A-Za-z0-9_-]{12,}", re.I)
USERINFO = re.compile(r"([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@", re.I)


class Sanitizer:
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self.secrets = sorted(
            {value for value in secrets if len(value) >= 6}, key=len, reverse=True
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "Sanitizer":
        values = [settings.jwt_secret.get_secret_value(), str(settings.database_url)]
        if settings.groq_api_key:
            values.append(settings.groq_api_key.get_secret_value())
        if password := make_url(str(settings.database_url)).password:
            values.append(password)
        return cls(values)

    def text(self, value: str, limit: int = 600, *, quarantine: bool = False) -> str:
        value = value.replace("\x00", "")
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = AUTH_HEADER.sub("authorization=[REDACTED]", value)
        value = ASSIGNMENT.sub(lambda match: f"{match[1]}=[REDACTED]", value)
        value = SENSITIVE_VALUE.sub("[REDACTED]", value)
        value = TOKEN.sub("[REDACTED]", value)
        value = USERINFO.sub(r"\1[REDACTED]@", value)
        value = re.sub(r"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]", "", value)
        if quarantine and INJECTION.search(value):
            return "[QUARANTINED: instruction-like source content]"
        return value[:limit]

    def data(self, value: Any, depth: int = 0) -> Any:
        if depth > 5:
            return "[TRUNCATED]"
        if isinstance(value, dict):
            return {
                str(key)[:80]: "[REDACTED]"
                if SENSITIVE_KEY.search(re.sub(r"[^a-z0-9]", "", str(key).lower()))
                else self.data(child, depth + 1)
                for key, child in list(value.items())[:40]
            }
        if isinstance(value, list):
            return [self.data(child, depth + 1) for child in value[:50]]
        if isinstance(value, str):
            return self.text(value, 600, quarantine=True)
        if isinstance(value, (int, float, bool)) or value is None:
            return value
        return self.text(str(value), 100)
