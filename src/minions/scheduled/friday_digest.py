"""Friday digest — weekly rollup notification to the operator.

Schedule: Fri 16:00 local (per `cadence_profiles.v0_frugal.weekly_digest`).
Reads from the Decision Store + a fresh monitor sweep, renders a Markdown
summary, and pushes it through the Notifier.

No LLM calls in v0 — pure aggregation of structured data we already have.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from minions.approval.store import DecisionStore
from minions.models.decision import Decision, DecisionStatus
from minions.models.manifest import Manifest, load_active_manifests
from minions.notify.base import Notifier
from minions.scheduled.daily_monitor import DailyMonitorReport, run_daily_monitor
from minions.scheduled.site_sentry import RenewalStatus, renewal_statuses

if TYPE_CHECKING:
    from minions.github.client import GitHubClient


class FridayDigestReport(BaseModel):
    started_at: str
    finished_at: str
    week_window_days: int = 7
    pending: int = 0
    approved: int = 0
    rejected: int = 0
    executed: int = 0
    monitor: DailyMonitorReport | None = None
    body: str = Field(default="")


def run_friday_digest(
    *,
    projects_dir: Path,
    store: DecisionStore,
    notifier: Notifier,
    week_window_days: int = 7,
    open_github_client: Callable[[Manifest], GitHubClient | None] | None = None,
    send: bool = True,
) -> FridayDigestReport:
    started = datetime.now(tz=UTC).isoformat()
    cutoff = datetime.now(tz=UTC) - timedelta(days=week_window_days)

    decisions = store.list_all()
    in_window = [d for d in decisions if d.created_at >= cutoff]

    pending = sum(1 for d in in_window if d.status is DecisionStatus.PENDING)
    approved = sum(1 for d in in_window if d.status is DecisionStatus.APPROVED)
    rejected = sum(1 for d in in_window if d.status is DecisionStatus.REJECTED)
    executed = sum(1 for d in in_window if d.status is DecisionStatus.EXECUTED)

    monitor = run_daily_monitor(projects_dir=projects_dir, open_github_client=open_github_client)

    # Renewal radar: licenses + credential rotations due within the amber
    # window (≤30d) or already overdue. Dates only — no secret access.
    upcoming_renewals = [
        r for r in renewal_statuses(load_active_manifests(projects_dir)) if r.severity != "ok"
    ]

    body = _render_digest(
        pending=pending,
        approved=approved,
        rejected=rejected,
        executed=executed,
        in_window=in_window,
        monitor=monitor,
        window_days=week_window_days,
        renewals=upcoming_renewals,
    )

    if send:
        try:
            notifier.notify_text(subject="Minions weekly digest", body=body)
        except Exception:  # noqa: BLE001 — digest must never crash the cron
            pass

    finished = datetime.now(tz=UTC).isoformat()
    return FridayDigestReport(
        started_at=started,
        finished_at=finished,
        week_window_days=week_window_days,
        pending=pending,
        approved=approved,
        rejected=rejected,
        executed=executed,
        monitor=monitor,
        body=body,
    )


def _render_digest(
    *,
    pending: int,
    approved: int,
    rejected: int,
    executed: int,
    in_window: Sequence[Decision],
    monitor: DailyMonitorReport,
    window_days: int,
    renewals: Sequence[RenewalStatus] = (),
) -> str:
    lines: list[str] = [
        f"# Minions weekly digest — last {window_days}d",
        "",
        f"- Pending: **{pending}**",
        f"- Approved: {approved}",
        f"- Rejected: {rejected}",
        f"- Executed (PR opened): {executed}",
        "",
        "## Portfolio snapshot",
        monitor.to_markdown(),
    ]
    if renewals:
        lines.append("\n## Renewals & rotations due\n")
        for r in renewals:
            when = f"in {r.days_until}d" if r.days_until >= 0 else f"**{-r.days_until}d overdue**"
            label = "🔑 rotate" if r.kind == "secret_rotation" else "📄 renew"
            link = f" ([link]({r.url}))" if r.url else ""
            lines.append(f"- {label} **{r.name}** ({r.project}) — {r.due} · {when}{link}")
    if pending:
        lines.append("\n## Awaiting your review\n")
        for d in in_window:
            if d.status is not DecisionStatus.PENDING:
                continue
            lines.append(f"- `{str(d.id)[:8]}` **{d.project}** — {d.summary}")
    return "\n".join(lines)
