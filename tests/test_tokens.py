import time
from uuid import uuid4

import pytest

from minions.approval.tokens import TokenError, sign, verify


def test_sign_and_verify_roundtrip(monkeypatch):
    monkeypatch.setenv("MINIONS_TOKEN_SECRET", "test-key")
    decision_id = uuid4()
    token = sign(decision_id, "approve")
    payload = verify(token)
    assert payload["id"] == str(decision_id)
    assert payload["action"] == "approve"
    assert payload["exp"] > int(time.time())


def test_invalid_action_raises(monkeypatch):
    monkeypatch.setenv("MINIONS_TOKEN_SECRET", "test-key")
    with pytest.raises(ValueError, match="approve"):
        sign(uuid4(), "merge")


def test_bad_signature_rejected(monkeypatch):
    monkeypatch.setenv("MINIONS_TOKEN_SECRET", "test-key")
    token = sign(uuid4(), "approve")
    head, _ = token.split(".")
    tampered = f"{head}.AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    with pytest.raises(TokenError, match="bad signature"):
        verify(tampered)


def test_malformed_token_rejected(monkeypatch):
    monkeypatch.setenv("MINIONS_TOKEN_SECRET", "test-key")
    with pytest.raises(TokenError, match="malformed"):
        verify("not-a-token")


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setenv("MINIONS_TOKEN_SECRET", "test-key")
    token = sign(uuid4(), "approve", ttl_seconds=-1)
    with pytest.raises(TokenError, match="expired"):
        verify(token)


def test_different_keys_dont_verify(monkeypatch):
    monkeypatch.setenv("MINIONS_TOKEN_SECRET", "key-A")
    token = sign(uuid4(), "approve")
    monkeypatch.setenv("MINIONS_TOKEN_SECRET", "key-B")
    with pytest.raises(TokenError, match="bad signature"):
        verify(token)
