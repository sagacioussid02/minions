"""Tests for the Anthropic auth preflight."""

from __future__ import annotations

from unittest.mock import patch

import httpx

from minions import anthropic_check


def test_empty_key_returns_false():
    ok, msg = anthropic_check.auth_check("")
    assert ok is False
    assert "empty" in msg


def _mock_response(status: int, body: str = "") -> httpx.Response:
    return httpx.Response(
        status, text=body, request=httpx.Request("GET", "https://api.anthropic.com/v1/models")
    )


def test_200_authenticated():
    with patch.object(httpx, "get", return_value=_mock_response(200, '{"data":[]}')):
        ok, msg = anthropic_check.auth_check("sk-ant-fake")
    assert ok is True
    assert msg == "authenticated"


def test_401_helpful_message():
    with patch.object(httpx, "get", return_value=_mock_response(401)):
        ok, msg = anthropic_check.auth_check("sk-ant-fake")
    assert ok is False
    assert "401" in msg
    assert "console.anthropic.com" in msg


def test_403_calls_out_workspace():
    with patch.object(httpx, "get", return_value=_mock_response(403)):
        ok, msg = anthropic_check.auth_check("sk-ant-fake")
    assert ok is False
    assert "workspace" in msg


def test_429_rate_limited():
    with patch.object(httpx, "get", return_value=_mock_response(429)):
        ok, msg = anthropic_check.auth_check("sk-ant-fake")
    assert ok is False
    assert "rate" in msg.lower()


def test_network_error():
    with patch.object(httpx, "get", side_effect=httpx.ConnectError("DNS")):
        ok, msg = anthropic_check.auth_check("sk-ant-fake")
    assert ok is False
    assert "network" in msg
