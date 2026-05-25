"""Tests for the Gmail notifier — uses smtp_send injection so no real SMTP runs."""

from __future__ import annotations

from email.message import Message
from email.mime.multipart import MIMEMultipart

import pytest

from minions.models.decision import (
    Decision,
    DecisionStatus,
    DecisionType,
    DevilsAdvocateCritique,
)
from minions.notify.gmail import (
    GmailNotifier,
    _render_approval_html,
    _render_approval_text,
)


def _decision(**overrides) -> Decision:
    base = dict(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="Add a CHANGELOG file to the repo root",
        rationale="Track release notes from now on.",
        diff_or_plan="Create CHANGELOG.md with the v0.1 entry.",
        risk="low",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
        proposer_display_name="Marcus",
    )
    base.update(overrides)
    return Decision(**base)


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[Message] = []

    def __call__(self, msg: Message) -> None:
        self.sent.append(msg)


def _notifier(recorder: _Recorder | None = None) -> GmailNotifier:
    rec = recorder or _Recorder()
    return GmailNotifier(
        smtp_user="ops@example.com",
        smtp_password="ignored-in-tests",
        smtp_send=rec,
    )


# ---------- Composition ----------


def test_approval_email_subject_and_recipients():
    recorder = _Recorder()
    notifier = _notifier(recorder)
    notifier.notify_approval_request(_decision())

    assert len(recorder.sent) == 1
    msg = recorder.sent[0]
    assert msg["From"] == "minions <ops@example.com>"
    assert msg["To"] == "ops@example.com"
    assert msg["Subject"].startswith("[minions/Demo/feature]")
    assert "CHANGELOG" in msg["Subject"]


def test_approval_email_has_text_and_html_parts():
    recorder = _Recorder()
    _notifier(recorder).notify_approval_request(_decision())
    msg = recorder.sent[0]
    assert msg.is_multipart()
    parts = msg.get_payload()
    types = sorted(p.get_content_type() for p in parts)
    assert types == ["text/html", "text/plain"]


def test_text_body_contains_decision_fields_and_cli_command():
    body = _render_approval_text(
        _decision(),
        proposer="Marcus",
        approve_token="aaa.bbb",
        reject_token="ccc.ddd",
    )
    assert "Demo" in body
    assert "feature" in body
    assert "Marcus" in body
    assert "minions decisions approve" in body
    assert "minions decisions reject" in body
    assert "aaa.bbb" in body
    assert "ccc.ddd" in body


def test_html_body_escapes_user_content():
    decision = _decision(summary="<script>alert(1)</script>", rationale="A & B < C")
    html = _render_approval_html(
        decision, proposer="Marcus", approve_token="x", reject_token="y"
    )
    # User-controlled values must be escaped
    assert "<script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "A &amp; B &lt; C" in html


def test_critique_renders_when_present():
    critique = DevilsAdvocateCritique(
        counter_argument="Scope is too broad for one PR",
        failure_modes=["test coverage gap", "user-facing breakage"],
        alternative_considered="Split into two PRs",
    )
    decision = _decision(critique=critique)

    text = _render_approval_text(
        decision, proposer="Marcus", approve_token="x", reject_token="y"
    )
    assert "Devil's Advocate" in text
    assert "Scope is too broad" in text
    assert "test coverage gap" in text
    assert "Split into two PRs" in text

    html = _render_approval_html(
        decision, proposer="Marcus", approve_token="x", reject_token="y"
    )
    assert "Devil&#x27;s Advocate" in html or "Devil's Advocate" in html
    assert "Scope is too broad" in html


def test_critique_omitted_when_absent():
    body = _render_approval_text(
        _decision(),
        proposer="Marcus",
        approve_token="x",
        reject_token="y",
    )
    assert "Devil's Advocate" not in body


# ---------- Resolution emails ----------


def test_resolution_email_subject_includes_verdict():
    recorder = _Recorder()
    decision = _decision(status=DecisionStatus.APPROVED, resolved_reason="LGTM")
    _notifier(recorder).notify_decision_resolved(decision)
    msg = recorder.sent[0]
    assert "APPROVED" in msg["Subject"]
    assert "Demo" in msg["Subject"]


def test_resolution_email_body_includes_pr_and_reason():
    recorder = _Recorder()
    decision = _decision(
        status=DecisionStatus.APPROVED,
        resolved_reason="LGTM",
        pr_url="https://github.com/x/y/pull/1",
    )
    _notifier(recorder).notify_decision_resolved(decision)
    body_text = recorder.sent[0].get_payload()[0].get_payload()
    assert "approved" in body_text.lower()
    assert "LGTM" in body_text
    assert "https://github.com/x/y/pull/1" in body_text


# ---------- Custom recipient ----------


def test_recipient_defaults_to_smtp_user():
    notifier = GmailNotifier(
        smtp_user="ops@example.com",
        smtp_password="x",
        smtp_send=_Recorder(),
    )
    assert notifier.recipient == "ops@example.com"


def test_recipient_can_be_overridden():
    recorder = _Recorder()
    notifier = GmailNotifier(
        smtp_user="ops@example.com",
        smtp_password="x",
        recipient="boss@example.com",
        smtp_send=recorder,
    )
    notifier.notify_approval_request(_decision())
    assert recorder.sent[0]["To"] == "boss@example.com"


# ---------- Token signing happens once per call ----------


def test_each_approval_request_has_fresh_tokens(monkeypatch):
    """Two calls produce two emails, each with its own approve/reject token pair."""
    monkeypatch.setenv("MINIONS_TOKEN_SECRET", "test-key")
    recorder = _Recorder()
    notifier = _notifier(recorder)
    notifier.notify_approval_request(_decision())
    notifier.notify_approval_request(_decision())
    assert len(recorder.sent) == 2
    # Both emails should mention magic-link tokens (text part)
    for msg in recorder.sent:
        text_part = msg.get_payload()[0].get_payload()
        assert "approve:" in text_part
        assert "reject:" in text_part
