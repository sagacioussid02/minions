"""Tests for the maintainer-bot signal gathering + ranking."""

from __future__ import annotations

from pathlib import Path

from minions.models.question import QuestionRecord, QuestionStatus
from minions.questions.store import QuestionStore
from minions.scheduled import run_maintainer


def _q(project: str, text: str, status: QuestionStatus = QuestionStatus.OPEN) -> QuestionRecord:
    return QuestionRecord(
        project=project,
        asker_role="engineer",
        asker_agent_id=f"engineer@{project}",
        target_role="product_owner",
        question=text,
        status=status,
    )


def test_no_signals_on_empty(tmp_path: Path) -> None:
    report = run_maintainer(questions_store=QuestionStore(tmp_path / "q.json"))
    assert report.signals == []
    assert report.proposed == []
    assert "Nothing to do" in report.to_markdown()


def test_gathers_and_ranks_escalated_first(tmp_path: Path) -> None:
    store = QuestionStore(tmp_path / "q.json")
    store.save(_q("Demo", "an open question", QuestionStatus.OPEN))
    store.save(_q("Demo", "an escalated one", QuestionStatus.ESCALATED))

    report = run_maintainer(questions_store=store, dry_run=True)

    assert len(report.signals) == 2
    assert report.signals[0].title == "an escalated one"  # higher priority first
    assert all(s.source == "question" for s in report.signals)


def test_skips_answered_and_cancelled(tmp_path: Path) -> None:
    store = QuestionStore(tmp_path / "q.json")
    store.save(_q("Demo", "answered", QuestionStatus.ANSWERED))
    store.save(_q("Demo", "cancelled", QuestionStatus.CANCELLED))
    assert run_maintainer(questions_store=store).signals == []


def test_max_proposals_caps_without_dropping_signals(tmp_path: Path) -> None:
    store = QuestionStore(tmp_path / "q.json")
    for i in range(5):
        store.save(_q("Demo", f"q{i}"))

    report = run_maintainer(questions_store=store, max_proposals=2, dry_run=False)

    assert len(report.signals) == 5  # all surfaced
    assert len(report.proposed) == 2  # only the cap acted on
