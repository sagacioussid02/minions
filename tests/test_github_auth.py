"""Tests for the GitHub token resolver."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from minions import secrets
from minions.github import auth as github_auth
from minions.secrets import EnvBackend, SecretNotFound


def test_env_var_wins(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")
    secrets.configure([EnvBackend()])
    assert github_auth.get_github_token(validate=False) == "from-env"


# ---- Validation fallthrough -------------------------------------------------


def test_invalid_env_token_falls_through_to_next_source(monkeypatch):
    """A 401 on the env-var token must fall through, not dead-end."""
    monkeypatch.setenv("GITHUB_TOKEN", "stale-token")
    monkeypatch.setenv("MINIONS_SECRET_GITHUB_TOKEN", "good-fallback")
    secrets.configure([EnvBackend()])
    github_auth.reset_validation_cache()

    import httpx

    def mock_get(*args, **kwargs):
        return httpx.Response(401, request=httpx.Request("GET", "https://api.github.com/user"))

    with patch.object(github_auth.httpx, "get", side_effect=mock_get):
        assert github_auth.get_github_token() == "good-fallback"


def test_valid_env_token_short_circuits_validation(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "fresh-token")
    secrets.configure([EnvBackend()])
    github_auth.reset_validation_cache()

    import httpx

    def mock_get(*args, **kwargs):
        return httpx.Response(
            200,
            json={"login": "octocat"},
            request=httpx.Request("GET", "https://api.github.com/user"),
        )

    with patch.object(github_auth.httpx, "get", side_effect=mock_get):
        assert github_auth.get_github_token() == "fresh-token"


def test_validation_cached_per_token(monkeypatch):
    """A repeated call for the same token must not re-hit GitHub."""
    monkeypatch.setenv("GITHUB_TOKEN", "cached-token")
    secrets.configure([EnvBackend()])
    github_auth.reset_validation_cache()

    import httpx

    call_count = {"n": 0}

    def mock_get(*args, **kwargs):
        call_count["n"] += 1
        return httpx.Response(
            200, json={}, request=httpx.Request("GET", "https://api.github.com/user")
        )

    with patch.object(github_auth.httpx, "get", side_effect=mock_get):
        github_auth.get_github_token()
        github_auth.get_github_token()
        github_auth.get_github_token()
    assert call_count["n"] == 1


def test_network_error_during_validation_trusts_token(monkeypatch):
    """If validation can't run (offline), don't pretend the token is bad."""
    monkeypatch.setenv("GITHUB_TOKEN", "maybe-good")
    secrets.configure([EnvBackend()])
    github_auth.reset_validation_cache()

    import httpx

    def mock_get(*args, **kwargs):
        raise httpx.ConnectError("offline")

    with patch.object(github_auth.httpx, "get", side_effect=mock_get):
        assert github_auth.get_github_token() == "maybe-good"


def test_falls_back_to_secret(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("MINIONS_SECRET_GITHUB_TOKEN", "from-secret")
    secrets.configure([EnvBackend()])  # no AWS in test chain
    assert github_auth.get_github_token() == "from-secret"


def test_falls_back_to_gh_cli(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    secrets.configure([EnvBackend()])  # no env secret, no AWS

    with patch.object(github_auth.shutil, "which", return_value="/usr/local/bin/gh"):
        with patch.object(
            github_auth.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=["gh", "auth", "token"], returncode=0, stdout="gho_fromcli\n", stderr=""
            ),
        ):
            assert github_auth.get_github_token() == "gho_fromcli"


def test_raises_when_nothing_resolves(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    secrets.configure([EnvBackend()])
    with patch.object(github_auth.shutil, "which", return_value=None):
        with pytest.raises(SecretNotFound, match="GitHub token not found"):
            github_auth.get_github_token()


def test_gh_cli_failure_falls_through(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    secrets.configure([EnvBackend()])
    with patch.object(github_auth.shutil, "which", return_value="/usr/local/bin/gh"):
        with patch.object(
            github_auth.subprocess,
            "run",
            return_value=subprocess.CompletedProcess(
                args=["gh", "auth", "token"], returncode=1, stdout="", stderr="not logged in"
            ),
        ):
            with pytest.raises(SecretNotFound):
                github_auth.get_github_token()
