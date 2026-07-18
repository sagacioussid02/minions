"""Weekly planning sweep — runs the planning crew across every active project.

Schedule: Mon 09:00 local (per `cadence_profiles.v0_frugal.weekly_planning`).
Outcome: one Decision Record per project, submitted for approval through the
standard pipeline (Decision Store + notifier).
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.activity import ActivityEntry, append
from minions.approval.service import submit_for_approval
from minions.approval.store import DecisionStore
from minions.budget import evaluate as evaluate_budget
from minions.budget import maybe_notify
from minions.config.portfolio import PortfolioConfig
from minions.crews.devils_advocate import attach_critique, should_critique
from minions.crews.planning import PlanningRefusedStaleError, run_planning_crew
from minions.crews.security import attach_review as attach_security_review
from minions.crews.security import should_review as should_security_review
from minions.models.agile import AgileRitualRecord
from minions.models.manifest import Manifest, load_active_manifests
from minions.notify.base import Notifier
from minions.onboarding import build_profile
from minions.onboarding.profile import ProjectProfile

if TYPE_CHECKING:
    from minions.agents.memory_store_factory import AgentMemoryStoreLike
    from minions.agile.store_factory import AgileStoreLike
    from minions.dossiers.store_factory import DossierStoreLike
    from minions.github.client import GitHubClient


_WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


def _weekday_name(idx: int) -> str:
    """Map ``datetime.weekday()`` (Mon=0…Sun=6) to the lowercase day name."""
    return _WEEKDAY_NAMES[idx]


class PlanningOutcome(BaseModel):
    project: str
    status: Literal["submitted", "skipped", "error", "throttled", "dossier_very_stale"]
    decision_id: str | None = None
    error: str | None = None
    profile_summary: str | None = None  # short one-line context for the digest
    budget_fraction: float | None = None  # populated when status == "throttled"


class WeeklyPlanningReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[PlanningOutcome] = Field(default_factory=list)

    @property
    def submitted(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "submitted")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")

    @property
    def throttled(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "throttled")


def run_weekly_planning(
    *,
    projects_dir: Path,
    store: DecisionStore,
    notifier: Notifier,
    api_key: str | None = None,
    dry_run: bool = True,
    open_github_client: Callable[[Manifest], GitHubClient | None] | None = None,
    cost_log_path: Path | None = None,
    budget_notifications_path: Path | None = None,
    portfolio: PortfolioConfig | None = None,
    projects: list[str] | None = None,
    rotate: bool = False,
    agile_store: AgileStoreLike | None = None,
    activity_log_path: Path | None = None,
    sprints_path: Path | None = None,
    memory_store: AgentMemoryStoreLike | None = None,
    dossier_store: DossierStoreLike | None = None,
) -> WeeklyPlanningReport:
    """Run the planning crew for every active project, fan out to approvals.

    `open_github_client` is an optional injection point for the CLI's existing
    `_open_github_client` helper; the entrypoint stays free of CLI imports so
    it remains importable from a runtime host.
    """
    import uuid
    from datetime import UTC, datetime  # local import keeps top of file lean

    started_dt = datetime.now(tz=UTC)
    started = started_dt.isoformat()
    manifests = load_active_manifests(projects_dir)
    if projects:
        wanted = {p.lower() for p in projects}
        manifests = {
            name: manifest for name, manifest in manifests.items() if name.lower() in wanted
        }
    elif rotate:
        # Budget mode: plan exactly one project per run, cycling through the
        # portfolio by ISO week number so each project is planned once every
        # N weeks. Pairs with a once-a-week cron to cap both Actions minutes
        # and LLM spend.
        ordered = sorted(manifests)
        if ordered:
            iso_week = started_dt.isocalendar().week
            pick = ordered[iso_week % len(ordered)]
            manifests = {pick: manifests[pick]}
    else:
        # Stagger-by-day filter: when a manifest sets ``planning_day``, only
        # run it on the matching UTC weekday. Explicit --project overrides
        # this (manual sweeps run regardless of the day). Manifests without
        # planning_day always run — preserves the legacy single-cron behavior
        # for projects the operator hasn't migrated yet.
        today = _weekday_name(started_dt.weekday())
        manifests = {
            name: m
            for name, m in manifests.items()
            if m.planning_day is None or m.planning_day == today
        }

    outcomes: list[PlanningOutcome] = []
    for name, manifest in sorted(manifests.items()):
        try:
            # §6.5 throttle — skip non-critical planning when project is at ≥80%.
            # Dry-run sweeps cost $0, so let them through unconditionally.
            if not dry_run:
                bstate = evaluate_budget(manifest, cost_log_path=cost_log_path)
                if bstate.is_throttled:
                    if budget_notifications_path is not None:
                        maybe_notify(
                            bstate,
                            notifier=notifier,
                            notifications_path=budget_notifications_path,
                        )
                    outcomes.append(
                        PlanningOutcome(
                            project=name,
                            status="throttled",
                            error=(
                                f"budget {bstate.state} "
                                f"({bstate.fraction * 100:.0f}% of "
                                f"${bstate.monthly_cap_usd:.2f}/mo)"
                            ),
                            budget_fraction=bstate.fraction,
                        )
                    )
                    continue

            gh = None
            if open_github_client is not None and manifest.source.kind == "github":
                gh = open_github_client(manifest)
            profile = None
            try:
                profile = build_profile(manifest, github_client=gh, dossier_store=dossier_store)
            except Exception:  # noqa: BLE001
                profile = None

            # Bump the per-project sprint counter — first call returns 0
            # (Sprint 0), subsequent calls 1, 2, 3, … Failures here are
            # non-fatal; the Decision just lands without a sprint number.
            sprint_number: int | None = None
            if sprints_path is not None:
                try:
                    from minions.sprints.store_factory import make_sprint_counter_store

                    counter = make_sprint_counter_store(sprints_path)
                    sprint_number = counter.bump(manifest.name)
                except Exception:  # noqa: BLE001
                    sprint_number = None

            try:
                decision = run_planning_crew(
                    manifest,
                    dry_run=dry_run,
                    api_key=api_key,
                    profile=profile,
                    sprint_number=sprint_number,
                )
                decision.tenant_id = manifest.tenant_id
            except PlanningRefusedStaleError as refused:
                # Plant the queued-discovery decision so the next cron-discovery
                # sweep picks it up; record an outcome and move on.
                with suppress(Exception):
                    store.save(refused.queued)
                outcomes.append(
                    PlanningOutcome(
                        project=name,
                        status="dossier_very_stale",
                        decision_id=str(refused.queued.id),
                        error=str(refused),
                    )
                )
                continue
            # §9.3 — risk≥medium decisions get a Devil's Advocate critique
            # attached before notification. Non-fatal on failure.
            if should_critique(decision) and api_key is not None:
                with suppress(Exception):
                    attach_critique(
                        decision,
                        api_key=api_key,
                        portfolio=portfolio,
                        memory_store=memory_store,
                    )
            # §9.4 — security review on risk>=medium. Same gate as DA, runs
            # independently so a parse failure doesn't drop the critique too.
            if should_security_review(decision) and api_key is not None:
                with suppress(Exception):
                    attach_security_review(decision, api_key=api_key, portfolio=portfolio)
            submit_for_approval(decision, store=store, notifier=notifier)
            if memory_store is not None:
                with suppress(Exception):
                    for role in ("product_owner", "principal_engineer", "manager"):
                        memory_store.record(
                            agent_id=f"{role}@{name}",
                            sprint_number=decision.sprint_number,
                            decision_id=decision.id,
                            event="sprint_planned",
                            summary=(
                                f"Planned Sprint {decision.sprint_number} for {name}: "
                                f"{decision.summary}."
                            ),
                            details=decision.structured_plan.goal
                            if decision.structured_plan
                            else decision.diff_or_plan,
                        )
            if agile_store is not None:
                ritual = AgileRitualRecord(
                    project=name,
                    ritual="sprint_planning",
                    period_start=started_dt,
                    period_end=datetime.now(tz=UTC),
                    summary=decision.summary,
                    blockers=[],
                    next_actions=[
                        "Operator reviews the proposed sprint Decision",
                        "Engineer crew executes after approval",
                    ],
                    related_decision_ids=[str(decision.id)],
                    related_pr_urls=[],
                )
                agile_store.save_ritual(ritual)
                append(
                    ActivityEntry(
                        timestamp=datetime.now(tz=UTC),
                        event="sprint_planned",
                        run_id=f"sprint-planning-{name}-{uuid.uuid4().hex}",
                        crew="weekly_planning",
                        project=manifest.name,
                        decision_id=str(ritual.id),
                        agents=("product_owner", "principal_engineer", "manager"),
                        tenant_id=manifest.tenant_id,
                    ),
                    path=activity_log_path,
                )
            outcomes.append(
                PlanningOutcome(
                    project=name,
                    status="submitted",
                    decision_id=str(decision.id),
                    profile_summary=_summarize_profile(profile),
                )
            )
        except Exception as e:  # noqa: BLE001 — per-project isolation
            outcomes.append(PlanningOutcome(project=name, status="error", error=str(e)))

    finished = datetime.now(tz=UTC).isoformat()
    return WeeklyPlanningReport(started_at=started, finished_at=finished, outcomes=outcomes)


def _summarize_profile(profile: ProjectProfile | None) -> str | None:
    if profile is None:
        return None
    bits: list[str] = []
    if profile.tasks_md is not None:
        bits.append(f"tasks.md remaining={profile.tasks_md.remaining}")
    if profile.open_issues:
        bits.append(f"issues={len(profile.open_issues)}")
    if profile.todo_count:
        bits.append(f"todos={profile.todo_count}")
    return ", ".join(bits) if bits else None
