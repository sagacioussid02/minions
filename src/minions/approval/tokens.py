"""HMAC-signed approval tokens for magic-link emails.

Token format: ``<payload_b64>.<sig_b64>`` (URL-safe base64 without padding).
Payload is JSON ``{"id": <decision_id>, "action": "approve"|"reject", "exp": <unix_ts>}``.

The signing key is resolved via :func:`minions.secrets.get_token_signing_key`,
which checks env var ``MINIONS_TOKEN_SECRET`` first, then a configured
secret backend (AWS Secrets Manager when wired). Production deployments
MUST set a real signing key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from uuid import UUID

from minions.secrets import get_token_signing_key

DEFAULT_TTL_SECONDS = 72 * 60 * 60  # 72 hours, matches the spec's auto-reject window


class TokenError(ValueError):
    """Raised when a token is malformed, has a bad signature, or is expired."""


def _key() -> bytes:
    return get_token_signing_key().encode("utf-8")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def sign(decision_id: UUID, action: str, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> str:
    """Produce a signed token for an approval action ('approve' or 'reject')."""
    if action not in {"approve", "reject"}:
        raise ValueError(f"action must be 'approve' or 'reject', got {action!r}")
    payload = {"id": str(decision_id), "action": action, "exp": int(time.time()) + ttl_seconds}
    payload_b64 = _b64encode(json.dumps(payload, sort_keys=True).encode("utf-8"))
    sig = hmac.new(_key(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64encode(sig)}"


def verify(token: str) -> dict[str, Any]:
    """Verify a signed token; returns the payload dict if valid."""
    try:
        payload_b64, sig_b64 = token.split(".")
    except ValueError as e:
        raise TokenError("malformed token") from e

    expected = hmac.new(_key(), payload_b64.encode("ascii"), hashlib.sha256).digest()
    expected_b64 = _b64encode(expected)
    if not hmac.compare_digest(sig_b64, expected_b64):
        raise TokenError("bad signature")

    try:
        payload = json.loads(_b64decode(payload_b64))
    except (ValueError, json.JSONDecodeError) as e:
        raise TokenError("malformed payload") from e

    if int(payload.get("exp", 0)) < int(time.time()):
        raise TokenError("token expired")

    return payload
