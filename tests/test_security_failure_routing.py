"""Security-failure routing — promote ``ci_failure`` to ``security_failure``
when any failing check on the PR is from a security workflow, and pass that
signal to the engineer crew as a kwarg.

Tests are scoped to the pure helpers + the regex contract; the actual
engineer-prompt content is exercised at integration time.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from minions.crews.engineer import _SECURITY_CHECK_NAME_RE as ENGINEER_RE
from minions.crews.engineer_runs_store import EngineerRunRecord
from minions.github.models import FailingCheckLog
from minions.scheduled.pr_owner_sweep import (
    _SECURITY_CHECK_NAME_RE as SWEEP_RE,
)
from minions.scheduled.pr_owner_sweep import (
    _build_security_triage_comment,
    _post_security_triage_comment,
    _promote_to_security_failure,
)


def _log(name: str, conclusion: str = "failure") -> FailingCheckLog:
    return FailingCheckLog(
        check_name=name,
        app_slug=None,
        conclusion=conclusion,
        html_url=None,
        log_excerpt=f"(sample log for {name})",
        was_truncated=False,
        original_bytes=64,
    )


class _StubGithub:
    """Fake ``GitHubClient`` exposing only the methods the helper touches."""

    def __init__(self, *, logs: list[FailingCheckLog] | None = None, raises: bool = False):
        self._logs = logs or []
        self._raises = raises
        self.calls: list[Any] = []

    def get_pr_failing_check_logs(self, number: int) -> list[FailingCheckLog]:
        self.calls.append(number)
        if self._raises:
            raise RuntimeError("github 503")
        return self._logs


# ---- regex contract -------------------------------------------------------


def test_security_regex_matches_common_sast_dast_names() -> None:
    matches = [
        "CodeQL / analyze (javascript)",
        "Trivy scan",
        "Semgrep",
        "snyk-test",
        "Security audit (SAST)",
        "internal-DAST",
        "CodeQL",  # exact
    ]
    for name in matches:
        assert SWEEP_RE.search(name), name
        assert ENGINEER_RE.search(name), name


def test_security_regex_does_not_match_neutral_check_names() -> None:
    non_matches = [
        "lint",
        "ruff",
        "pytest (unit)",
        "build",
        "typecheck (mypy)",
        "deploy",
        "preview / Vercel",
    ]
    for name in non_matches:
        assert SWEEP_RE.search(name) is None, name


def test_sweep_and_engineer_regex_stay_in_lockstep() -> None:
    """Two modules carry the same regex on purpose — assert they agree on
    the same patterns so a drift here is caught in CI."""
    for name in ("codeql", "snyk", "ruff", "build"):
        assert bool(SWEEP_RE.search(name)) == bool(ENGINEER_RE.search(name)), name


# ---- promotion logic ------------------------------------------------------


def test_promote_only_acts_on_ci_failure() -> None:
    gh = _StubGithub(logs=[_log("CodeQL")])
    assert _promote_to_security_failure(None, gh, 1) is None
    assert _promote_to_security_failure("merge_conflict", gh, 1) == "merge_conflict"
    assert (
        _promote_to_security_failure("review_changes_requested", gh, 1)
        == "review_changes_requested"
    )
    # Helper never even calls GitHub for non-ci_failure cases.
    assert gh.calls == []


def test_promote_returns_security_failure_when_security_check_failed() -> None:
    gh = _StubGithub(logs=[_log("lint"), _log("CodeQL / analyze (python)")])
    assert _promote_to_security_failure("ci_failure", gh, 42) == "security_failure"
    assert gh.calls == [42]


def test_promote_returns_ci_failure_when_only_non_security_checks_failed() -> None:
    gh = _StubGithub(logs=[_log("ruff"), _log("pytest")])
    assert _promote_to_security_failure("ci_failure", gh, 7) == "ci_failure"


def test_promote_is_robust_to_github_errors() -> None:
    gh = _StubGithub(raises=True)
    assert _promote_to_security_failure("ci_failure", gh, 7) == "ci_failure"


def test_promote_no_pr_number_passes_through() -> None:
    gh = _StubGithub(logs=[_log("CodeQL")])
    assert _promote_to_security_failure("ci_failure", gh, None) == "ci_failure"
    assert gh.calls == []


# ---- read-only triage comment on operator-takeover branches ---------------


class _CommentingGithub(_StubGithub):
    """Adds the comment_on_pull_request hook the triage helper invokes."""

    def __init__(self, *, logs: list[FailingCheckLog] | None = None) -> None:
        super().__init__(logs=logs)
        self.comments: list[tuple[int, str]] = []

    def comment_on_pull_request(self, *, number: int, body: str) -> None:
        self.comments.append((number, body))


def _record_for_triage(**kw: Any) -> EngineerRunRecord:
    defaults: dict[str, Any] = {
        "decision_id": "00000000-0000-4000-8000-000000000099",
        "project": "demo_three",
        "completed_at": datetime.now(tz=UTC),
        "pr_url": "https://github.com/your-github-org/demo_three/pull/75",
        "pr_number": 75,
        "branch_name": "minions/eng/security-fix",
        "pr_state": "open",
        "owner_agent_id": "engineer@demo_three#1",
    }
    defaults.update(kw)
    return EngineerRunRecord(**defaults)


def test_build_triage_comment_includes_each_security_check() -> None:
    logs = [
        _log("CodeQL / analyze (javascript)"),
        _log("Trivy scan"),
        _log("ruff"),  # ignored — not security
    ]
    body = _build_security_triage_comment(logs, owner="engineer@demo_three#1")
    assert "CodeQL / analyze (javascript)" in body
    assert "Trivy scan" in body
    assert "ruff" not in body  # non-security check filtered out
    assert "Recommended next steps" in body
    assert "engineer@demo_three#1" in body


def test_build_triage_comment_handles_empty_security_logs() -> None:
    body = _build_security_triage_comment(
        [_log("ruff"), _log("pytest")],  # nothing security-flavoured
        owner="engineer@x",
    )
    # Defensive branch: race / classification drift — we still produce a
    # readable, honest comment instead of crashing.
    assert "Security triage" in body
    assert "no security check was failing" in body


def test_post_triage_dedups_on_timestamp() -> None:
    gh = _CommentingGithub(logs=[_log("CodeQL")])
    record = _record_for_triage()
    now = datetime.now(tz=UTC)

    posted_first = _post_security_triage_comment(record=record, github=gh, dry_run=False, now=now)
    assert posted_first is True
    assert len(gh.comments) == 1
    assert record.security_triage_comment_posted_at == now

    # Second tick of the sweep — same record, dedup must hold.
    posted_second = _post_security_triage_comment(
        record=record,
        github=gh,
        dry_run=False,
        now=datetime.now(tz=UTC),
    )
    assert posted_second is False
    assert len(gh.comments) == 1  # NOT re-posted


def test_post_triage_noop_on_dry_run() -> None:
    gh = _CommentingGithub(logs=[_log("CodeQL")])
    record = _record_for_triage()
    posted = _post_security_triage_comment(
        record=record,
        github=gh,
        dry_run=True,
        now=datetime.now(tz=UTC),
    )
    assert posted is False
    assert gh.comments == []
    assert record.security_triage_comment_posted_at is None


def test_post_triage_noop_without_pr_number() -> None:
    gh = _CommentingGithub(logs=[_log("CodeQL")])
    record = _record_for_triage(pr_number=None)
    posted = _post_security_triage_comment(
        record=record,
        github=gh,
        dry_run=False,
        now=datetime.now(tz=UTC),
    )
    assert posted is False
    assert gh.comments == []


def test_post_triage_swallows_github_errors_gracefully() -> None:
    """A GitHub failure when fetching logs falls through to a minimal-but-
    still-truthful comment + timestamp set, so the sweep never spins on it."""
    gh = _CommentingGithub()  # no logs configured
    gh._raises = True  # force get_pr_failing_check_logs to throw

    record = _record_for_triage()
    now = datetime.now(tz=UTC)
    posted = _post_security_triage_comment(
        record=record,
        github=gh,
        dry_run=False,
        now=now,
    )
    assert posted is True
    assert len(gh.comments) == 1
    body = gh.comments[0][1]
    assert "Security triage" in body
    assert record.security_triage_comment_posted_at == now
