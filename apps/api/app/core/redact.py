"""Redact secrets from structured data before it is logged or exported."""

from __future__ import annotations

import re

SENSITIVE_PARTS = (
    "password",
    "api_key",
    "apikey",
    "token",
    "secret",
    "authorization",
    "credential",
    "cookie",
    "session",
    "private_key",
)

SECRET_VALUE = re.compile(
    r"(?i)\b(api[_-]?key|secret|password|token|authorization|app[_-]?password)\b(\s*[:=]\s*)(['\"]?)([^\s'\"]{4,})(\3)"
)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in SENSITIVE_PARTS)


def redact(value):
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if is_sensitive_key(str(key)):
                cleaned[key] = "[redacted]"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return SECRET_VALUE.sub(r"\1\2\3[redacted]\5", value)
    return value
