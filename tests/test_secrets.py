"""Tests for the secret resolver — backends, chain, caching, AWS SM mock."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from minions import secrets
from minions.secrets import (
    AwsSecretsManagerBackend,
    ChainResolver,
    EnvBackend,
    SecretNotFound,
    get_anthropic_api_key,
    get_secret,
    get_token_signing_key,
)


# ---------- EnvBackend ----------


def test_env_backend_reads_env(monkeypatch):
    monkeypatch.setenv("MINIONS_SECRET_FOO_BAR", "value-123")
    backend = EnvBackend()
    assert backend.get("foo-bar") == "value-123"


def test_env_backend_missing_returns_none(monkeypatch):
    monkeypatch.delenv("MINIONS_SECRET_NOT_SET", raising=False)
    assert EnvBackend().get("not-set") is None


def test_env_backend_normalizes_name(monkeypatch):
    # Hyphens and slashes are normalized to underscores; case is upper.
    monkeypatch.setenv("MINIONS_SECRET_GITHUB_APP_PRIVATE_KEY", "k")
    assert EnvBackend().get("github-app/private-key") == "k"


# ---------- AwsSecretsManagerBackend ----------


def test_aws_backend_returns_secret_string():
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "the-value"}
    backend = AwsSecretsManagerBackend(client=mock_client)
    assert backend.get("anthropic-api-key") == "the-value"
    mock_client.get_secret_value.assert_called_once_with(SecretId="minions/anthropic-api-key")


def test_aws_backend_uses_custom_prefix():
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "v"}
    backend = AwsSecretsManagerBackend(client=mock_client, prefix="other")
    backend.get("foo")
    mock_client.get_secret_value.assert_called_once_with(SecretId="other/foo")


def test_aws_backend_resource_not_found_returns_none():
    mock_client = MagicMock()

    class _ClientError(Exception):
        def __init__(self) -> None:
            self.response = {"Error": {"Code": "ResourceNotFoundException"}}

    mock_client.get_secret_value.side_effect = _ClientError()
    backend = AwsSecretsManagerBackend(client=mock_client)
    assert backend.get("missing") is None


def test_aws_backend_no_credentials_returns_none():
    mock_client = MagicMock()

    class NoCredentialsError(Exception):
        pass

    mock_client.get_secret_value.side_effect = NoCredentialsError()
    backend = AwsSecretsManagerBackend(client=mock_client)
    assert backend.get("any") is None


def test_aws_backend_access_denied_propagates():
    mock_client = MagicMock()

    class _ClientError(Exception):
        def __init__(self) -> None:
            self.response = {"Error": {"Code": "AccessDeniedException"}}

    mock_client.get_secret_value.side_effect = _ClientError()
    backend = AwsSecretsManagerBackend(client=mock_client)
    # AccessDenied is a real misconfiguration the operator should see.
    with pytest.raises(Exception):
        backend.get("anything")


def test_aws_backend_construction_failure_returns_none(monkeypatch):
    """If boto3 itself fails (no region, no creds at construction), the backend silently disables."""
    backend = AwsSecretsManagerBackend()  # no client; will try real boto3

    def _raise(name, *a, **kw):
        raise RuntimeError("simulated boto3 init failure")

    # Monkey-patch the import inside the function path.
    import boto3

    monkeypatch.setattr(boto3, "client", _raise)
    assert backend.get("any") is None
    # Subsequent calls don't retry init:
    assert backend.get("any") is None


# ---------- ChainResolver ----------


class _StaticBackend:
    label = "static"

    def __init__(self, data: dict[str, str]) -> None:
        self.data = data
        self.calls = 0

    def get(self, name: str) -> str | None:
        self.calls += 1
        return self.data.get(name)


def test_chain_resolver_first_backend_wins():
    a = _StaticBackend({"k": "from-a"})
    b = _StaticBackend({"k": "from-b"})
    resolver = ChainResolver([a, b])
    assert resolver.get("k") == "from-a"
    assert b.calls == 0


def test_chain_resolver_falls_through():
    a = _StaticBackend({})
    b = _StaticBackend({"k": "from-b"})
    resolver = ChainResolver([a, b])
    assert resolver.get("k") == "from-b"
    assert a.calls == 1
    assert b.calls == 1


def test_chain_resolver_returns_none_when_all_miss():
    resolver = ChainResolver([_StaticBackend({}), _StaticBackend({})])
    assert resolver.get("nope") is None


def test_chain_resolver_caches():
    a = _StaticBackend({"k": "v"})
    resolver = ChainResolver([a])
    assert resolver.get("k") == "v"
    assert resolver.get("k") == "v"
    assert a.calls == 1  # second call hit the cache


def test_chain_resolver_invalidate():
    a = _StaticBackend({"k": "v"})
    resolver = ChainResolver([a])
    resolver.get("k")
    resolver.invalidate("k")
    resolver.get("k")
    assert a.calls == 2


def test_chain_resolver_invalidate_all():
    a = _StaticBackend({"a": "1", "b": "2"})
    resolver = ChainResolver([a])
    resolver.get("a")
    resolver.get("b")
    resolver.invalidate()  # all
    resolver.get("a")
    resolver.get("b")
    assert a.calls == 4


# ---------- Module-level facade ----------


def test_get_secret_uses_env_first(monkeypatch):
    monkeypatch.setenv("MINIONS_SECRET_FOO", "from-env")
    assert get_secret("foo") == "from-env"


def test_get_secret_missing_raises(monkeypatch):
    monkeypatch.delenv("MINIONS_SECRET_NOT_SET", raising=False)
    # Replace the chain with env-only so we don't depend on AWS being absent.
    secrets.configure([EnvBackend()])
    with pytest.raises(SecretNotFound, match="not-set"):
        get_secret("not-set")


def test_get_secret_falls_through_to_aws():
    aws_mock = MagicMock()
    aws_mock.get_secret_value.return_value = {"SecretString": "from-aws"}
    secrets.configure([EnvBackend(), AwsSecretsManagerBackend(client=aws_mock)])
    assert get_secret("test-key") == "from-aws"


def test_configure_replaces_chain():
    secrets.configure([_StaticBackend({"x": "y"})])
    assert get_secret("x") == "y"


def test_list_backends_default():
    # Default chain is EnvBackend + AwsSecretsManagerBackend.
    labels = secrets.list_backends()
    assert labels == ["env", "aws_secrets_manager"]


def test_get_anthropic_api_key_prefers_standard_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-direct")
    monkeypatch.setenv("MINIONS_SECRET_ANTHROPIC_API_KEY", "sk-via-secrets")
    assert get_anthropic_api_key() == "sk-direct"


def test_get_anthropic_api_key_falls_back_to_secrets(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MINIONS_SECRET_ANTHROPIC_API_KEY", "sk-via-secrets")
    assert get_anthropic_api_key() == "sk-via-secrets"


def test_get_token_signing_key_dev_fallback(monkeypatch):
    monkeypatch.delenv("MINIONS_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("MINIONS_SECRET_TOKEN_SIGNING_KEY", raising=False)
    secrets.configure([EnvBackend()])
    key = get_token_signing_key()
    assert "dev-only" in key


def test_get_token_signing_key_falls_back_on_aws_error(monkeypatch):
    """When AWS SM raises (e.g. AccessDenied), the dev fallback still kicks in.

    Regression: the fallback used to catch only SecretNotFound, so AWS errors
    crashed downstream callers like ConsoleNotifier (which calls sign() to
    render preview tokens).
    """
    monkeypatch.delenv("MINIONS_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("MINIONS_SECRET_TOKEN_SIGNING_KEY", raising=False)

    class _AccessDenied(Exception):
        def __init__(self) -> None:
            self.response = {"Error": {"Code": "AccessDeniedException"}}

    aws_mock = MagicMock()
    aws_mock.get_secret_value.side_effect = _AccessDenied()
    secrets.configure([EnvBackend(), AwsSecretsManagerBackend(client=aws_mock)])

    key = get_token_signing_key()
    assert "dev-only" in key
