"""GitHub App installation-token auth for tenant-project crew runs.

This is the "production seam" ``github/auth.py``'s docstring points at:
tenant projects (``Manifest.tenant_id`` set) authenticate to GitHub via a
short-lived **installation access token**, not the operator's own
``GITHUB_TOKEN``. Mirrors the Node implementation shipped in
``web/lib/github-app-config.ts`` / ``web/lib/github-app.ts`` — same two
Postgres tables (``platform_github_app``, ``tenant_github_installations``),
same JWT-then-installation-token exchange.

    App JWT (RS256, signed w/ the App's private key)
    -> POST /app/installations/{id}/access_tokens
    -> short-lived (60 min) installation token
"""

from __future__ import annotations

import base64
import json
import logging
import time

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from minions.db.connection import connect

logger = logging.getLogger(__name__)

GH_API = "https://api.github.com"

# installation_id -> (token, expires_at_epoch). Refreshed a little before the
# real ~60-minute TTL so a run never straddles expiry mid-call.
_token_cache: dict[int, tuple[str, float]] = {}

_TOKEN_REFRESH_MARGIN_S = 120


class GithubAppNotConfiguredError(RuntimeError):
    pass


class TenantInstallationMissingError(RuntimeError):
    pass


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _app_jwt(app_id: str, private_key_pem: str) -> str:
    """Mint a ~9-minute App JWT (RS256), matching web/lib/github-app.ts::appJwt."""
    key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
    if not isinstance(key, rsa.RSAPrivateKey):
        raise GithubAppNotConfiguredError(
            "platform_github_app.private_key is not an RSA key — GitHub Apps require RSA."
        )
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode())
    signing_input = f"{header}.{payload}".encode()
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def _load_app_credentials() -> tuple[str, str]:
    """(app_id, private_key_pem) from platform_github_app, or raise."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT app_id, private_key FROM platform_github_app ORDER BY created_at DESC LIMIT 1"
        )
        row = cur.fetchone()
    if row is None:
        raise GithubAppNotConfiguredError(
            "No GitHub App configured — set one up at /admin/github-app before running tenant crews."
        )
    return row[0], row[1]


def _load_installation_id(tenant_id: str) -> int:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT installation_id FROM tenant_github_installations "
            "WHERE tenant_id = %s ORDER BY created_at DESC LIMIT 1",
            (tenant_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise TenantInstallationMissingError(
            f"Tenant {tenant_id} has no GitHub App installation — they haven't finished "
            "the 'Connect your repositories' onboarding step."
        )
    return int(row[0])


def get_installation_token(tenant_id: str) -> str:
    """Short-lived GitHub token scoped to this tenant's installation.

    Cached in-process until close to its ~60-minute expiry.
    """
    installation_id = _load_installation_id(tenant_id)

    cached = _token_cache.get(installation_id)
    if cached is not None:
        token, expires_at = cached
        if time.time() < expires_at - _TOKEN_REFRESH_MARGIN_S:
            return token

    app_id, private_key_pem = _load_app_credentials()
    jwt = _app_jwt(app_id, private_key_pem)

    r = httpx.post(
        f"{GH_API}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {jwt}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=10.0,
    )
    r.raise_for_status()
    body = r.json()
    token = str(body["token"])
    # expires_at is an ISO-8601 string; approximate epoch via a fixed 55-minute
    # TTL rather than parsing it, matching GitHub's documented ~60-minute life.
    _token_cache[installation_id] = (token, time.time() + 55 * 60)
    return token
