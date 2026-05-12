"""Tests for the auto-execute-approved sweep."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from minions.approval.store import DecisionStore
from minions.budget import BudgetBreachError, BudgetState
from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.manifest import load_active_manifests
from minions.scheduled.execute_approved import run_execute_approved

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


def _decision(
    project: str, summary: str = "Real plan", status: DecisionStatus = DecisionStatus.APPROVED
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
