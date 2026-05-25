"""Refinement crew — turn an approved Sprint Proposal into Tasks.

Phase 3 of openspec/sprint-tasks-memory, extended by Phases B+D of
openspec/enriched-sprint-planning:

  * Phase B — when a ``PlanItem`` has ``subtasks`` populated (l/xl items
    the planning crew chose to break down), refinement creates one Task
    per subtask with the parent title as a prefix.
  * Phase D — load-aware owner resolution. Each new Task picks the
    candidate agent with the lowest open WIP. If every candidate is at
    ``MAX_WIP_PER_AGENT``, the Task lands with ``owner_agent_id=None``
    and ``status='unassigned'``; the backlog sweep picks it up later.

Fires when a Decision of type FEATURE with ``status=APPROVED`` and a
populated ``structured_plan`` has no Tasks yet. Idempotent.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from minions.activity import crew_run
from minions.agents.naming import resolve_display_name
from minions.models.decision import Decision
from minions.models.sprint_plan import PlanItem, StructuredSprintPlan
from minions.models.task import Task, TaskCategory

if TYPE_CHECKING:
    from minions.tasks.store_factory import TaskStoreLike

logger = logging.getLogger(__name__)


# Map category → default owner role when the PlanItem doesn't suggest one.
CATEGORY_DEFAULT_OWNER: dict[TaskCategory, str] = {
    "feature": "engineer",
    "bug": "engineer",
    "tech_debt": "senior_engineer",
    "ops": "cloud_devops",
    "docs": "documentation_engineer",
}

# Per-project roles vs shared bench. Engineer/PM/PO/Manager/TechTeamLead
# live per project; the rest are shared. Used to construct the agent_id.
_PER_PROJECT_ROLES = {
    "engineer", "manager", "product_owner", "tech_team_lead",
}

# Max in-flight Tasks an agent should hold before the load-balancer
# refuses to assign more. Overridable via env. See openspec/changes/
# enriched-sprint-planning/design.md for the rationale (default = 3).
MAX_WIP_PER_AGENT = int(os.environ.get("MINIONS_MAX_WIP_PER_AGENT", "3"))

# Safety cap on subtask expansion per Decision. If the LLM tries to
# decompose a 5-item plan into 30 subtasks, refinement falls back to
# using parent PlanItems as-is.
MAX_SUBTASKS_PER_DECISION = 8


def _agent_id_for(role: str, project: str) -> str:
    """Return ``role@project`` or ``role@shared`` per the namespace rule."""
    return f"{role}@{project}" if role in _PER_PROJECT_ROLES else f"{role}@shared"


def _eligible_candidates(role: str, project: str) -> list[str]:
    """Return all eligible agent_ids for ``role`` in ``project``.

    Multi-seat support (openspec/multi-seat-roster Phase C): when the
    project's manifest declares N seats for ``role`` (today: only
    ``engineer`` via ``Manifest.team.engineers``), this returns
    ``[role@project, role@project#1, role@project#2, …]`` — matching the
    seat-index suffix scheme in ``MinionAgent.for_role``. Shared-bench
    roles stay singleton.
    """
    if role not in _PER_PROJECT_ROLES:
        return [f"{role}@shared"]
    base = _agent_id_for(role, project)
    try:
        from minions.agents.roster import seats_for
        n = seats_for(role, project)
    except Exception:  # noqa: BLE001
        n = 1
    if n <= 1:
        return [base]
    # Seat 0 has no suffix (matches MinionAgent.for_role convention);
    # seats 1..N-1 get "#1", "#2", … so the load-balancer enumerates all.
    return [base] + [f"{base}#{i}" for i in range(1, n)]


def _resolve_owner(
    *,
    item: PlanItem,
    category: TaskCategory,
    project: str,
    open_load: dict[str, int],
    max_wip: int | None = None,
) -> tuple[str, str | None, str | None]:
    """Return ``(role, agent_id|None, display|None)`` for the new Task.

    Strategy:
      1. Resolve ``role`` from item.suggested_owner_role or category default.
      2. Build the candidate list (single-seat today; multi-seat post-Phase-C).
      3. Filter to candidates with ``open_load < max_wip``.
      4. If any free, pick the lowest-load candidate (tie-break: stable
         sort on agent_id for round-robin determinism). Mutate
         ``open_load`` to reflect the assignment.
      5. If none free, return ``(role, None, None)`` → caller saves the
         Task with ``status="unassigned"``.
    """
    role = (item.suggested_owner_role or "").strip().lower()
    if not role:
        role = CATEGORY_DEFAULT_OWNER[category]
    candidates = _eligible_candidates(role, project)
    cap = MAX_WIP_PER_AGENT if max_wip is None else max_wip
    free = [aid for aid in candidates if open_load.get(aid, 0) < cap]
    if not free:
        return role, None, None
    chosen = min(free, key=lambda aid: (open_load.get(aid, 0), aid))
    open_load[chosen] = open_load.get(chosen, 0) + 1
    return role, chosen, resolve_display_name(chosen)


def refine_decision(
    decision: Decision,
    *,
    task_store: "TaskStoreLike",
) -> list[Task]:
    """Emit one Task per PlanItem (or per subtask if the item has them).

    Idempotent: returns existing Tasks if any are already linked to this
    Decision.
    """
    existing = task_store.list_by_decision(decision.id)
    if existing:
        return existing

    plan = decision.structured_plan
    if plan is None:
        return []

    open_load = task_store.count_open_by_owner()
    created: list[Task] = []

    # Backstop on subtask expansion: count total subtasks across the plan
    # before we commit to expanding any. If the plan over-decomposes,
    # fall back to using parent PlanItems as-is for the whole Decision so
    # the operator gets a coherent (if less granular) sprint.
    total_subtasks = sum(
        len(item.subtasks)
        for _category, items in _iter_plan_sections(plan)
        for item in items
    )
    expand_subtasks = total_subtasks <= MAX_SUBTASKS_PER_DECISION
    if not expand_subtasks and total_subtasks > 0:
        logger.warning(
            "refinement: %d subtasks across decision %s exceeds cap %d; "
            "using parent PlanItems as-is",
            total_subtasks, str(decision.id)[:8], MAX_SUBTASKS_PER_DECISION,
        )

    with crew_run(
        crew="refinement",
        project=decision.project,
        agents=["manager"],
        decision_id=str(decision.id),
    ):
        for category, items in _iter_plan_sections(plan):
            for item in items:
                if expand_subtasks and item.subtasks:
                    for sub in item.subtasks:
                        created.append(
                            _create_task_for_item(
                                parent_title=item.title,
                                item=sub,
                                category=category,
                                decision=decision,
                                open_load=open_load,
                                task_store=task_store,
                            )
                        )
                else:
                    created.append(
                        _create_task_for_item(
                            parent_title=None,
                            item=item,
                            category=category,
                            decision=decision,
                            open_load=open_load,
                            task_store=task_store,
                        )
                    )

    return created


def _create_task_for_item(
    *,
    parent_title: str | None,
    item: PlanItem,
    category: TaskCategory,
    decision: Decision,
    open_load: dict[str, int],
    task_store: "TaskStoreLike",
) -> Task:
    role, agent_id, display = _resolve_owner(
        item=item,
        category=category,
        project=decision.project,
        open_load=open_load,
    )
    title = f"[{parent_title}] {item.title}" if parent_title else item.title
    payload: dict[str, object] = {}
    if parent_title:
        payload["parent_plan_item"] = parent_title
    task = Task(
        decision_id=decision.id,
        project=decision.project,
        sprint_number=decision.sprint_number,
        category=category,
        title=title,
        description=item.rationale or item.title,
        acceptance_criteria=item.acceptance_criteria,
        owner_role=role,
        owner_agent_id=agent_id,
        owner_display_name=display,
        estimated_effort=item.estimated_effort,
        status="unassigned" if agent_id is None else "queued",
        payload=payload,
    )
    task_store.save(task)
    return task


def _iter_plan_sections(
    plan: StructuredSprintPlan,
) -> list[tuple[TaskCategory, list[PlanItem]]]:
    return [
        ("feature", plan.features),
        ("bug", plan.bugs),
        ("tech_debt", plan.tech_debt),
        ("ops", plan.ops),
        ("docs", plan.docs),
    ]
