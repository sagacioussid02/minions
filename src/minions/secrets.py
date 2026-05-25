"""Secret resolver — env vars first (local dev), then AWS Secrets Manager.

Agents themselves NEVER call this. Secrets are resolved by the orchestrator
process and either (a) used to build a client (e.g., the LLM) or (b) injected
as scoped tool inputs. Filesystem-level deny-lists prevent agents from
reading .env files directly.

## Backend chain

Each call to :func:`get_secret` walks a list of backends in order. The first
backend that returns a non-None value wins. The resolver memoizes successful
reads in-process to avoid round-tripping AWS Secrets Manager on every model
call. Tests should call :func:`reset` between cases (an autouse fixture in
tests/conftest.py does this).

Default chain (production):
  1. EnvBackend — reads ``MINIONS_SECRET_<NAME_UPPER>`` env vars.
  2. AwsSecretsManagerBackend — reads ``minions/<name>`` from AWS SM.

The AWS backend silently returns None (rather than raising) when:
  - boto3 isn't importable
  - AWS credentials aren't configured
  - The region can't be determined
  - The secret doesn't exist
These are all "this backend doesn't have it" cases, allowing the chain to
fall through cleanly. Other errors (e.g., AccessDenied, KMS decryption
failures) propagate so misconfigurations get noticed.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class SecretNotFound(RuntimeError):
    """Raised when a requested secret cannot be located in any backend."""


def _env_name(name: str) -> str:
    return f"MINIONS_SECRET_{name.upper().replace('-', '_').replace('/', '_')}"


# Error codes (and class names) we treat as "this backend doesn't have it" and
# silently fall through. AccessDenied and KMS decryption failures are NOT in
# this set — those are real misconfigurations the operator must see.
_BACKEND_UNAVAILABLE_CODES = frozenset(
    {
        "ResourceNotFoundException",
        "NoCredentialsError",
        "EndpointConnectionError",
        "ProfileNotFound",
        "PartialCredentialsError",
        "NoRegionError",
    }
)


class SecretBackend(Protocol):
    """A backend that can produce a secret value by name, or return None."""

    def get(self, name: str) -> str | None: ...

    @property
    def label(self) -> str: ...


class EnvBackend:
    """Reads ``MINIONS_SECRET_<NAME>`` from environment variables."""

    label = "env"

    def get(self, name: str) -> str | None:
        return os.environ.get(_env_name(name))


class AwsSecretsManagerBackend:
    """Reads secrets from AWS Secrets Manager, named ``<prefix>/<name>``.

    Construction errors (boto3 missing, no credentials, no region) are deferred
    to first use and treated as "backend unusable" so dev environments without
    AWS work fine.
    """

    label = "aws_secrets_manager"

    def __init__(
        self,
        *,
        prefix: str = "minions",
        region_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.prefix = prefix
        self.region_name = region_name
        self._client = client
        self._client_init_failed = False

    def _get_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if self._client_init_failed:
            return None
        try:
            import boto3  # type: ignore[import-not-found]

            kwargs: dict[str, Any] = {}
            if self.region_name:
                kwargs["region_name"] = self.region_name
            self._client = boto3.client("secretsmanager", **kwargs)
            return self._client
        except Exception as e:
            code = type(e).__name__
            logger.debug("AwsSecretsManagerBackend disabled (%s): %s", code, e)
            self._client_init_failed = True
            return None

    def get(self, name: str) -> str | None:
        client = self._get_client()
        if client is None:
            return None
        secret_id = f"{self.prefix}/{name}"
        try:
            response = client.get_secret_value(SecretId=secret_id)
        except Exception as e:
            code = _aws_error_code(e)
            if code in _BACKEND_UNAVAILABLE_CODES:
                logger.debug("Secret '%s' not retrievable from AWS SM: %s", secret_id, code)
                return None
            raise
        # SecretString is the common case; SecretBinary is rare for our use.
        return response.get("SecretString")


def _aws_error_code(exc: BaseException) -> str:
    """Extract the AWS error code from a botocore ClientError or fall back to class name."""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        err = response.get("Error", {})
        if isinstance(err, dict):
            code = err.get("Code")
            if isinstance(code, str):
                return code
    return type(exc).__name__


class ChainResolver:
    """Tries each backend in order until one returns a non-None value.

    Caches successful reads in-process (cleared via :meth:`invalidate`).
    Tests reset the module-level resolver between cases (see conftest.py).
    """

    def __init__(self, backends: list[SecretBackend]) -> None:
        self.backends = backends
        self._cache: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        if name in self._cache:
            return self._cache[name]
        for backend in self.backends:
            val = backend.get(name)
            if val is not None:
                self._cache[name] = val
                return val
        return None

    def invalidate(self, name: str | None = None) -> None:
        if name is None:
            self._cache.clear()
        else:
            self._cache.pop(name, None)


# ---- Module-level singleton ----

_resolver: ChainResolver | None = None


def _default_resolver() -> ChainResolver:
    global _resolver
    if _resolver is None:
        _resolver = ChainResolver([EnvBackend(), AwsSecretsManagerBackend()])
    return _resolver


def configure(backends: list[SecretBackend]) -> None:
    """Replace the default resolver with a custom backend chain. Used in tests."""
    global _resolver
    _resolver = ChainResolver(backends)


def reset() -> None:
    """Reset to the default backend chain (env + AWS SM). Used in tests for isolation."""
    global _resolver
    _resolver = None


def list_backends() -> list[str]:
    """Return the labels of the active backend chain (for diagnostics)."""
    return [b.label for b in _default_resolver().backends]


def get_secret(name: str) -> str:
    val = _default_resolver().get(name)
    if val is None:
        raise SecretNotFound(
            f"secret '{name}' not found. "
            f"Tried env var {_env_name(name)} and AWS Secrets Manager (key 'minions/{name}'). "
            f"For dev: export {_env_name(name)}=...; for production wire AWS Secrets Manager."
        )
    return val


def get_anthropic_api_key() -> str:
    """Resolve the Anthropic API key.

    Order: ``ANTHROPIC_API_KEY`` env var (standard) → secret 'anthropic-api-key'.
    """
    direct = os.environ.get("ANTHROPIC_API_KEY")
    if direct:
        return direct
    return get_secret("anthropic-api-key")


def get_vercel_token(project: str | None = None) -> str:
    """Resolve a Vercel API token, optionally per-project.

    Order:
    1. ``MINIONS_SECRET_VERCEL_TOKEN_<PROJECT_UPPER>`` env var (project override)
    2. ``MINIONS_SECRET_VERCEL_TOKEN`` env var (global)
    3. ``VERCEL_TOKEN`` env var (developer convention)
    4. Secret backends: ``minions/vercel-token-<project>`` then
       ``minions/vercel-token``
    """
    if project:
        scoped = os.environ.get(
            f"MINIONS_SECRET_VERCEL_TOKEN_{project.upper().replace('-', '_')}"
        )
        if scoped:
            return scoped
    for env_name in ("MINIONS_SECRET_VERCEL_TOKEN", "VERCEL_TOKEN"):
        v = os.environ.get(env_name)
        if v:
            return v
    if project:
        try:
            return get_secret(f"vercel-token-{project}")
        except SecretNotFound:
            pass
    return get_secret("vercel-token")


def get_token_signing_key() -> str:
    """Resolve the HMAC key used to sign approval magic-link tokens.

    Order: ``MINIONS_TOKEN_SECRET`` env var → secret 'token-signing-key' →
    dev-only fallback (with a warning log).

    The fallback catches *all* exceptions (not just SecretNotFound) because
    token signing is non-critical in v0 demos — the tokens are for preview
    only and don't gate any real action. A misconfigured AWS Secrets Manager
    (e.g., AccessDenied) shouldn't crash the planning flow. Production
    deploys MUST set MINIONS_TOKEN_SECRET or grant the role IAM access to
    the 'minions/token-signing-key' secret.
    """
    direct = os.environ.get("MINIONS_TOKEN_SECRET")
    if direct:
        return direct
    try:
        return get_secret("token-signing-key")
    except Exception as e:
        logger.warning(
            "token-signing-key not retrievable (%s: %s) — falling back to dev-only key. "
            "Set MINIONS_TOKEN_SECRET or grant IAM access to 'minions/token-signing-key'.",
            type(e).__name__,
            e,
        )
        return "dev-only-do-not-use-in-prod"
