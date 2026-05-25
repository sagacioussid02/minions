from pathlib import Path

from minions.crews.planning import _infer_risk, run_planning_crew
from minions.models.decision import DecisionStatus, DecisionType
from minions.models.manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_dry_run_returns_pending_decision():
    manifest = load_manifest(REPO_ROOT / "projects" / "demo.yaml")
    decision = run_planning_crew(manifest, dry_run=True)
    assert decision.project == "Demo"
    assert decision.type is DecisionType.FEATURE
    assert decision.status is DecisionStatus.PENDING
    assert "DRY RUN" in decision.summary
    assert decision.diff_or_plan is not None
    assert decision.proposer_role == "manager"


def test_dry_run_assigns_low_risk():
    manifest = load_manifest(REPO_ROOT / "projects" / "demo_three.yaml")
    decision = run_planning_crew(manifest, dry_run=True)
    assert decision.risk == "low"


def test_infer_risk_high():
    assert _infer_risk("Plan...\nRisk: high") == "high"


def test_infer_risk_medium():
    assert _infer_risk("...risk score: medium ...") == "medium"


def test_infer_risk_default_low():
    assert _infer_risk("Plan with no risk declaration") == "low"


def test_dry_run_with_profile_includes_grounding_signals(tmp_path: Path):
    """Profile context should be threaded into the dry-run plan text."""
    from minions.models.manifest import Manifest
    from minions.onboarding.profile import (
        PackageFile,
        ProjectProfile,
        TasksMdSummary,
    )

    manifest = Manifest.model_validate(
        {
            "name": "demo",
            "description": "test",
            "source": {"kind": "local", "path": str(tmp_path), "default_branch": "main"},
            "weekly_budget_usd": 1.0,
            "monthly_budget_usd": 4.0,
            "owner": "owner@example.com",
        }
    )
    profile = ProjectProfile(
        project="demo",
        source_kind="local",
        source_path=str(tmp_path),
        languages={"ts": 5},
        package_files=[PackageFile(path="package.json", kind="npm", dep_count=27)],
        tasks_md=TasksMdSummary(path="openspec/tasks.md", total=71, done=19, remaining=52),
        todo_count=3,
    )
    decision = run_planning_crew(manifest, dry_run=True, profile=profile)
    assert decision.diff_or_plan is not None
    plan = decision.diff_or_plan
    assert "Grounding signals" in plan
    assert "52 items remaining" in plan
    assert "npm" in plan
    assert "3 TODO/FIXME" in plan
