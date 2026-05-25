"""Refinement crew + agent naming tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from minions.agents.naming import (
    list_all,
    resolve_display_name,
    set_display_name,
)
from minions.crews.refinement import refine_decision
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.sprint_plan import PlanItem, StructuredSprintPlan
from minions.tasks.store import TaskStore


def _plan() -> StructuredSprintPlan:
    return StructuredSprintPlan(
        goal="Ship audit log v2",
        features=[
            PlanItem(
                title="Audit search endpoint",
                rationale="ops asked",
                acceptance_criteria="paginated",
                estimated_effort="m",
                suggested_owner_role="engineer",
            ),
            PlanItem(title="Per-user drilldown", estimated_effort="l"),  # no suggested owner
        ],
        bugs=[
            PlanItem(title="Cron noise", estimated_effort="s", suggested_owner_role="cloud_devops"),
        ],
        ops=[
            PlanItem(title="Promote staging", estimated_effort="m"),  # default: cloud_devops
        ],
        docs=[
            PlanItem(
                title="Update runbook", estimated_effort="xs"
            ),  # default: documentation_engineer
        ],
    )


def _approved_sprint_decision(plan: StructuredSprintPlan, project: str = "Demo") -> Decision:
    return Decision(
        project=project,
        type=DecisionType.FEATURE,
        summary=f"Sprint 0 proposal for {project}",
        rationale="x",
        proposer_role="manager",
        proposer_agent_id=f"manager@{project}",
        status=DecisionStatus.APPROVED,
        sprint_number=0,
        structured_plan=plan,
    )


def test_refinement_emits_one_task_per_plan_item(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    decision = _approved_sprint_decision(_plan())
    tasks = refine_decision(decision, task_store=store)
    assert len(tasks) == 5  # 2 features + 1 bug + 1 ops + 1 docs
    assert {t.category for t in tasks} == {"feature", "bug", "ops", "docs"}
    # Sprint number propagates
    assert all(t.sprint_number == 0 for t in tasks)
    # Project propagates
    assert all(t.project == "Demo" for t in tasks)


def test_owner_resolution_prefers_suggested_role(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    decision = _approved_sprint_decision(_plan())
    tasks = refine_decision(decision, task_store=store)
    by_title = {t.title: t for t in tasks}
    # suggested: engineer → first engineer seat on Demo (lexicographic
    # tie-break with all seats at load 0)
    assert by_title["Audit search endpoint"].owner_agent_id == "engineer@Demo"
    # suggested: cloud_devops → cloud_devops@shared (shared, single-seat)
    assert by_title["Cron noise"].owner_agent_id == "cloud_devops@shared"
    # no suggested → category default (feature → engineer). Round-robin
    # picks the next free engineer seat (post-Phase-C multi-seat).
    assert by_title["Per-user drilldown"].owner_agent_id in {
        "engineer@Demo",
        "engineer@Demo#1",
        "engineer@Demo#2",
    }
    # ops default
    assert by_title["Promote staging"].owner_agent_id == "cloud_devops@shared"
    # docs default
    assert by_title["Update runbook"].owner_agent_id == "documentation_engineer@shared"


def test_owner_display_name_from_registry(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    decision = _approved_sprint_decision(_plan())
    tasks = refine_decision(decision, task_store=store)
    sasha = next(t for t in tasks if t.owner_agent_id == "engineer@Demo")
    # Seed registry maps engineer@Demo → "Sasha"
    assert sasha.owner_display_name == "Sasha"


def test_refinement_is_idempotent(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    decision = _approved_sprint_decision(_plan())
    first = refine_decision(decision, task_store=store)
    second = refine_decision(decision, task_store=store)
    # Second call returns the existing tasks without creating duplicates.
    assert len(second) == len(first)
    assert {t.id for t in second} == {t.id for t in first}
    assert len(store.list_all()) == len(first)


def test_naming_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "names.yaml"
    assert resolve_display_name("engineer@Demo", path=path) == "Engineer"  # fallback prettyRole
    set_display_name("engineer@Demo", "Sasha", path=path)
    assert resolve_display_name("engineer@Demo", path=path) == "Sasha"


def test_naming_shared_fallback_for_missing_per_project(tmp_path: Path) -> None:
    path = tmp_path / "names.yaml"
    set_display_name("cloud_devops@shared", "Milo", path=path)
    # Per-project agent that's missing falls back to the shared name (not prettyRole)
    assert resolve_display_name("cloud_devops@Demo", path=path) == "Milo"


def test_naming_rejects_bad_input(tmp_path: Path) -> None:
    path = tmp_path / "names.yaml"
    with pytest.raises(ValueError):
        set_display_name("no_at_sign", "X", path=path)
    with pytest.raises(ValueError):
        set_display_name("role@project", "", path=path)


def test_naming_list_all(tmp_path: Path) -> None:
    path = tmp_path / "names.yaml"
    set_display_name("engineer@Demo", "Sasha", path=path)
    set_display_name("manager@Demo", "Mira", path=path)
    names = list_all(path=path)
    assert names == {"engineer@Demo": "Sasha", "manager@Demo": "Mira"}


# -- Phase B: subtask expansion ------------------------------------------------


def test_subtasks_expand_to_one_task_each(tmp_path: Path) -> None:
    store = TaskStore(tmp_path / "tasks.json")
    plan = StructuredSprintPlan(
        goal="Big feature",
        features=[
            PlanItem(
                title="Calculate Stripe totals server-side",
                estimated_effort="l",
                subtasks=[
                    PlanItem(title="Move multiplication to checkout service", estimated_effort="s"),
                    PlanItem(title="Add unit tests for currency edge cases", estimated_effort="s"),
                    PlanItem(title="Add Stripe-test-key integration test", estimated_effort="s"),
                ],
            ),
        ],
    )
    decision = _approved_sprint_decision(plan)
    tasks = refine_decision(decision, task_store=store)
    assert len(tasks) == 3
    titles = [t.title for t in tasks]
    assert all(t.startswith("[Calculate Stripe totals server-side] ") for t in titles)
    # Parent linkage preserved on Task.payload for the UI drawer.
    assert all(
        t.payload.get("parent_plan_item") == "Calculate Stripe totals server-side" for t in tasks
    )


def test_subtask_overflow_falls_back_to_parents(tmp_path: Path) -> None:
    """Plan with >8 subtasks across items → use parent PlanItems as-is."""
    store = TaskStore(tmp_path / "tasks.json")
    # 3 features each with 4 subtasks = 12 subtasks > MAX_SUBTASKS_PER_DECISION (8)
    plan = StructuredSprintPlan(
        goal="Over-decomposed",
        features=[
            PlanItem(
                title=f"feat-{i}",
                estimated_effort="l",
                subtasks=[PlanItem(title=f"sub-{i}-{j}") for j in range(4)],
            )
            for i in range(3)
        ],
    )
    decision = _approved_sprint_decision(plan)
    tasks = refine_decision(decision, task_store=store)
    # Fallback: 3 parent items, not 12 subtasks.
    assert len(tasks) == 3
    assert {t.title for t in tasks} == {"feat-0", "feat-1", "feat-2"}


def test_item_without_subtasks_behaves_as_before(tmp_path: Path) -> None:
    """Regression: PlanItems without subtasks land as a single Task."""
    store = TaskStore(tmp_path / "tasks.json")
    plan = StructuredSprintPlan(
        goal="g",
        features=[PlanItem(title="single feature", estimated_effort="m")],
    )
    decision = _approved_sprint_decision(plan)
    tasks = refine_decision(decision, task_store=store)
    assert len(tasks) == 1
    assert tasks[0].title == "single feature"
    assert tasks[0].payload == {}


# -- Phase D: WIP cap + unassigned + load balancing ----------------------------


def test_wip_cap_produces_unassigned_task(tmp_path: Path, monkeypatch) -> None:
    """When every eligible candidate is at MAX_WIP, Task lands unassigned.

    Demo has 3 engineer seats (Phase C). With MAX_WIP=1, the load
    distributes across all 3 seats first, then overflows to unassigned.
    """
    import minions.crews.refinement as ref

    monkeypatch.setattr(ref, "MAX_WIP_PER_AGENT", 1)

    store = TaskStore(tmp_path / "tasks.json")
    # 3 seats × cap 1 = 3 slots → 4th and 5th land unassigned.
    plan = StructuredSprintPlan(
        goal="overload",
        features=[
            PlanItem(title=f"feat {chr(65 + i)}", suggested_owner_role="engineer") for i in range(5)
        ],
    )
    decision = _approved_sprint_decision(plan)
    tasks = refine_decision(decision, task_store=store)
    assert len(tasks) == 5
    queued = [t for t in tasks if t.status == "queued"]
    unassigned = [t for t in tasks if t.status == "unassigned"]
    # First 3 fill the 3 seats; remaining 2 land unassigned.
    assert len(queued) == 3
    assert len(unassigned) == 2
    # The 3 queued ones each landed on a distinct seat.
    assert {t.owner_agent_id for t in queued} == {
        "engineer@Demo",
        "engineer@Demo#1",
        "engineer@Demo#2",
    }
    # The unassigned ones have no owner.
    assert all(t.owner_agent_id is None for t in unassigned)
    assert all(t.owner_display_name is None for t in unassigned)


# -- Phase C: multi-seat roster + round-robin ---------------------------------


def test_eligible_candidates_single_seat_default() -> None:
    """Roles without a seat declaration return the singleton base id."""
    from minions.crews.refinement import _eligible_candidates

    # `manager` has default 1 seat on Demo (no override in manifest).
    assert _eligible_candidates("manager", "Demo") == ["manager@Demo"]


def test_eligible_candidates_engineer_multi_seat() -> None:
    """Engineer reads `Manifest.team.engineers` (default 3)."""
    from minions.crews.refinement import _eligible_candidates

    # All seed projects ship engineers=3 by default in TeamOverrides.
    candidates = _eligible_candidates("engineer", "Demo")
    assert candidates == ["engineer@Demo", "engineer@Demo#1", "engineer@Demo#2"]


def test_round_robin_distributes_across_engineer_seats(tmp_path: Path) -> None:
    """3-seat engineer + 6 same-role tasks → 2 per seat (round-robin lowest load)."""
    store = TaskStore(tmp_path / "tasks.json")
    plan = StructuredSprintPlan(
        goal="lots of feature work",
        features=[PlanItem(title=f"feat-{i}", suggested_owner_role="engineer") for i in range(6)],
    )
    decision = _approved_sprint_decision(plan)
    tasks = refine_decision(decision, task_store=store)
    assert len(tasks) == 6
    counts: dict[str, int] = {}
    for t in tasks:
        if t.owner_agent_id:
            counts[t.owner_agent_id] = counts.get(t.owner_agent_id, 0) + 1
    # Even split across the 3 seats — no single seat over the WIP cap.
    assert sorted(counts.values()) == [2, 2, 2]


def test_naming_fallback_for_unseeded_seat(tmp_path: Path) -> None:
    """When `engineer@Demo#9` isn't seeded, fall back to the base name + suffix."""
    from minions.agents.naming import resolve_display_name

    path = tmp_path / "names.yaml"
    set_display_name("engineer@Demo", "Sasha", path=path)
    assert resolve_display_name("engineer@Demo#9", path=path) == "Sasha #9"


def test_naming_seeded_seat_uses_distinct_name(tmp_path: Path) -> None:
    """An explicit `engineer@Demo#1: Vera` registry entry wins over the fallback."""
    from minions.agents.naming import resolve_display_name

    path = tmp_path / "names.yaml"
    set_display_name("engineer@Demo", "Sasha", path=path)
    set_display_name("engineer@Demo#1", "Vera", path=path)
    assert resolve_display_name("engineer@Demo", path=path) == "Sasha"
    assert resolve_display_name("engineer@Demo#1", path=path) == "Vera"
    # Unseeded seat still falls back.
    assert resolve_display_name("engineer@Demo#2", path=path) == "Sasha #2"
