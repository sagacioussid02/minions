"""Tests for the auto-execute-approved sweep."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from minions.approval.service import resolve
from minions.approval.store import DecisionStore
from minions.budget import BudgetBreachError, BudgetState
from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.models.decision import Decision, DecisionPriority, DecisionStatus, DecisionType
from minions.models.manifest import load_active_manifests
from minions.models.task import Task
from minions.scheduled.execute_approved import run_execute_approved
from minions.tasks.store import TaskStore

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


def _decision(
    project: str,
    summary: str = "Real plan",
    status: DecisionStatus = DecisionStatus.APPROVED,
    priority: DecisionPriority = "p3",
    expedited: bool = False,
) -> Decision:
    return Decision(
        project=project,
        type=DecisionType.FEATURE,
        summary=summary,
        rationale="r",
        diff_or_plan="plan body",
        proposer_role="manager",
        proposer_agent_id=f"manager@{project}",
        proposer_display_name="m",
        status=status,
        priority=priority,
        expedited=expedited,
    )


def _fake_github_client(_manifest: Any) -> Any:
    class _FakeClient:
        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

    return _FakeClient()


def _success_runner(pr_url: str = "https://example/pr/1"):
    def runner(decision: Decision, manifest: Any, **_kw: Any) -> EngineerResult:
        return EngineerResult(
            decision_id=str(decision.id),
            pr_url=pr_url,
            pr_number=1,
            branch_name="minions/eng/x",
            files_changed=["README.md"],
            files_rejected=[],
            dry_run=False,
        )

    return runner


def test_skips_dry_run_decisions(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")

    d = _decision("Demo", summary="[DRY RUN] Sprint proposal for Demo")
    store.save(d)

    calls: list[str] = []

    def runner(decision: Decision, *_a: Any, **_k: Any) -> EngineerResult:
        calls.append(str(decision.id))
        return EngineerResult(decision_id=str(decision.id), pr_url="x", dry_run=False)

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=False,
        runner=runner,
    )

    assert calls == []  # runner never invoked
    assert report.executed == 0
    assert report.skipped == 1
    assert "dry-run" in (report.outcomes[0].reason or "")


def test_skips_decisions_that_already_have_engineer_run(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")

    d = _decision("Demo")
    store.save(d)
    # Pre-seed an engineer run for this decision.
    prior = EngineerResult(decision_id=str(d.id), pr_url="https://x", dry_run=False)
    runs.save(prior, project="Demo")

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=False,
        runner=_success_runner(),
    )

    assert report.executed == 0
    assert report.skipped == 1
    assert "already" in (report.outcomes[0].reason or "")


def test_executes_marks_decision_and_persists_run(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")

    d = _decision("Demo")
    store.save(d)

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=False,
        runner=_success_runner("https://example/pr/7"),
    )

    assert report.executed == 1
    after = store.get(d.id)
    assert after is not None
    assert after.status is DecisionStatus.EXECUTED
    assert after.pr_url == "https://example/pr/7"
    assert runs.get(str(d.id)) is not None


def test_dry_run_does_not_persist_run_or_mark_executed(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")

    d = _decision("Demo")
    store.save(d)

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=True,
        runner=_success_runner("https://example/pr/7"),
    )

    assert report.executed == 1
    after = store.get(d.id)
    assert after is not None
    assert after.status is DecisionStatus.APPROVED
    assert after.pr_url is None
    assert runs.get(str(d.id)) is None


def test_caps_at_max_runs(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")

    manifests = load_active_manifests(PROJECTS_DIR)
    projects = list(manifests.keys())[:3]
    assert len(projects) >= 3, "needs at least 3 active projects to exercise the cap"

    for p in projects:
        store.save(_decision(p))

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=False,
        runner=_success_runner(),
        max_runs=2,
    )

    assert report.executed == 2
    assert report.capped is True


def test_budget_breach_is_per_decision_not_fatal(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")

    manifests = load_active_manifests(PROJECTS_DIR)
    p1, p2 = list(manifests.keys())[:2]
    store.save(_decision(p1, summary="First — will throttle"))
    store.save(_decision(p2, summary="Second — should still run"))

    calls: list[str] = []

    def runner(decision: Decision, *_a: Any, **_k: Any) -> EngineerResult:
        calls.append(decision.project)
        if decision.project == p1:
            raise BudgetBreachError(
                BudgetState(
                    project=p1,
                    monthly_cap_usd=10.0,
                    month_to_date_usd=10.5,
                    fraction=1.05,
                    state="breach",
                )
            )
        return EngineerResult(decision_id=str(decision.id), pr_url="https://ok", dry_run=False)

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=False,
        runner=runner,
    )

    assert report.executed == 1
    assert report.throttled == 1
    assert calls == [p1, p2]  # second decision still attempted after first throttled


def test_expedited_leadership_decisions_run_before_fifo_backlog(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")

    manifests = load_active_manifests(PROJECTS_DIR)
    project = list(manifests.keys())[0]
    older = _decision(project, summary="Older normal work")
    urgent = _decision(
        project,
        summary="Leadership deployment investigation",
        priority="p1",
        expedited=True,
    )
    store.save(older)
    store.save(urgent)
    calls: list[str] = []

    def runner(decision: Decision, *_a: Any, **_k: Any) -> EngineerResult:
        calls.append(decision.summary)
        return EngineerResult(decision_id=str(decision.id), pr_url="https://ok", dry_run=False)

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=False,
        runner=runner,
        max_runs=1,
    )

    assert report.executed == 1
    assert calls == ["Leadership deployment investigation"]


def test_only_expedited_skips_non_expedited_backlog(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")

    manifests = load_active_manifests(PROJECTS_DIR)
    project = list(manifests.keys())[0]
    backlog = _decision(project, summary="Normal backlog work")
    fast = _decision(project, summary="CTO investigation", priority="p1", expedited=True)
    store.save(backlog)
    store.save(fast)
    calls: list[str] = []

    def runner(decision: Decision, *_a: Any, **_k: Any) -> EngineerResult:
        calls.append(decision.summary)
        return EngineerResult(decision_id=str(decision.id), pr_url="https://ok", dry_run=False)

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=False,
        runner=runner,
        only_expedited=True,
    )

    assert report.executed == 1
    assert calls == ["CTO investigation"]
    # backlog Decision remains APPROVED — not skipped via outcome list, just filtered out
    assert store.get(backlog.id).status is DecisionStatus.APPROVED  # type: ignore[union-attr]


def test_task_aware_execute_processes_one_queued_task(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")
    tasks = TaskStore(tmp_path / "tasks.json")

    decision = _decision("Demo", summary="Sprint proposal for Demo")
    store.save(decision)
    first = tasks.save(_task(decision, "Add onboarding copy"))
    second = tasks.save(_task(decision, "Add pricing copy"))
    calls: list[str | None] = []

    def runner(decision: Decision, *_a: Any, task: Task | None = None, **_k: Any) -> EngineerResult:
        calls.append(str(task.id) if task else None)
        return EngineerResult(
            decision_id=str(decision.id),
            task_id=str(task.id) if task else None,
            pr_url="https://example/pr/12",
            pr_number=12,
            branch_name="minions/eng/task",
            files_changed=["README.md"],
            dry_run=False,
        )

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=False,
        runner=runner,
        task_store=tasks,
    )

    assert report.executed == 1
    assert calls == [str(first.id)]
    first_after = tasks.get(first.id)
    second_after = tasks.get(second.id)
    assert first_after is not None
    assert second_after is not None
    assert first_after.status == "review"
    assert first_after.pr_url == "https://example/pr/12"
    assert first_after.pr_number == 12
    assert second_after.status == "queued"
    assert runs.get(str(decision.id)) is not None
    assert runs.get(str(decision.id)).task_id == str(first.id)  # type: ignore[union-attr]
    assert store.get(decision.id).status is DecisionStatus.APPROVED  # type: ignore[union-attr]


def test_reject_decision_cancels_refined_tasks(tmp_path: Path) -> None:
    from minions.notify.base import Notifier

    class NoopNotifier(Notifier):
        def notify_approval_request(self, decision: Decision) -> None:
            return None

        def notify_decision_resolved(self, decision: Decision) -> None:
            return None

        def notify_text(self, *, subject: str, body: str) -> None:
            return None

    store = DecisionStore(tmp_path / "decisions.json")
    tasks = TaskStore(tmp_path / "tasks.json")
    decision = _decision("Demo", status=DecisionStatus.PENDING)
    store.save(decision)
    task = tasks.save(_task(decision, "Cancel me"))

    resolve(
        decision.id,
        store=store,
        notifier=NoopNotifier(),
        action="reject",
        task_store=tasks,
    )

    assert tasks.get(task.id).status == "cancelled"  # type: ignore[union-attr]


def _task(decision: Decision, title: str) -> Task:
    return Task(
        decision_id=decision.id,
        project=decision.project,
        sprint_number=decision.sprint_number,
        category="feature",
        title=title,
        description=f"Implement {title}",
        acceptance_criteria="Visible in the app",
        owner_role="engineer",
        owner_agent_id=f"engineer@{decision.project}",
        owner_display_name="Eli",
    )


def test_in_place_fix_fields_pass_through_to_runner(tmp_path: Path) -> None:
    """A fix Decision stamped with existing_pr_branch + existing_pr_number
    is threaded into run_engineer_crew kwargs so the engineer commits
    on the original branch instead of opening a new PR."""
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")

    manifests = load_active_manifests(PROJECTS_DIR)
    project = list(manifests.keys())[0]
    fix = _decision(project, summary="Fix CI failure on PR #99")
    # Stamp the in-place fields directly into the model (extra='allow').
    fix.__pydantic_extra__["existing_pr_number"] = 99  # type: ignore[index]
    fix.__pydantic_extra__["existing_pr_branch"] = "minions/eng/some-feature"  # type: ignore[index]
    fix.__pydantic_extra__["retry_attempt"] = 2  # type: ignore[index]
    store.save(fix)

    captured: dict[str, Any] = {}

    def runner(decision: Decision, manifest: Any, **kw: Any) -> EngineerResult:
        captured.update(kw)
        return EngineerResult(
            decision_id=str(decision.id),
            pr_url="https://github.com/x/repo/pull/99",
            pr_number=99,
            dry_run=False,
        )

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=False,
        runner=runner,
        max_runs=1,
    )

    assert report.executed == 1
    assert captured.get("target_branch") == "minions/eng/some-feature"
    assert captured.get("existing_pr_number") == 99
    assert captured.get("retry_attempt") == 2


def test_fresh_pr_decision_has_no_in_place_fields(tmp_path: Path) -> None:
    """A regular sprint Decision without in-place fields passes None kwargs
    so the engineer crew takes the fresh-PR path."""
    store = DecisionStore(tmp_path / "decisions.json")
    runs = EngineerRunStore(tmp_path / "engineer_runs.json")

    manifests = load_active_manifests(PROJECTS_DIR)
    project = list(manifests.keys())[0]
    d = _decision(project, summary="Sprint proposal for X")
    store.save(d)

    captured: dict[str, Any] = {}

    def runner(decision: Decision, manifest: Any, **kw: Any) -> EngineerResult:
        captured.update(kw)
        return EngineerResult(decision_id=str(decision.id), pr_url="https://x", dry_run=False)

    run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=store,
        engineer_runs_store=runs,
        open_github_client=_fake_github_client,
        dry_run=False,
        runner=runner,
        max_runs=1,
    )

    assert captured.get("target_branch") is None
    assert captured.get("existing_pr_number") is None
    assert captured.get("retry_attempt") == 0
