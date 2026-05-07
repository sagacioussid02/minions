"""Daily monitoring sweep — read-only signal collection across the portfolio.

Schedule: every day 09:00 local (per `cadence_profiles.v0_frugal.daily_monitoring`).
No LLM calls. No writes. Walks every active manifest, builds a `ProjectProfile`,
and emits a structured report. The Friday digest reads these reports; the
weekly planner re-uses the same profiler for grounding.

Why no Decision Records here: monitoring is observation. If a signal warrants
action it surfaces in Monday's planning run or the Friday digest.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.approval.service import DEFAULT_TIMEOUT_HOURS, sweep_timeouts
from minions.approval.store import DecisionStore
from minions.audit import AuditFindingStore, audit_after_sync
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.models.manifest import Manifest, load_active_manifests
from minions.notify.base import Notifier
from minions.onboarding import build_profile
from minions.onboarding.profile import ProjectProfile
from minions.sync import sync_pr_status

if TYPE_CHECKING:
    from minions.config.portfolio import PortfolioConfig
    from minions.github.client import GitHubClient


class ProjectMonitorEntry(BaseModel):
    project: str
    status: Literal["ok", "error"]
    error: str | None = None
    tasks_remaining: int | None = None
    open_issues: int = 0
    todo_count: int = 0
    has_ci: bool = False
    languages: dict[str, int] = Field(default_factory=dict)


class DailyMonitorReport(BaseModel):
    started_at: str
    finished_at: str
    entries: list[ProjectMonitorEntry] = Field(default_factory=list)
    timed_out: list[str] = Field(default_factory=list)  # decision ids auto-rejected this run
    pr_state_changes: int = 0  # how many PRs flipped state during this run
    pr_audits_run: int = 0  # how many merged PRs the Code Auditor reviewed
    pr_findings_by_severity: dict[str, int] = Field(default_factory=dict)

    def to_markdown(self) -> str:
        lines: list[str] = ["# Daily monitoring report", ""]
        if self.timed_out:
            lines.append(
                f"⏱ **Auto-rejected {len(self.timed_out)} stale decision(s)**: "
                + ", ".join(f"`{d[:8]}`" for d in self.timed_out)
            )
            lines.append("")
        if self.pr_state_changes:
            audit_note = f", {self.pr_audits_run} audited" if self.pr_audits_run else ""
            lines.append(f"🔄 **PR sync:** {self.pr_state_changes} state change(s){audit_note}")
            for sev, n in sorted(self.pr_findings_by_severity.items()):
                emoji = {"high": "🔴", "medium": "🟡", "advisory": "🔵"}.get(sev, "•")
                lines.append(f"  {emoji} {n} {sev} finding(s)")
            lines.append("")
        for e in self.entries:
            if e.status == "error":
                lines.append(f"- ❌ **{e.project}** — {e.error}")
                continue
            tr = f", tasks remaining={e.tasks_remaining}" if e.tasks_remaining is not None else ""
            lines.append(
                f"- ✅ **{e.project}** — issues={e.open_issues}, todos={e.todo_count}, "
                f"ci={'on' if e.has_ci else 'off'}{tr}"
            )
        return "\n".join(lines)


def run_daily_monitor(
    *,
    projects_dir: Path,
    open_github_client: Callable[[Manifest], GitHubClient | None] | None = None,
    store: DecisionStore | None = None,
    notifier: Notifier | None = None,
    timeout_hours: float = DEFAULT_TIMEOUT_HOURS,
    engineer_runs_store: EngineerRunStore | None = None,
    audit_findings_store: AuditFindingStore | None = None,
    api_key: str | None = None,
    portfolio: PortfolioConfig | None = None,
) -> DailyMonitorReport:
    from datetime import UTC, datetime

    started = datetime.now(tz=UTC).isoformat()
    manifests = load_active_manifests(projects_dir)

    timed_out_ids: list[str] = []
    if store is not None and notifier is not None:
        try:
            timed_out = sweep_timeouts(store=store, notifier=notifier, ttl_hours=timeout_hours)
            timed_out_ids = [str(d.id) for d in timed_out]
        except Exception:  # noqa: BLE001 — sweep failure must not abort monitor
            timed_out_ids = []

    # PR state sync + (optional) Code Auditor on newly-merged PRs.
    pr_state_changes = 0
    pr_audits_run = 0
    pr_findings_by_severity: dict[str, int] = {}
    if engineer_runs_store is not None and store is not None and open_github_client is not None:
        try:
            sync_report = sync_pr_status(
                store=engineer_runs_store,
                open_github_client=open_github_client,
                manifests=manifests,
                decision_store=store,
            )
            pr_state_changes = sync_report.changed
            if audit_findings_store is not None:
                audit_report = audit_after_sync(
                    sync_outcomes=sync_report.outcomes,
                    runs_store=engineer_runs_store,
                    decision_store=store,
                    findings_store=audit_findings_store,
                    open_github_client=open_github_client,
                    manifests=manifests,
                    api_key=api_key,
                    portfolio=portfolio,
                )
                pr_audits_run = audit_report.audited
                pr_findings_by_severity = audit_report.findings_by_severity
        except Exception:  # noqa: BLE001 — sync/audit failure must not abort monitor
            pass

    entries: list[ProjectMonitorEntry] = []
    for name, manifest in sorted(manifests.items()):
        try:
            gh = None
            if open_github_client is not None and manifest.source.kind == "github":
                gh = open_github_client(manifest)
            profile: ProjectProfile = build_profile(manifest, github_client=gh)
            entries.append(
                ProjectMonitorEntry(
                    project=name,
                    status="ok",
                    tasks_remaining=(profile.tasks_md.remaining if profile.tasks_md else None),
                    open_issues=len(profile.open_issues),
                    todo_count=profile.todo_count,
                    has_ci=profile.has_ci,
                    languages=profile.languages,
                )
            )
        except Exception as e:  # noqa: BLE001 — per-project isolation
            entries.append(ProjectMonitorEntry(project=name, status="error", error=str(e)))

    finished = datetime.now(tz=UTC).isoformat()
    return DailyMonitorReport(
        started_at=started,
        finished_at=finished,
        entries=entries,
        timed_out=timed_out_ids,
        pr_state_changes=pr_state_changes,
        pr_audits_run=pr_audits_run,
        pr_findings_by_severity=pr_findings_by_severity,
    )
