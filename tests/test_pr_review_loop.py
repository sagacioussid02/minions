"""Tests for the PR review-loop sweep."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from minions.approval.store import DecisionStore
from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunRecord, EngineerRunStore
from minions.github.client import GitHubError
from minions.github.models import PullRequest
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.scheduled.pr_review_loop import run_pr_review_loop

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


class _FakeGH:
    def __init__(
        self,
        ci_for: dict[int, tuple[str | None, str | None]],
        *,
        merge_error: GitHubError | None = None,
        merge_result: bool = True,
        merge_state_for: dict[int, str | None] | None = None,
        merged_prs: set[int] | None = None,
    ) -> None:
        self.ci_for = ci_for
        self.comments: list[tuple[int, str]] = []
        self.merge_error = merge_error
        self.merge_result = merge_result
        self.merge_calls: list[int] = []
        self.merge_state_for = merge_state_for or {}
        self.merged_prs = merged_prs or set()
        self.closed_prs: list[int] = []

    def __enter__(self) -> _FakeGH:
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def get_pr_check_status(self, number: int) -> tuple[str | None, str | None]:
        return self.ci_for.get(number, (None, None))

    def get_pr_merge_state(self, number: int) -> str | None:
        return self.merge_state_for.get(number, "clean")

    def get_pull_request(self, number: int) -> PullRequest:
        return PullRequest(
            number=number,
            title=f"PR {number}",
            state="open",
            head=f"branch-{number}",
            base="main",
            draft=False,
            html_url=f"https://github.com/x/Demo/pull/{number}",
            merged=number in self.merged_prs,
        )

    def close_pull_request(self, *, number: int) -> PullRequest:
        self.closed_prs.append(number)
        return PullRequest(
            number=number,
            title=f"PR {number}",
            state="closed",
            head=f"branch-{number}",
            base="main",
            draft=False,
            html_url=f"https://github.com/x/Demo/pull/{number}",
        )

    def comment_on_pull_request(self, *, number: int, body: str) -> None:
        self.comments.append((number, body))

    def merge_pull_request(self, *, number: int, **_: Any) -> bool:
        self.merge_calls.append(number)
        if self.merge_error is not None:
            raise self.merge_error
        return self.merge_result


def _decision(**kwargs: Any) -> Decision:
    base = {
        "project": "Demo",
        "type": DecisionType.FEATURE,
        "summary": "Add a crew loop",
        "rationale": "Investors should see the team reviewing its work.",
        "diff_or_plan": "Open a PR and run crew review.",
        "proposer_role": "manager",
        "proposer_agent_id": "manager@Demo",
        "risk": "low",
    }
    base.update(kwargs)
    return Decision(**base)


def _run_record(decision_id: str, *, pr_number: int = 7) -> EngineerRunRecord:
    return EngineerRunRecord(
        decision_id=decision_id,
        project="Demo",
        completed_at=datetime.now(tz=UTC),
        pr_url=f"https://github.com/x/Demo/pull/{pr_number}",
        pr_number=pr_number,
        branch_name="minions/eng/crew-loop",
        files_changed=["src/app.py", "tests/test_app.py"],
        pr_state="open",
    )


def _save_record(store: EngineerRunStore, record: EngineerRunRecord) -> None:
    store.save(
        EngineerResult(
            decision_id=record.decision_id,
            pr_url=record.pr_url,
            pr_number=record.pr_number,
            branch_name=record.branch_name,
            files_changed=record.files_changed,
            dry_run=False,
        ),
        project=record.project,
    )


def test_pr_review_loop_assigns_reviewers_and_posts_comments(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    decision = _decision()
    decisions.save(decision)
    _save_record(runs, _run_record(str(decision.id), pr_number=7))

    gh = _FakeGH({7: ("success", "https://ci/7")})

    report = run_pr_review_loop(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=lambda _m: gh,
    )

    assert report.merged == 1
    assert report.outcomes[0].comments_posted == 2
    assert len(gh.comments) == 3  # assignment + TTL + QA
    assert "Crew review started" in gh.comments[0][1]
    assert "Tech Team Lead review" in gh.comments[1][1]
    assert "QA Engineer review" in gh.comments[2][1]

    after = runs.get(str(decision.id))
    assert after is not None
    assert after.review_status == "merged"
    assert after.pr_state == "merged"
    assert [r.role for r in after.reviewers] == ["ttl", "qa_engineer"]
    assert all(r.comment_posted_at is not None for r in after.reviewers)
    assert all(r.verdict == "approve" for r in after.reviewers)
    assert after.crew_approved_at is not None
    assert after.merge_attempted_at is not None
    assert gh.merge_calls == [7]


def test_pr_review_loop_records_changes_requested_on_failed_ci(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    decision = _decision()
    decisions.save(decision)
    _save_record(runs, _run_record(str(decision.id), pr_number=9))

    gh = _FakeGH({9: ("failure", "https://ci/9")})

    report = run_pr_review_loop(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=lambda _m: gh,
    )

    # PR 2 contract: review_round bumps, NO sibling fix Decision is filed.
    # The owner sweep handles the in-place re-dispatch on next tick.
    assert report.creator_responded == 1
    after = runs.get(str(decision.id))
    assert after is not None
    assert after.review_status == "changes_requested"
    assert after.review_round == 1
    assert after.creator_response_posted_at is not None
    assert {r.verdict for r in after.reviewers} == {"request_changes"}
    # Outcome carries no sibling Decision id any more.
    assert report.outcomes[0].fix_decision_id is None
    # Zero approved Decisions filed by the review loop.
    assert decisions.list_by_status(DecisionStatus.APPROVED) == []


def test_pr_review_loop_does_not_file_new_decision_even_if_legacy_one_exists(
    tmp_path: Path,
) -> None:
    """A pre-existing legacy ``pr_followup`` Decision in the store from before
    PR 2 shipped must NOT cause the review loop to file a fresh one. Idempotency
    guard for the rollout window.
    """
    decisions = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    decision = _decision()
    decisions.save(decision)
    _save_record(runs, _run_record(str(decision.id), pr_number=9))
    legacy = _decision(
        type=DecisionType.BUG,
        summary="Fix CI failure on PR #9 (Demo)",
        proposer_role="pr_followup",
        proposer_agent_id="pr_followup@Demo",
        status=DecisionStatus.APPROVED,
    )
    decisions.save(legacy)

    gh = _FakeGH({9: ("failure", "https://ci/9")})

    run_pr_review_loop(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=lambda _m: gh,
    )

    # Only the legacy row remains; no new Decisions filed.
    fixes = decisions.list_by_status(DecisionStatus.APPROVED)
    assert [f.id for f in fixes] == [legacy.id]


def test_pr_review_loop_does_not_double_comment(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    decision = _decision()
    decisions.save(decision)
    _save_record(runs, _run_record(str(decision.id), pr_number=11))

    gh = _FakeGH({11: ("success", None)})

    run_pr_review_loop(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=lambda _m: gh,
    )
    run_pr_review_loop(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=lambda _m: gh,
    )

    assert len(gh.comments) == 3
    assert gh.merge_calls == [11]


def test_pr_review_loop_adds_security_reviewer_for_high_risk(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    decision = _decision(risk="high")
    decisions.save(decision)
    _save_record(runs, _run_record(str(decision.id), pr_number=12))

    gh = _FakeGH({12: ("success", None)})

    run_pr_review_loop(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=lambda _m: gh,
    )

    after = runs.get(str(decision.id))
    assert after is not None
    assert [r.role for r in after.reviewers] == [
        "ttl",
        "qa_engineer",
        "security_champion",
    ]


def test_pr_review_loop_hands_off_when_branch_protection_blocks_merge(
    tmp_path: Path,
) -> None:
    decisions = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    decision = _decision()
    decisions.save(decision)
    _save_record(runs, _run_record(str(decision.id), pr_number=14))

    gh = _FakeGH(
        {14: ("success", None)},
        merge_error=GitHubError("Required approving review", status_code=405),
    )

    report = run_pr_review_loop(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=lambda _m: gh,
    )

    assert report.handoff == 1
    after = runs.get(str(decision.id))
    assert after is not None
    assert after.review_status == "merge_blocked"
    assert after.merge_attempted_at is not None
    assert after.human_handoff_posted_at is not None
    assert "Required approving review" in (after.merge_blocked_reason or "")
    assert "Ready for operator merge" in gh.comments[-1][1]


def test_pr_review_loop_closes_original_when_linked_followup_merged(
    tmp_path: Path,
) -> None:
    decisions = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    original = _decision()
    decisions.save(original)
    _save_record(runs, _run_record(str(original.id), pr_number=51))

    followup = _decision(
        type=DecisionType.BUG,
        summary="Fix CI failure on PR #51 (Demo)",
        rationale="Creator response for https://github.com/x/Demo/pull/51.",
        diff_or_plan="Linked follow-up for https://github.com/x/Demo/pull/51.",
        proposer_role="creator_response",
        proposer_agent_id="creator_response@Demo",
        status=DecisionStatus.EXECUTED,
    )
    decisions.save(followup)
    _save_record(runs, _run_record(str(followup.id), pr_number=52))

    gh = _FakeGH({51: ("success", None), 52: ("success", None)}, merged_prs={52})

    report = run_pr_review_loop(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=lambda _m: gh,
    )

    assert report.superseded == 1
    after = runs.get(str(original.id))
    assert after is not None
    assert after.review_status == "superseded"
    assert after.pr_state == "closed"
    assert after.superseded_by_pr_url == "https://github.com/x/Demo/pull/52"
    assert gh.closed_prs == [51]
    assert "Original PR superseded" in gh.comments[0][1]


def test_pr_review_loop_marks_dirty_pr_for_owner_sweep(
    tmp_path: Path,
) -> None:
    """pr-ownership-sweep Phase 4: dirty merge state no longer files a
    conflict-resolution Decision. It just stamps review_status +
    conflict_resolution_queued_at on the record so the next pr_owner_sweep
    tick re-dispatches the original owner agent in-place."""
    decisions = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    decision = _decision()
    decisions.save(decision)
    _save_record(runs, _run_record(str(decision.id), pr_number=51))

    gh = _FakeGH({51: ("success", None)}, merge_state_for={51: "dirty"})

    report = run_pr_review_loop(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=lambda _m: gh,
    )

    assert report.conflict_queued == 1
    after = runs.get(str(decision.id))
    assert after is not None
    assert after.review_status == "conflict_queued"
    assert after.conflict_resolution_queued_at is not None

    # Zero new Decisions filed — owner sweep handles it.
    conflicts = [
        d
        for d in decisions.list_by_status(DecisionStatus.APPROVED)
        if d.proposer_role == "conflict_resolution"
    ]
    assert conflicts == []
    # No comment posted by review loop — owner sweep posts its own.
    assert gh.comments == []
