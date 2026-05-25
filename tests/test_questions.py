"""Tests for the Question Record subsystem."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from minions.models.question import QuestionRecord, QuestionStatus
from minions.notify.base import Notifier
from minions.questions import answer_question, escalate_question, submit_question
from minions.questions.store import QuestionStore


class _RecordingNotifier(Notifier):
    def __init__(self) -> None:
        self.text_calls: list[tuple[str, str]] = []

    def notify_approval_request(self, decision: Any) -> None: ...
    def notify_decision_resolved(self, decision: Any) -> None: ...
    def notify_text(self, *, subject: str, body: str) -> None:
        self.text_calls.append((subject, body))


def _q(**kw: Any) -> QuestionRecord:
    defaults = dict(
        project="Demo",
        asker_role="engineer",
        asker_agent_id="engineer@Demo",
        target_role="manager",
        question="Should we add CI to the new repo?",
    )
    defaults.update(kw)
    return QuestionRecord(**defaults)


def test_submit_persists_as_open(tmp_path: Path) -> None:
    store = QuestionStore(tmp_path / "q.json")
    q = submit_question(_q(), store=store)
    assert q.status is QuestionStatus.OPEN

    fetched = store.get(q.id)
    assert fetched is not None
    assert fetched.status is QuestionStatus.OPEN
    assert fetched.question == q.question


def test_answer_transitions_to_answered(tmp_path: Path) -> None:
    store = QuestionStore(tmp_path / "q.json")
    q = submit_question(_q(), store=store)

    answered = answer_question(q.id, store=store, answer="yes, add CI", answered_by="manager")
    assert answered.status is QuestionStatus.ANSWERED
    assert answered.answer == "yes, add CI"
    assert answered.answered_by == "manager"
    assert answered.answered_at is not None

    refetched = store.get(q.id)
    assert refetched is not None
    assert refetched.status is QuestionStatus.ANSWERED


def test_escalate_fires_notifier_and_marks_record(tmp_path: Path) -> None:
    store = QuestionStore(tmp_path / "q.json")
    q = submit_question(
        _q(context="Engineer crew failed twice on JSON truncation."),
        store=store,
    )
    notifier = _RecordingNotifier()

    escalated = escalate_question(
        q.id, store=store, notifier=notifier, reason="manager unavailable for 24h"
    )

    assert escalated.status is QuestionStatus.ESCALATED
    assert escalated.escalation_reason == "manager unavailable for 24h"
    assert escalated.escalated_at is not None

    # Notifier got a single text call with the right shape.
    assert len(notifier.text_calls) == 1
    subject, body = notifier.text_calls[0]
    assert "Demo" in subject
    assert "engineer" in subject and "operator" in subject
    assert "manager unavailable" in body
    assert "Engineer crew failed twice on JSON truncation." in body


def test_answer_missing_id_raises(tmp_path: Path) -> None:
    store = QuestionStore(tmp_path / "q.json")
    with pytest.raises(KeyError):
        answer_question(uuid4(), store=store, answer="x", answered_by="manager")


def test_list_by_status_filters(tmp_path: Path) -> None:
    store = QuestionStore(tmp_path / "q.json")
    q1 = submit_question(_q(question="q1"), store=store)
    q2 = submit_question(_q(question="q2"), store=store)
    answer_question(q1.id, store=store, answer="done", answered_by="manager")

    open_qs = store.list_by_status(QuestionStatus.OPEN)
    answered_qs = store.list_by_status(QuestionStatus.ANSWERED)
    assert {q.id for q in open_qs} == {q2.id}
    assert {q.id for q in answered_qs} == {q1.id}
