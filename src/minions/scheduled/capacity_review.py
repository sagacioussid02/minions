"""Capacity watcher — proposes new seats when the org is structurally short-staffed.

openspec/changes/hire-as-decision. Runs weekly (Sunday, before Monday
planning). Reads the task store, detects roles that are over WIP cap or
sitting on a deep unassigned backlog, and files propose-only
``TEAM_COMPOSITION`` Decisions carrying a :class:`HireProposal`.

It NEVER mutates a roster. Every hire is a Decision behind the operator's
approval gate. Two anti-runaway guards: a per-month proposal cap and a
30-day cooldown after a rejection for the same ``(role, scope)``.

This first phase uses point-in-time detection (current load + current
unassigned backlog). Sustained-trend detection (14-day averages) and the
manifest applier land in follow-up phases.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from minions.crews.refinement import _PER_PROJECT_ROLES, MAX_WIP_PER_AGENT
from minions.models.capacity import HireEvidence, HireLevel, HireProposal
from minions.models.decision import Decision, DecisionStatus, DecisionType

if TYPE_CHECKING:
    from minions.approval.store_factory import DecisionStoreLike
    from minions.notify.base import Notifier
    from minions.tasks.store_factory import TaskStoreLike

logger = logging.getLogger(__name__)

# Detection thresholds (env-overridable later; constants for now).
OVERLOAD_MULTIPLIER = 1.5  # seat load >= 1.5x cap → overloaded
UNASSIGNED_BACKLOG_THRESHOLD = 5  # >= N role-matched unassigned tasks → understaffed

# Anti-runaway.
MAX_HIRES_PER_PORTFOLIO_PER_MONTH = 5
MAX_HIRES_PER_PROJECT_PER_MONTH = 2
REJECTION_COOLDOWN_DAYS = 30

# Role → hire level. Most roles hire at "standard"; senior/principal seats
# hire at "senior"; intern stays intern.
_LEVEL_BY_ROLE: dict[str, HireLevel] = {
    "intern": "intern",
    "senior_engineer": "senior",
    "principal_engineer": "senior",
}

# Seed weekly cost per (level) for the frugal cadence — used as the estimate
# on the proposal. Refined from real cost data in a later phase.
_WEEKLY_COST_FRUGAL: dict[HireLevel, float] = {
    "intern": 0.10,
    "standard": 0.20,
    "senior": 0.50,
}

_OPEN_STATUSES = {"queued", "in_progress", "review"}


def _level_for(role: str) -> HireLevel:
    return _LEVEL_BY_ROLE.get(role, "standard")


@dataclass
class CapacityOutcome:
    role: str
    scope: str
    status: str  # "proposed" | "skipped_cap" | "skipped_cooldown" | "dry_run"
    reason: str = ""
    decision_id: str | None = None


@dataclass
class CapacityReport:
    started_at: str
    finished_at: str
    outcomes: list[CapacityOutcome] = field(default_factory=list)

    @property
    def proposed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "proposed")


def _hire_proposal_dict(d: Decision) -> dict[str, object] | None:
    """Pull the ``hire_proposal`` block off a Decision (it lives in the
    extra-payload bucket since Decision uses ``extra='allow'``)."""
    extra = getattr(d, "__pydantic_extra__", None)
    hp = extra.get("hire_proposal") if isinstance(extra, dict) else None
    if hp is None:
        hp = getattr(d, "hire_proposal", None)
    return hp if isinstance(hp, dict) else None


def _recent_hire_decisions(
    store: DecisionStoreLike, *, now: datetime
) -> tuple[dict[str, int], dict[str, int], dict[tuple[str, str], datetime]]:
    """Scan TEAM_COMPOSITION hire Decisions to enforce caps + cooldown.

    Returns:
      - per-project hire count this calendar month,
      - portfolio-wide hire count this month,
      - ``{(role, scope): rejected_at}`` for rejections in the cooldown window.
    """
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    cooldown_start = now - timedelta(days=REJECTION_COOLDOWN_DAYS)

    per_project: dict[str, int] = defaultdict(int)
    portfolio = 0
    rejected: dict[tuple[str, str], datetime] = {}

    for d in store.list_all():
        if d.type != DecisionType.TEAM_COMPOSITION:
            continue
        hp = _hire_proposal_dict(d)
        if hp is None:
            continue
        role = str(hp.get("role", ""))
        scope = str(hp.get("scope", ""))
        created = d.created_at if d.created_at.tzinfo else d.created_at.replace(tzinfo=UTC)

        if created >= month_start and d.status != DecisionStatus.REJECTED:
            portfolio += 1
            # Key by the *cap-check key* so shared hires actually count
            # toward the shared cap. Decision.project for a shared-scope
            # hire is "portfolio" (or whatever the proposer recorded), but
            # the cap check below looks up per_project["shared"]. Without
            # this alignment, shared hires never count toward the cap.
            proj_key = "shared" if scope == "shared" else d.project
            per_project[proj_key] += 1
        if d.status == DecisionStatus.REJECTED and created >= cooldown_start:
            rejected[(role, scope)] = created

    return dict(per_project), {"_portfolio": portfolio}, rejected


def _detect(task_store: TaskStoreLike) -> dict[tuple[str, str], HireEvidence]:
    """Point-in-time detection. Returns ``{(role, scope): evidence}`` for roles
    that are over cap or sitting on a deep unassigned backlog."""
    tasks = task_store.list_all()
    load = task_store.count_open_by_owner()

    # role → scope → unassigned count; role → scope → projects set
    unassigned: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in tasks:
        if t.status != "unassigned":
            continue
        role = t.owner_role
        scope = t.project if role in _PER_PROJECT_ROLES else "shared"
        unassigned[role][scope] += 1

    # Worst open load per (role, scope) from owner_agent_id buckets.
    worst_seat: dict[tuple[str, str], tuple[str, int]] = {}
    for agent_id, n in load.items():
        # agent_id is "<role>@<scope>[#seat]"
        if "@" not in agent_id:
            continue
        role, _, rest = agent_id.partition("@")
        scope = rest.split("#", 1)[0]
        key = (role, scope)
        if key not in worst_seat or n > worst_seat[key][1]:
            worst_seat[key] = (agent_id, n)

    out: dict[tuple[str, str], HireEvidence] = {}
    cap = MAX_WIP_PER_AGENT
    overload_floor = OVERLOAD_MULTIPLIER * cap

    # Union of keys seen in either signal.
    keys: set[tuple[str, str]] = set(worst_seat.keys())
    for role, scopes in unassigned.items():
        for scope in scopes:
            keys.add((role, scope))

    for role, scope in keys:
        seat_id, current_load = worst_seat.get((role, scope), (f"{role}@{scope}", 0))
        backlog = unassigned.get(role, {}).get(scope, 0)
        is_overloaded = current_load >= overload_floor
        is_backlogged = backlog >= UNASSIGNED_BACKLOG_THRESHOLD
        if not (is_overloaded or is_backlogged):
            continue
        projects_affected = (
            sorted(unassigned.get(role, {}).keys()) if scope == "shared" else [scope]
        )
        out[(role, scope)] = HireEvidence(
            seat_id=seat_id,
            current_load=current_load,
            cap=cap,
            unassigned_count=backlog,
            projects_affected=projects_affected,
        )
    return out


def _justify(role: str, scope: str, ev: HireEvidence) -> str:
    parts: list[str] = []
    if ev.current_load >= OVERLOAD_MULTIPLIER * ev.cap:
        parts.append(
            f"{ev.seat_id} is carrying {ev.current_load} open tasks against a cap of {ev.cap}"
        )
    if ev.unassigned_count >= UNASSIGNED_BACKLOG_THRESHOLD:
        where = (
            f" across {', '.join(ev.projects_affected)}"
            if scope == "shared" and ev.projects_affected
            else ""
        )
        parts.append(f"{ev.unassigned_count} {role} tasks are sitting unassigned{where}")
    body = " and ".join(parts) if parts else f"{role} capacity is short on {scope}"
    return (
        f"{body}. Recommend adding a {role} seat on "
        f"{'the shared bench' if scope == 'shared' else scope} to keep throughput healthy."
    )


def _build_proposal(role: str, scope: str, ev: HireEvidence) -> HireProposal:
    level = _level_for(role)
    seat_base = f"{role}@{scope}"
    return HireProposal(
        role=role,
        level=level,
        scope=scope,
        suggested_seat_id=f"{seat_base}#1",
        justification=_justify(role, scope, ev),
        evidence=ev,
        cost_estimate_weekly_usd=_WEEKLY_COST_FRUGAL.get(level, 0.20),
        alternatives_considered=[
            "Raise MAX_WIP_PER_AGENT — rejected: the cap is a real signal, not a workaround.",
            "Wait for natural drain — rejected: the throughput math doesn't close.",
        ],
    )


def run_capacity_review(
    *,
    task_store: TaskStoreLike,
    decision_store: DecisionStoreLike,
    notifier: Notifier,
    dry_run: bool = True,
    now: datetime | None = None,
) -> CapacityReport:
    """Detect overload and file propose-only HIRE Decisions.

    Dry-run (default) detects + reports without writing Decisions.
    """
    from minions.approval.service import submit_for_approval

    now = now or datetime.now(tz=UTC)
    started = now.isoformat()
    outcomes: list[CapacityOutcome] = []

    detected = _detect(task_store)
    per_project, portfolio_counts, rejected = _recent_hire_decisions(decision_store, now=now)
    portfolio_count = portfolio_counts["_portfolio"]

    # Worst-first so the most acute shortage wins the scarce monthly slots.
    ordered = sorted(
        detected.items(),
        key=lambda kv: (kv[1].current_load, kv[1].unassigned_count),
        reverse=True,
    )

    for (role, scope), ev in ordered:
        # Cooldown.
        if (role, scope) in rejected:
            outcomes.append(
                CapacityOutcome(
                    role=role,
                    scope=scope,
                    status="skipped_cooldown",
                    reason=f"rejected within last {REJECTION_COOLDOWN_DAYS}d",
                )
            )
            continue
        # Caps.
        project_key = scope if scope != "shared" else "shared"
        if portfolio_count >= MAX_HIRES_PER_PORTFOLIO_PER_MONTH:
            outcomes.append(
                CapacityOutcome(
                    role=role, scope=scope, status="skipped_cap", reason="portfolio cap"
                )
            )
            continue
        if per_project.get(project_key, 0) >= MAX_HIRES_PER_PROJECT_PER_MONTH:
            outcomes.append(
                CapacityOutcome(role=role, scope=scope, status="skipped_cap", reason="project cap")
            )
            continue

        proposal = _build_proposal(role, scope, ev)
        if dry_run:
            outcomes.append(
                CapacityOutcome(
                    role=role,
                    scope=scope,
                    status="dry_run",
                    reason=proposal.justification,
                )
            )
            continue

        decision = Decision(
            project=scope if scope != "shared" else "portfolio",
            type=DecisionType.TEAM_COMPOSITION,
            summary=f"Hire {proposal.level} {role} for {scope}",
            rationale=proposal.justification,
            risk="medium",
            proposer_role="head_of_engineering",
            proposer_agent_id="head_of_engineering@shared",
            requested_by_role="head_of_engineering",
        )
        # Attach the structured block (Decision allows extra payload keys).
        decision.hire_proposal = proposal.model_dump(mode="json")  # type: ignore[attr-defined]
        submit_for_approval(decision, store=decision_store, notifier=notifier)  # type: ignore[arg-type]
        portfolio_count += 1
        per_project[project_key] = per_project.get(project_key, 0) + 1
        outcomes.append(
            CapacityOutcome(
                role=role,
                scope=scope,
                status="proposed",
                reason=proposal.justification,
                decision_id=str(decision.id),
            )
        )

    return CapacityReport(
        started_at=started,
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
    )
