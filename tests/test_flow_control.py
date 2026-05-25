"""Tests for src/minions/crews/flow_control.py + the cap wiring.

Covers:

* distinct_open_pr_count collapses duplicate ``pr_number`` rows into one
  count (the bug that let the demo_three loop run away).
* has_open_fix_decision_for_pr matches the pr_followup-style fix Decision
  via extras AND falls back to the summary substring.
* execute_approved respects ``manifest.flow_control.max_open_prs`` and
  throttles fresh-PR decisions when the project is at the cap.
* execute_approved does NOT throttle in-place fix decisions (existing
  branch supplied) even when the project is at the cap.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from minions.approval.store import DecisionStore
from minions.crews.engineer_runs_store import EngineerRunRecord, EngineerRunStore
from minions.crews.flow_control import (
    distinct_open_pr_count,
    has_open_fix_decision_for_pr,
)
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.manifest import FlowControl, load_manifest
from minions.scheduled.execute_approved import run_execute_approved

REPO_ROOT = Path(__file__).resolve().parents[1]


# --------------------------- distinct_open_pr_count -------------------------


def _run(decision_id: str, *, pr_number: int | None,
         pr_state: str | None, project: str = "p") -> EngineerRunRecord:
    return EngineerRunRecord(
        decision_id=decision_id,
        project=project,
        completed_at=datetime.now(UTC),
        pr_url=f"https://x/{project}/{pr_number}" if pr_number else None,
        pr_number=pr_number,
        pr_state=pr_state,
        branch_name=f"minions/eng/x-{decision_id[:6]}",
    )


def test_distinct_open_pr_count_collapses_duplicate_pr_numbers(tmp_path: Path) -> None:
    runs = EngineerRunStore(tmp_path / "runs.json")
    # Three rows, same PR #42 — the runaway pattern.
    runs.update(_run("a", pr_number=42, pr_state="open"))
    runs.update(_run("b", pr_number=42, pr_state=None))  # unsynced — treated as open
    runs.update(_run("c", pr_number=42, pr_state="open"))
    # An unrelated open PR.
    runs.update(_run("d", pr_number=43, pr_state="open"))
    # Closed PR — excluded.
    runs.update(_run("e", pr_number=99, pr_state="closed"))

    assert distinct_open_pr_count(project="p", engineer_runs_store=runs) == 2


def test_distinct_open_pr_count_skips_rows_without_pr_number(tmp_path: Path) -> None:
    runs = EngineerRunStore(tmp_path / "runs.json")
    runs.update(_run("a", pr_number=None, pr_state=None))  # skipped run
    runs.update(_run("b", pr_number=10, pr_state="open"))
    assert distinct_open_pr_count(project="p", engineer_runs_store=runs) == 1


def test_distinct_open_pr_count_filters_by_project(tmp_path: Path) -> None:
    runs = EngineerRunStore(tmp_path / "runs.json")
    runs.update(_run("a", pr_number=1, pr_state="open", project="p1"))
    runs.update(_run("b", pr_number=2, pr_state="open", project="p2"))
    assert distinct_open_pr_count(project="p1", engineer_runs_store=runs) == 1


# --------------------------- has_open_fix_decision_for_pr -------------------


def _fix_decision(project: str, *, pr_number: int,
                  status: DecisionStatus = DecisionStatus.PENDING) -> Decision:
    d = Decision(
        project=project,
        type=DecisionType.BUG,
        summary=f"Fix CI failure on PR #{pr_number} ({project})",
        rationale="x",
        proposer_role="pr_followup",
        proposer_agent_id=f"pr_followup@{project}",
        status=status,
    )
    d.__pydantic_extra__ = {
        "existing_pr_number": pr_number,
        "existing_pr_branch": f"minions/eng/x-{pr_number}",
        "retry_attempt": 1,
    }
    return d


def test_open_fix_decision_matches_via_extras(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "dec.json")
    store.save(_fix_decision("demo_three", pr_number=42))
    assert has_open_fix_decision_for_pr(
        project="demo_three", pr_number=42, store=store
    )
    assert not has_open_fix_decision_for_pr(
        project="demo_three", pr_number=99, store=store
    )


def test_open_fix_decision_ignores_resolved_decisions(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "dec.json")
    store.save(_fix_decision("demo_three", pr_number=42, status=DecisionStatus.REJECTED))
    store.save(_fix_decision("demo_three", pr_number=42, status=DecisionStatus.EXECUTED))
    assert not has_open_fix_decision_for_pr(
        project="demo_three", pr_number=42, store=store
    )


def test_open_fix_decision_ignores_other_projects(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "dec.json")
    store.save(_fix_decision("demo_three", pr_number=42))
    assert not has_open_fix_decision_for_pr(
        project="other", pr_number=42, store=store
    )


# --------------------------- manifest default + override --------------------


def test_flow_control_default_is_five() -> None:
    assert FlowControl().max_open_prs == 5


def test_flow_control_override_from_manifest(tmp_path: Path) -> None:
    src = REPO_ROOT / "projects" / "Demo.yaml"
    data = yaml.safe_load(src.read_text())
    data["flow_control"] = {"max_open_prs": 2}
    out = tmp_path / "x.yaml"
    out.write_text(yaml.safe_dump(data))
    m = load_manifest(out)
    assert m.flow_control.max_open_prs == 2


# --------------------------- execute_approved cap enforcement ---------------


def _fake_github(_manifest: Any) -> Any:
    class _C:
        def __enter__(self) -> _C:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

    return _C()


def _sprint_decision(project: str = "Demo") -> Decision:
    return Decision(
        project=project,
        type=DecisionType.FEATURE,
        summary="Sprint proposal",
        rationale="r",
        diff_or_plan="plan",
        proposer_role="manager",
        proposer_agent_id=f"manager@{project}",
        status=DecisionStatus.APPROVED,
    )


def test_execute_approved_throttles_when_cap_reached(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    # Seed 5 open PRs to hit the default cap.
    for i in range(5):
        runs.update(_run(f"r{i}", pr_number=100 + i, pr_state="open", project="Demo"))
    decision = _sprint_decision()
    decisions.save(decision)

    def runner(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("runner must not be called when cap reached")

    report = run_execute_approved(
        projects_dir=REPO_ROOT / "projects",
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=_fake_github,
        dry_run=False,
        runner=runner,
    )
    assert report.outcomes[0].status == "throttled"
    assert "open_pr_cap" in (report.outcomes[0].reason or "")


def test_execute_approved_allows_in_place_fix_at_cap(tmp_path: Path) -> None:
    """In-place fix decisions don't open new PRs, so they bypass the cap."""
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    for i in range(5):
        runs.update(_run(f"r{i}", pr_number=100 + i, pr_state="open", project="Demo"))

    fix = _fix_decision("Demo", pr_number=100, status=DecisionStatus.APPROVED)
    decisions.save(fix)

    seen: dict[str, bool] = {}

    def runner(_d: Decision, _m: Any, **_k: Any) -> Any:
        seen["called"] = True
        from minions.crews.engineer import EngineerResult

        return EngineerResult(
            decision_id=str(_d.id), pr_url="https://x/p/100",
            pr_number=100, dry_run=False,
        )

    report = run_execute_approved(
        projects_dir=REPO_ROOT / "projects",
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=_fake_github,
        dry_run=False,
        runner=runner,
    )
    assert seen.get("called")
    assert report.executed == 1
