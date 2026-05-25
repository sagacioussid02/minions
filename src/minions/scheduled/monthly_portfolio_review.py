"""Monthly portfolio review sweep.

Runs the executive-layer portfolio review crew once across the whole active
portfolio, then submits the resulting Decision through the standard approval
pipeline.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from minions.activity import ActivityEntry, append
from minions.approval.service import submit_for_approval
from minions.crews.devils_advocate import attach_critique, should_critique
from minions.crews.portfolio_review import assemble_inputs
from minions.crews.portfolio_review import run_portfolio_review as run_portfolio_review_crew
from minions.crews.security import attach_review as attach_security_review
from minions.crews.security import should_review as should_security_review
from minions.models.agile import AgileRitualRecord

if TYPE_CHECKING:
    from datetime import datetime

    from minions.agile.store_factory import AgileStoreLike
    from minions.approval.store_factory import DecisionStoreLike
    from minions.audit.store_factory import AuditFindingStoreLike
    from minions.config.portfolio import PortfolioConfig
    from minions.crews.engineer_runs_store_factory import EngineerRunStoreLike
    from minions.notify.base import Notifier
    from minions.questions.store_factory import QuestionStoreLike


class MonthlyReviewReport(BaseModel):
    started_at: str
    finished_at: str
    status: Literal["submitted", "error"]
    decision_id: str | None = None
    error: str | None = None
    period: str | None = None
    projects_count: int = 0

    @property
    def submitted(self) -> int:
        return 1 if self.status == "submitted" else 0

    @property
    def errored(self) -> int:
        return 1 if self.status == "error" else 0


def run_monthly_portfolio_review(
    *,
    projects_dir: Path,
    store: DecisionStoreLike,
    engineer_runs_store: EngineerRunStoreLike,
    notifier: Notifier,
    audit_findings_store: AuditFindingStoreLike | None = None,
    questions_store: QuestionStoreLike | None = None,
    api_key: str | None = None,
    dry_run: bool = True,
    cost_log_path: Path | None = None,
    portfolio: PortfolioConfig | None = None,
    now: datetime | None = None,
    agile_store: AgileStoreLike | None = None,
    activity_log_path: Path | None = None,
) -> MonthlyReviewReport:
    """Run the executive portfolio review and submit one pending Decision."""
    import uuid
    from datetime import UTC, datetime

    started_dt = datetime.now(tz=UTC)
    started = started_dt.isoformat()
    period: str | None = None
    projects_count = 0
    try:
        inputs = assemble_inputs(
            projects_dir=projects_dir,
            decision_store=store,
            engineer_runs_store=engineer_runs_store,
            audit_findings_store=audit_findings_store,
            questions_store=questions_store,
            cost_log_path=cost_log_path,
            portfolio=portfolio,
            now=now,
        )
        period = inputs.current_period_label
        projects_count = len(inputs.per_project)

        decision = run_portfolio_review_crew(
            inputs=inputs,
            api_key=api_key,
            portfolio=portfolio,
            dry_run=dry_run,
        )

        if should_critique(decision) and api_key is not None:
            with suppress(Exception):
                attach_critique(decision, api_key=api_key, portfolio=portfolio)
        if should_security_review(decision) and api_key is not None:
            with suppress(Exception):
                attach_security_review(decision, api_key=api_key, portfolio=portfolio)

        submit_for_approval(decision, store=store, notifier=notifier)
        if agile_store is not None:
            for project_stats in inputs.per_project:
                project = project_stats.project
                project_runs = [
                    r for r in engineer_runs_store.list_by_project(project)
                    if r.pr_state == "merged"
                ][:8]
                demo = AgileRitualRecord(
                    project=project,
                    ritual="monthly_demo",
                    period_start=started_dt.replace(day=1),
                    period_end=datetime.now(tz=UTC),
                    summary=(
                        f"{project} monthly demo for {inputs.current_period_label}: "
                        f"{len(project_runs)} merged PR(s), portfolio review "
                        f"Decision {str(decision.id)[:8]} submitted."
                    ),
                    blockers=[],
                    next_actions=["Review monthly portfolio Decision and approve priority changes"],
                    related_decision_ids=[str(decision.id)],
                    related_pr_urls=[r.pr_url for r in project_runs if r.pr_url],
                )
                agile_store.save_ritual(demo)
                append(
                    ActivityEntry(
                        timestamp=datetime.now(tz=UTC),
                        event="monthly_demo_ready",
                        run_id=f"monthly-demo-{project}-{uuid.uuid4().hex}",
                        crew="monthly_demo",
                        project=project,
                        decision_id=str(demo.id),
                        agents=("product_manager", "manager", "managing_director"),
                    ),
                    path=activity_log_path,
                )
        return MonthlyReviewReport(
            started_at=started,
            finished_at=datetime.now(tz=UTC).isoformat(),
            status="submitted",
            decision_id=str(decision.id),
            period=period,
            projects_count=projects_count,
        )
    except Exception as e:  # noqa: BLE001
        return MonthlyReviewReport(
            started_at=started,
            finished_at=datetime.now(tz=UTC).isoformat(),
            status="error",
            error=str(e),
            period=period,
            projects_count=projects_count,
        )
