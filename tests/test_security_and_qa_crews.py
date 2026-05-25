"""Tests for the Security Champion + QA Engineer crews + their pipeline hooks."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from minions.approval.store import DecisionStore
from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.crews.qa import QAReview, render_pr_comment, run_qa_review
from minions.crews.security import attach_review, should_review
from minions.models.decision import Decision, DecisionType, SecurityReview
from minions.notify.base import Notifier
from minions.scheduled.pr_followup import run_pr_followup

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


# --------- Security crew ---------


def _decision(risk: str = "medium") -> Decision:
    return Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="Add auth middleware",
        rationale="Need user auth",
        diff_or_plan="Add session tokens, store in cookies.",
        risk=risk,
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
    )


def test_security_should_review_gate() -> None:
    assert should_review(_decision(risk="low")) is False
    assert should_review(_decision(risk="medium")) is True
    assert should_review(_decision(risk="high")) is True


def test_attach_review_noop_on_low_risk() -> None:
    d = _decision(risk="low")
    review = attach_review(
        d,
        output_override=SecurityReview(verdict="pass", concerns=[], reasoning="not applicable"),
    )
    assert review is None
    assert d.security_review is None


def test_attach_review_with_override_sets_field() -> None:
    d = _decision(risk="medium")
    override = SecurityReview(
        verdict="flag",
        concerns=["Session cookies need SameSite=Strict"],
        reasoning="The plan stores session tokens in cookies but doesn't specify SameSite.",
    )
    result = attach_review(d, output_override=override)
    assert result is override
    assert d.security_review is not None
    assert d.security_review.verdict == "flag"
    assert "SameSite" in d.security_review.concerns[0]


def test_attach_review_noop_without_api_key_or_override() -> None:
    d = _decision(risk="medium")
    result = attach_review(d, api_key=None, output_override=None)
    assert result is None
    assert d.security_review is None


# --------- QA crew ---------


def test_qa_review_with_override_returns_unmodified() -> None:
    override = QAReview(
        test_coverage_score=7,
        concerns=["No test for 429 response path"],
        suggested_tests=["assert POST /api/x with 11 rapid requests returns 429"],
    )
    out = run_qa_review(_decision(), files_changed=["src/index.js"], output_override=override)
    assert out is override


def test_qa_review_noop_without_api_key() -> None:
    out = run_qa_review(_decision(), files_changed=["x"], api_key=None)
    assert out is None


def test_render_pr_comment_includes_score_and_concerns() -> None:
    review = QAReview(
        test_coverage_score=4,
        concerns=["No edge-case test for empty input"],
        suggested_tests=["test_handles_empty_payload"],
    )
    body = render_pr_comment(review)
    assert "QA Engineer review" in body
    assert "4/10" in body
    assert "No edge-case test" in body
    assert "test_handles_empty_payload" in body


def test_render_pr_comment_no_concerns_renders_clean_message() -> None:
    review = QAReview(test_coverage_score=9, concerns=[], suggested_tests=[])
    body = render_pr_comment(review)
    assert "9/10" in body
    assert "No additional concerns" in body


# --------- QA hook into pr_followup ---------


class _FakeGH:
    def __init__(self, ci_for: dict[int, tuple[str | None, str | None]]) -> None:
        self.ci_for = ci_for
        self.comments: list[tuple[int, str]] = []

    def __enter__(self) -> _FakeGH:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def get_pr_check_status(self, number: int) -> tuple[str | None, str | None]:
        return self.ci_for.get(number, (None, None))

    def comment_on_pull_request(self, *, number: int, body: str) -> None:
        self.comments.append((number, body))


class _NullNotifier(Notifier):
    def notify_approval_request(self, decision: Any) -> None: ...
    def notify_decision_resolved(self, decision: Any) -> None: ...
    def notify_text(self, *, subject: str, body: str) -> None: ...


def test_pr_followup_posts_qa_comment_on_success_ci(monkeypatch: Any, tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "d.json")
    runs = EngineerRunStore(tmp_path / "r.json")

    # Persist a real decision so the QA hook can fetch it.
    d = Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="Add CI",
        rationale="r",
        diff_or_plan="plan",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
    )
    decisions.save(d)

    runs.save(
        EngineerResult(
            decision_id=str(d.id),
            pr_url="https://github.com/x/Demo/pull/1",
            pr_number=1,
            branch_name="b",
            files_changed=["src/index.js"],
            dry_run=False,
        ),
        project="Demo",
    )

    gh = _FakeGH(ci_for={1: ("success", None)})

    # Stub the QA crew to return a fixed review without LLM calls.
    fixed_review = QAReview(test_coverage_score=8, concerns=["nit"], suggested_tests=["t1"])
    monkeypatch.setattr(
        "minions.scheduled.pr_followup.run_qa_review",
        lambda *a, **kw: fixed_review,
    )

    report = run_pr_followup(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        notifier=_NullNotifier(),
        open_github_client=lambda _m: gh,
        api_key="fake-key-so-the-gate-passes",
    )

    assert report.outcomes[0].status == "ok"
    # QA comment posted exactly once.
    assert len(gh.comments) == 1
    assert "QA Engineer review" in gh.comments[0][1]
    assert "8/10" in gh.comments[0][1]

    # Record flagged so a second tick doesn't double-post.
    after = runs.get(str(d.id))
    assert after is not None
    assert after.qa_review_posted_at is not None


def test_pr_followup_skips_qa_when_already_posted(monkeypatch: Any, tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "d.json")
    runs = EngineerRunStore(tmp_path / "r.json")

    d = Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="x",
        rationale="r",
        diff_or_plan="p",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
    )
    decisions.save(d)

    runs.save(
        EngineerResult(
            decision_id=str(d.id),
            pr_url="https://x/1",
            pr_number=1,
            branch_name="b",
            files_changed=["a"],
            dry_run=False,
        ),
        project="Demo",
    )
    # Mark already-QA'd.
    rec = runs.get(str(d.id))
    assert rec is not None
    rec.qa_review_posted_at = datetime.now(tz=UTC)
    runs.update(rec)

    gh = _FakeGH(ci_for={1: ("success", None)})

    called = {"n": 0}

    def _should_not_run(*_a: Any, **_kw: Any) -> Any:
        called["n"] += 1
        return None

    monkeypatch.setattr("minions.scheduled.pr_followup.run_qa_review", _should_not_run)

    run_pr_followup(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        notifier=_NullNotifier(),
        open_github_client=lambda _m: gh,
        api_key="fake-key",
    )

    assert called["n"] == 0
    assert gh.comments == []
