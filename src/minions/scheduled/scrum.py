"""Two-day Agile scrum ritual sweep."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from minions.activity import ActivityEntry, append
from minions.agile.store_factory import AgileStoreLike
from minions.approval.store_factory import DecisionStoreLike
from minions.crews.engineer_runs_store_factory import EngineerRunStoreLike
from minions.models.agile import AgileRitualRecord
from minions.models.decision import DecisionStatus
from minions.models.manifest import load_active_manifests
from minions.questions.store_factory import QuestionStoreLike


class ScrumOutcome(BaseModel):
    project: str
    status: Literal["recorded", "error"]
    ritual_id: str | None = None
    blockers: list[str] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)
    error: str | None = None


class ScrumReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[ScrumOutcome] = Field(default_factory=list)

    @property
    def recorded(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "recorded")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")


def run_scrum(
    *,
    projects_dir: Path,
    store: DecisionStoreLike,
    engineer_runs_store: EngineerRunStoreLike,
    agile_store: AgileStoreLike,
    questions_store: QuestionStoreLike | None = None,
    activity_log_path: Path | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> ScrumReport:
    now = now or datetime.now(tz=UTC)
    period_start = now - timedelta(days=2)
    manifests = load_active_manifests(projects_dir)
    outcomes: list[ScrumOutcome] = []

    for project in sorted(manifests):
        try:
            decisions = [
                d
                for d in store.list_all()
                if d.project == project and d.created_at >= period_start
            ]
            runs = [
                r
                for r in engineer_runs_store.list_by_project(project)
                if r.completed_at >= period_start
            ]
            questions = (
                [q for q in questions_store.list_all() if q.project == project]
                if questions_store is not None
                else []
            )
            open_questions = [q for q in questions if q.status.value in {"open", "escalated"}]
            blockers = _blockers(decisions, runs, open_questions)
            next_actions = _next_actions(decisions, runs, open_questions)
            summary = _summary(project, decisions, runs, open_questions, blockers, next_actions)
            record = AgileRitualRecord(
                project=project,
                ritual="scrum",
                period_start=period_start,
                period_end=now,
                summary=summary,
                blockers=blockers,
                next_actions=next_actions,
                related_decision_ids=[str(d.id) for d in decisions],
                related_pr_urls=[r.pr_url for r in runs if r.pr_url],
            )
            if not dry_run:
                agile_store.save_ritual(record)
                # Attach the ritual's actual content so the Stage feed renders
                # "demo_three scrum: 14 blockers — <first blocker>. Next: <first action>"
                # instead of the generic "shared a daily scrum update" line.
                extra_payload = {
                    "summary": summary[:280],
                    "blocker_count": len(blockers),
                    "blockers_preview": [b[:180] for b in blockers[:3]],
                    "next_actions_preview": [a[:180] for a in next_actions[:3]],
                    "decisions_count": len(decisions),
                    "open_pr_count": sum(
                        1 for r in runs if (r.pr_state or "open") == "open"
                    ),
                }
                append(
                    ActivityEntry(
                        timestamp=now,
                        event="scrum_created",
                        run_id=f"scrum-{project}-{uuid.uuid4().hex}",
                        crew="scrum",
                        project=project,
                        decision_id=str(record.id),
                        agents=("product_owner", "principal_engineer", "manager"),
                        extra=extra_payload,
                    ),
                    path=activity_log_path,
                )
            outcomes.append(
                ScrumOutcome(
                    project=project,
                    status="recorded",
                    ritual_id=str(record.id),
                    blockers=blockers,
                    next_actions=next_actions,
                )
            )
        except Exception as e:  # noqa: BLE001
            outcomes.append(ScrumOutcome(project=project, status="error", error=str(e)))

    return ScrumReport(
        started_at=now.isoformat(),
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
    )


def _blockers(decisions, runs, open_questions) -> list[str]:
    blockers: list[str] = []
    for run in runs:
        if run.ci_conclusion == "failure":
            blockers.append(f"PR #{run.pr_number or '?'} has failing CI")
        if run.review_status in {"changes_requested", "merge_blocked", "conflict_queued"}:
            blockers.append(
                f"PR #{run.pr_number or '?'} review status is {run.review_status}"
            )
    for decision in decisions:
        if decision.status is DecisionStatus.PENDING:
            blockers.append(f"Decision {str(decision.id)[:8]} is awaiting operator approval")
    for question in open_questions[:3]:
        blockers.append(f"Question {str(question.id)[:8]} is {question.status.value}")
    return _dedupe(blockers)


def _next_actions(decisions, runs, open_questions) -> list[str]:
    actions: list[str] = []
    if any(d.status is DecisionStatus.APPROVED for d in decisions):
        actions.append("Engineer crew should pick up approved Decisions on the next execute sweep")
    if any(r.ci_conclusion == "failure" for r in runs):
        actions.append("Creator agent should inspect failed checks and queue or open a fix")
    if any(r.review_status == "merge_blocked" for r in runs):
        actions.append("Operator should review crew-approved PRs blocked by branch protection")
    if open_questions:
        actions.append("Product Owner or PM should answer open questions before planning more work")
    if not actions:
        actions.append("Product Owner should identify the next valuable sprint-sized improvement")
    return actions


def _summary(project, decisions, runs, open_questions, blockers, next_actions) -> str:
    merged = sum(1 for r in runs if r.pr_state == "merged")
    open_prs = sum(1 for r in runs if r.pr_state in {None, "open"})
    return (
        f"{project} scrum: {len(decisions)} recent Decision(s), {open_prs} open PR(s), "
        f"{merged} merged PR(s), {len(open_questions)} open question(s). "
        f"Blockers: {len(blockers)}. Next: {next_actions[0]}"
    )


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out
