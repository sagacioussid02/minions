"""Minimal smoke for the LLM-driven PR reviewer.

Three cases: stub fallback when api_key=None, parse-failure path, and
role-to-tier mapping. LLM dispatch itself is exercised live during
operator validation, not in unit tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

from minions.crews.engineer_runs_store import EngineerRunRecord, PRReviewerAssignment
from minions.crews.pr_reviewer import _ROLE_TO_CREW_ROLE, _parse_loose, run_pr_review
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.roles import Role
from minions.scheduled.pr_review_loop import StructuredReview


def _decision() -> Decision:
    return Decision(
        project="Demo",
        type=DecisionType.FEATURE,
        summary="x",
        rationale="y",
        proposer_role="manager",
        proposer_agent_id="manager@Demo",
        status=DecisionStatus.EXECUTED,
    )


def _record() -> EngineerRunRecord:
    return EngineerRunRecord(
        decision_id="00000000-0000-4000-8000-000000000001",
        project="Demo",
        completed_at=datetime.now(tz=UTC),
        pr_url="https://x/p/1",
        pr_number=1,
        pr_state="open",
        branch_name="minions/eng/x",
    )


def _reviewer(role: str = "ttl") -> PRReviewerAssignment:
    return PRReviewerAssignment(
        role=role,
        agent_id=f"{role}@Demo",
        display_name=role.upper(),
    )


def test_stub_fallback_when_no_api_key() -> None:
    review = run_pr_review(
        role="ttl",
        decision=_decision(),
        record=_record(),
        reviewer=_reviewer(),
        pr_files=[],
        prior_comments=[],
        ci_conclusion="success",
        ci_details_url=None,
        api_key=None,
    )
    # Stub path produces the legacy "acceptable shape" approve.
    assert review.role == "ttl"
    assert review.verdict in ("approve", "comment", "request_changes")
    assert "acceptable shape" in review.summary.lower() or review.verdict in (
        "comment",
        "request_changes",
    )


def test_role_to_crew_role_mapping() -> None:
    assert _ROLE_TO_CREW_ROLE["ttl"] == Role.TTL
    assert _ROLE_TO_CREW_ROLE["qa_engineer"] == Role.QA_ENGINEER
    assert _ROLE_TO_CREW_ROLE["security_champion"] == Role.SECURITY_CHAMPION


def test_parse_loose_handles_fenced_json() -> None:
    text = (
        "Here is my review:\n\n"
        "```json\n"
        '{"role":"ttl","verdict":"request_changes",'
        '"summary":"Missing tests for src/x.py","body":"## TTL\\nplease add tests"}'
        "\n```"
    )
    review = _parse_loose(text, role="ttl")
    assert isinstance(review, StructuredReview)
    assert review.verdict == "request_changes"
    assert "Missing tests" in review.summary


def test_parse_loose_handles_bare_json() -> None:
    text = (
        '{"role":"qa_engineer","verdict":"comment","summary":"checks unknown",'
        '"body":"will revisit"}'
    )
    review = _parse_loose(text, role="qa_engineer")
    assert isinstance(review, StructuredReview)
    assert review.verdict == "comment"


def test_parse_loose_returns_none_on_garbage() -> None:
    assert _parse_loose("just prose, no json here", role="ttl") is None
    assert _parse_loose("```json\n{bad json}\n```", role="ttl") is None
