"""Resolve a tenant's real email address via Clerk's Admin API.

Used so tenant crew runs can send approval emails to the actual customer
instead of the founder. Requires ``CLERK_SECRET_KEY`` (the same secret the
web app uses server-side) — if unset, or the lookup fails for any reason,
callers should fall back to ``manifest.owner`` rather than blocking a run.
"""

from __future__ import annotations

import logging
import os

import httpx

from minions.db.connection import connect

logger = logging.getLogger(__name__)

CLERK_API = "https://api.clerk.com/v1"

# tenant_id -> email. Small in-process cache; a tenant's email rarely
# changes mid-run and this avoids a Clerk API round-trip per decision.
_email_cache: dict[str, str] = {}


def _load_clerk_user_id(tenant_id: str) -> str | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT clerk_user_id FROM tenants WHERE tenant_id = %s", (tenant_id,))
        row = cur.fetchone()
    return row[0] if row else None


def get_tenant_email(tenant_id: str) -> str | None:
    """The tenant's primary email address, or None if unresolvable."""
    if tenant_id in _email_cache:
        return _email_cache[tenant_id]

    secret_key = os.environ.get("CLERK_SECRET_KEY")
    if not secret_key:
        logger.debug("CLERK_SECRET_KEY not set; cannot resolve tenant email")
        return None

    clerk_user_id = _load_clerk_user_id(tenant_id)
    if clerk_user_id is None:
        logger.debug("no tenants row for tenant_id=%s", tenant_id)
        return None

    try:
        r = httpx.get(
            f"{CLERK_API}/users/{clerk_user_id}",
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=10.0,
        )
        r.raise_for_status()
        body = r.json()
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Clerk user lookup failed for tenant %s: %s", tenant_id, e)
        return None

    addresses = body.get("email_addresses") or []
    primary_id = body.get("primary_email_address_id")
    email = next(
        (a["email_address"] for a in addresses if a.get("id") == primary_id),
        addresses[0]["email_address"] if addresses else None,
    )
    if email:
        _email_cache[tenant_id] = email
    return email
