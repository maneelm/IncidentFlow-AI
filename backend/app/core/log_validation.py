"""Bound JSON metadata and reject credential-shaped fields without echoing their values."""

import json
import re
from typing import Any

SENSITIVE_KEY = re.compile(
    r"password|passwd|pwd|secret|token|authorization|cookie|credential|apikey"
)
SENSITIVE_VALUE = re.compile(
    r"(?i)\bbearer\s+\S+|\biflow_[0-9a-f]{32}\.[A-Za-z0-9_-]+|"
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
)


def validate_metadata(value: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 8192:
        raise ValueError("Log metadata exceeds 8192 bytes")
    nodes = 0

    def walk(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > 256 or depth > 5:
            raise ValueError("Log metadata exceeds its depth or node limit")
        if isinstance(item, dict):
            for key, child in item.items():
                if "\x00" in key:
                    raise ValueError("NUL characters are forbidden in log metadata")
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if SENSITIVE_KEY.search(normalized):
                    raise ValueError("Credential fields are forbidden in log metadata")
                walk(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1)
        elif isinstance(item, str):
            if "\x00" in item:
                raise ValueError("NUL characters are forbidden in log metadata")
            if SENSITIVE_VALUE.search(item):
                raise ValueError("Credential material is forbidden in log metadata")

    walk(value, 0)
    return value
