"""Redaction helpers for interview answers and code scans."""

from __future__ import annotations

import re

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^'\"\s]{8,}"),
]


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: _redact_match(m.group(0)), redacted)
    return redacted


def _redact_match(value: str) -> str:
    if "=" in value:
        return value.split("=", 1)[0] + "=<redacted>"
    if ":" in value:
        return value.split(":", 1)[0] + ": <redacted>"
    return "<redacted-secret>"
