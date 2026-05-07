"""End-to-end tests for the FastAPI approval webhook.

Uses an in-process JSON DecisionStore + a recording notifier; no DB or
network. Asserts: token verification, approve/reject happy paths,
idempotency on a second click, and rejection of bad/expired/wrong-action
tokens.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from minions.approval.store import DecisionStore
from minions.approval.tokens import sign
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.webhook.app import create_app


class _RecordingNotifier:
    def __init__(self) -> None:
        self.resolutions: list[Decision] = []

    def notify_approval_request(self, decision: Decision) -> None:  # pragma: no cover
        pass

    def notify_decision_resolved(self, decision: Decision) -> None:
        self.resolutions.append(decision)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("MINIONS_TOKEN_SECRET", "test-key")


@pytest.fixture
def store(tmp_path) -> DecisionStore:
    return DecisionStore(tmp_path / "decisions.json")


@pytest.fixture
def decision(store: DecisionStore) -> Decision:
    d = Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="Webhook test",
        rationale="unit test",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
    )
    store.save(d)
    return d


@pytest.fixture
def client(store, env) -> tuple[TestClient, _RecordingNotifier]:
    notifier = _RecordingNotifier()
    app = create_app(store_factory=lambda: store, notifier=notifier)
    return TestClient(app), notifier


def test_healthz(client):
    c, _ = client
    r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_approve_happy_path(client, store, decision):
    c, notifier = client
    token = sign(decision.id, "approve")
    r = c.get("/approve", params={"token": token})
    assert r.status_code == 200
    assert "APPROVED" in r.text
    assert store.get(decision.id).status is DecisionStatus.APPROVED
    assert len(notifier.resolutions) == 1


def test_reject_happy_path(client, store, decision):
    c, notifier = client
    token = sign(decision.id, "reject")
    r = c.get("/reject", params={"token": token})
    assert r.status_code == 200
    assert "REJECTED" in r.text
    assert store.get(decision.id).status is DecisionStatus.REJECTED


def test_second_click_is_idempotent(client, store, decision):
    c, notifier = client
    token = sign(decision.id, "approve")
    c.get("/approve", params={"token": token})
    r = c.get("/approve", params={"token": token})
    assert r.status_code == 200
    assert "APPROVED" in r.text
    # Notifier fires once on the fresh resolve, not on the replay.
    assert len(notifier.resolutions) == 1


def test_bad_signature_rejected(client, decision):
    c, _ = client
    token = sign(decision.id, "approve")
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    r = c.get("/approve", params={"token": tampered})
    assert r.status_code == 400
    assert "invalid" in r.text.lower() or "expired" in r.text.lower()


def test_action_mismatch_rejected(client, decision):
    c, _ = client
    token = sign(decision.id, "approve")
    r = c.get("/reject", params={"token": token})
    assert r.status_code == 400
    assert "mismatch" in r.text.lower()


def test_expired_token_rejected(client, decision):
    c, _ = client
    # ttl in the past → instantly expired
    token = sign(decision.id, "approve", ttl_seconds=-10)
    # sanity: time has indeed passed
    assert int(time.time()) > 0
    r = c.get("/approve", params={"token": token})
    assert r.status_code == 400


def test_unknown_decision_id_404(client, env):
    notifier = _RecordingNotifier()
    empty_store = type(
        "Empty",
        (),
        {
            "get": lambda self, _id: None,
            "save": lambda self, _d: None,
            "list_all": lambda self: [],
            "list_by_status": lambda self, _s: [],
            "update_status": lambda self, *a, **k: (_ for _ in ()).throw(KeyError()),
        },
    )()
    app = create_app(store_factory=lambda: empty_store, notifier=notifier)
    c = TestClient(app)
    from uuid import uuid4

    token = sign(uuid4(), "approve")
    r = c.get("/approve", params={"token": token})
    assert r.status_code == 404
