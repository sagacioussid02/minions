"""Question Record service — submit, answer, escalate.

Matches `approval/service.py` in spirit:
  * `submit_question`: persist as OPEN. No notifier hit by default — Questions
    are intra-org first; only escalation notifies the operator.
  * `answer_question`: another role (or the operator) responds; transitions to
    ANSWERED.
  * `escalate_question`: target role couldn't answer; bumps to operator via
    `notifier.notify_text`. Transitions to ESCALATED.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from minions.models.question import QuestionRecord, QuestionStatus
from minions.notify.base import Notifier
from minions.questions.store_factory import QuestionStoreLike


def submit_question(
    question: QuestionRecord,
    *,
    store: QuestionStoreLike,
) -> QuestionRecord:
    """Persist a new question as OPEN."""
    question.status = QuestionStatus.OPEN
    store.save(question)
    return question


def answer_question(
    question_id: UUID | str,
    *,
    store: QuestionStoreLike,
    answer: str,
    answered_by: str,
) -> QuestionRecord:
    """Mark a question ANSWERED with the given response."""
    q = store.get(question_id)
    if q is None:
        raise KeyError(question_id)
    q.status = QuestionStatus.ANSWERED
    q.answer = answer
    q.answered_by = answered_by
    q.answered_at = datetime.now(UTC)
    store.save(q)
    return q


def escalate_question(
    question_id: UUID | str,
    *,
    store: QuestionStoreLike,
    notifier: Notifier,
    reason: str | None = None,
) -> QuestionRecord:
    """Bump a question to the operator. Updates state + fires the notifier."""
    q = store.get(question_id)
    if q is None:
        raise KeyError(question_id)
    q.status = QuestionStatus.ESCALATED
    q.escalated_at = datetime.now(UTC)
    q.escalation_reason = reason
    store.save(q)

    body_lines = [
        f"Project: {q.project}",
        f"Asker:   {q.asker_role} ({q.asker_agent_id})",
        f"Target:  {q.target_role} — could not resolve, escalated to you",
        "",
        f"Question: {q.question}",
    ]
    if q.context:
        body_lines += ["", "Context:", q.context]
    if q.related_pr_url:
        body_lines += ["", f"Related PR: {q.related_pr_url}"]
    if reason:
        body_lines += ["", f"Escalation reason: {reason}"]

    notifier.notify_text(
        subject=f"[minions/{q.project}] {q.asker_role} → operator: {q.question[:60]}",
        body="\n".join(body_lines),
    )
    return q
