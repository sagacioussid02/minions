"""Weekly planning sweep — runs the planning crew across every active project.

Schedule: Mon 09:00 local (per `cadence_profiles.v0_frugal.weekly_planning`).
Outcome: one Decision Record per project, submitted for approval through the
standard pipeline (Decision Store + notifier).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.approval.service import submit_for_approval
from minions.approval.store import DecisionStore
from minions.budget import evaluate as evaluate_budget
from minions.budget import maybe_notify
from minions.config.portfolio import PortfolioConfig
from minions.crews.devils_advocate import attach_critique, should_critique
from minions.crews.planning import run_planning_crew
from minions.models.manifest import Manifest, load_active_manifests
from minions.notify.base import Notifier
from minions.onboarding import build_profile
from minions.onboarding.profile import ProjectProfile

if TYPE_CHECKING:
    from minions.github.client import GitHubClient


class PlanningOutcome(BaseModel):
    project: str
    status: Literal["submitted", "skipped", "error", "throttled"]
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
) -> WeeklyPlanningReport:
    """Run the planning crew for every active project, fan out to approvals.

    `open_github_client` is an optional injection point for the CLI's existing
    `_open_github_client` helper; the entrypoint stays free of CLI imports so
    it remains importable from a runtime host.
    """
    from datetime import UTC, datetime  # local import keeps top of file lean

    started = datetime.now(tz=UTC).isoformat()
    manifests = load_active_manifests(projects_dir)

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
                profile = build_profile(manifest, github_client=gh)
            except Exception:  # noqa: BLE001
                profile = None

            decision = run_planning_crew(
                manifest, dry_run=dry_run, api_key=api_key, profile=profile
            )
            # §9.3 — risk≥medium decisions get a Devil's Advocate critique
            # attached before notification. Non-fatal on failure.
            if should_critique(decision) and api_key is not None:
                try:
                    attach_critique(decision, api_key=api_key, portfolio=portfolio)
                except Exception:  # noqa: BLE001
                    pass
            submit_for_approval(decision, store=store, notifier=notifier)
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
