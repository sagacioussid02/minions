"""Light regression guards for the PR 2 contract.

Operator does the real smoke testing; these tests just lock down the two
invariants that are cheap to assert and easy to break by accident:

  1. ``pr_owner_sweep._classify_failure`` picks
     review_changes_requested only when nothing harder is failing.
  2. ``pr_review_loop.MAX_REVIEW_ROUNDS_PER_PR`` is 2 (operator-agreed).
"""

from __future__ import annotations

from minions.scheduled.pr_owner_sweep import _classify_failure
from minions.scheduled.pr_review_loop import MAX_REVIEW_ROUNDS_PER_PR


def test_review_cap_is_two() -> None:
    assert MAX_REVIEW_ROUNDS_PER_PR == 2


def test_classify_priority_merge_conflict_wins() -> None:
    assert _classify_failure("failure", "dirty", "changes_requested") == "merge_conflict"


def test_classify_priority_ci_failure_over_review() -> None:
    assert _classify_failure("failure", "clean", "changes_requested") == "ci_failure"


def test_classify_returns_review_when_only_review_failing() -> None:
    assert _classify_failure("success", "clean", "changes_requested") == "review_changes_requested"


def test_classify_returns_none_when_healthy() -> None:
    assert _classify_failure("success", "clean", "crew_approved") is None
    assert _classify_failure(None, None, None) is None
