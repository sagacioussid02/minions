"""Cheap Anthropic API key validation — `GET /v1/models` round-trip.

Called as a preflight before any ``--no-dry-run`` crew kickoff so a bad key
fails fast (one HTTP call, < 200ms) instead of bouncing through CrewAI's
retry-on-401 loop.
"""

from __future__ import annotations

import httpx

ANTHROPIC_BASE = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_TIMEOUT = 10.0


def auth_check(api_key: str, *, timeout: float = DEFAULT_TIMEOUT) -> tuple[bool, str]:
    """Verify an Anthropic API key by hitting ``/v1/models``.

    Returns ``(ok, message)``. Cheap (no model invocation, no token cost).
    """
    if not api_key:
        return (False, "empty api key")
    try:
        r = httpx.get(
            f"{ANTHROPIC_BASE}/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION},
            timeout=timeout,
        )
    except httpx.RequestError as e:
        return (False, f"network error: {e}")
    if r.status_code == 200:
        return (True, "authenticated")
    if r.status_code == 401:
        return (
            False,
            "401 invalid x-api-key — generate a new key at "
            "https://console.anthropic.com/settings/keys and update your .env",
        )
    if r.status_code == 403:
        return (
            False,
            "403 forbidden — the key is valid but the workspace lacks access "
            "(billing / model permissions). Check console.anthropic.com.",
        )
    if r.status_code == 429:
        return (False, "429 rate-limited — workspace is over quota right now")
    return (False, f"unexpected status {r.status_code}: {r.text[:200]}")
