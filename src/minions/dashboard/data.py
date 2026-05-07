"""Pure data builders for the dashboard — testable without Streamlit.

All functions here read from existing sources (cost log, decision store,
manifests, portfolio config, roster) and return plain dataclasses /
Pydantic models. No new persistence layer.

Status definitions for an agent:
  * **active**  — last activity in the last 24h
  * **idle**    — last activity in the last 14d but not 24h
  * **stale**   — never invoked, OR last activity > 14d ago
  * **error**   — reserved for Phase B once the cost log carries a status
                  field; today this never fires.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from minions.activity import is_role_running
from minions.agents.roster import (
    AUDIT,
    SHARED_EXECUTIVE,
    SHARED_SPECIALIST,
    build_project_agents,
    build_shared_agents,
)
from minions.config.portfolio import PortfolioConfig, load_portfolio_config
from minions.cost import CostEntry, read_log
from minions.crews.engineer_runs_store import EngineerRunRecord
from minions.dashboard.schedule import NextRun, next_run_for_role
from minions.models.audit import AuditFinding
from minions.models.decision import Decision, DecisionStatus
from minions.models.manifest import Manifest, load_active_manifests

Status = Literal["active", "idle", "stale", "error"]

ACTIVE_THRESHOLD_HOURS = 24.0
IDLE_THRESHOLD_DAYS = 14


@dataclass(frozen=True)
class AgentSummary:
    """One row in the agents page — represents (scope, role) collapsed across seats.

    Phase A intentionally aggregates across seat_index because the cost log
    doesn't carry seat information yet. Multi-seat roles list every seat
    label so the operator sees who shares the bucket.
    """

    scope: Literal["project", "shared"]
    project: str | None
    role: str
    tier: str
    seats: int
    seat_labels: list[str]
    last_activity: datetime | None
    last_decision_id: str | None  # most recent decision id this (project, role) touched
    calls_7d: int
    cost_7d_usd: float
    calls_total: int
    cost_total_usd: float
    running_now: bool = False  # has an in-flight crew_started in the activity log
    next_run: NextRun | None = None

    @property
    def status(self) -> Status:
        if self.running_now:
            return "active"
        if self.last_activity is None:
            return "stale"
        now = datetime.now(tz=UTC)
        delta = now - self.last_activity
        if delta < timedelta(hours=ACTIVE_THRESHOLD_HOURS):
            return "active"
        if delta < timedelta(days=IDLE_THRESHOLD_DAYS):
            return "idle"
        return "stale"

    @property
    def primary_label(self) -> str:
        """First seat's label — what we headline the card with."""
        return self.seat_labels[0] if self.seat_labels else f"{self.role}@{self.scope_label}"

    @property
    def scope_label(self) -> str:
        return self.project or "org"


@dataclass(frozen=True)
class SprintBoard:
    """One project's decisions grouped into kanban columns."""

    project: str
    pending: list[Decision] = field(default_factory=list)
    approved: list[Decision] = field(default_factory=list)
    in_progress: list[Decision] = field(default_factory=list)  # Phase B will populate
    pr_open: list[Decision] = field(default_factory=list)  # Phase B will populate
    done: list[Decision] = field(default_factory=list)

    @property
    def total(self) -> int:
        return (
            len(self.pending)
            + len(self.approved)
            + len(self.in_progress)
            + len(self.pr_open)
            + len(self.done)
        )


@dataclass(frozen=True)
class DashboardData:
    """Top-level snapshot — all the data the three pages need."""

    generated_at: datetime
    agents: list[AgentSummary]
    decisions: list[Decision]
    sprint_boards: dict[str, SprintBoard]
    cost_log_entries: int
    audit_findings: list[AuditFinding] = field(default_factory=list)

    @property
    def pending_count(self) -> int:
        return sum(1 for d in self.decisions if d.status is DecisionStatus.PENDING)

    @property
    def approved_count(self) -> int:
        return sum(1 for d in self.decisions if d.status is DecisionStatus.APPROVED)

    @property
    def rejected_count(self) -> int:
        return sum(1 for d in self.decisions if d.status is DecisionStatus.REJECTED)

    @property
    def executed_count(self) -> int:
        return sum(1 for d in self.decisions if d.status is DecisionStatus.EXECUTED)


# ---------------------------------------------------------------------------
# Builders.
# ---------------------------------------------------------------------------


def build_agent_summaries(
    *,
    manifests: dict[str, Manifest],
    portfolio: PortfolioConfig,
    cost_log_path: Path | None = None,
    now: datetime | None = None,
) -> list[AgentSummary]:
    """Build one AgentSummary per (scope, role) across project + shared layers.

    Each agent is enriched with its activity from the cost log. Roles that
    have never been invoked still appear (status="stale"), so the operator
    can see "what could I be using but am not?".
    """
    now = now or datetime.now(tz=UTC)
    cutoff_7d = now - timedelta(days=7)
    entries = read_log(cost_log_path)

    # (project|None, role) → list of CostEntry
    by_bucket: dict[tuple[str | None, str], list[CostEntry]] = {}
    for e in entries:
        key = (e.project or None, e.role or "")
        by_bucket.setdefault(key, []).append(e)

    out: list[AgentSummary] = []

    # Project-scoped agents — one row per (project, role), seats listed inline.
    for project, manifest in sorted(manifests.items()):
        seats_by_role: dict[str, list[str]] = {}
        tier_by_role: dict[str, str] = {}
        for agent in build_project_agents(manifest):
            role_value = agent.role.value if hasattr(agent.role, "value") else str(agent.role)
            seats_by_role.setdefault(role_value, []).append(agent.label)
            tier_by_role[role_value] = str(agent.tier)
        for role_value, labels in sorted(seats_by_role.items()):
            out.append(
                _summarize_bucket(
                    scope="project",
                    project=project,
                    role=role_value,
                    tier=tier_by_role[role_value],
                    seat_labels=labels,
                    bucket=by_bucket.get((project, role_value), []),
                    cutoff_7d=cutoff_7d,
                )
            )

    # Shared layers (executive / specialist / audit) — one row per role.
    seen_shared: set[str] = set()
    for layer in (SHARED_EXECUTIVE, SHARED_SPECIALIST, AUDIT):
        for agent in build_shared_agents(portfolio, layer):
            role_value = agent.role.value if hasattr(agent.role, "value") else str(agent.role)
            if role_value in seen_shared:
                continue
            seen_shared.add(role_value)
            shared_agents = [
                a
                for a in build_shared_agents(portfolio, layer)
                if (a.role.value if hasattr(a.role, "value") else str(a.role)) == role_value
            ]
            seat_labels = [a.label for a in shared_agents]
            out.append(
                _summarize_bucket(
                    scope="shared",
                    project=None,
                    role=role_value,
                    tier=str(agent.tier),
                    seat_labels=seat_labels,
                    bucket=by_bucket.get((None, role_value), []),
                    cutoff_7d=cutoff_7d,
                )
            )

    return out


def _summarize_bucket(
    *,
    scope: Literal["project", "shared"],
    project: str | None,
    role: str,
    tier: str,
    seat_labels: list[str],
    bucket: list[CostEntry],
    cutoff_7d: datetime,
) -> AgentSummary:
    next_run = next_run_for_role(role)
    running = bool(project) and is_role_running(project or "", role)
    if not bucket:
        return AgentSummary(
            scope=scope,
            project=project,
            role=role,
            tier=tier,
            seats=len(seat_labels),
            seat_labels=seat_labels,
            last_activity=None,
            last_decision_id=None,
            calls_7d=0,
            cost_7d_usd=0.0,
            calls_total=0,
            cost_total_usd=0.0,
            running_now=running,
            next_run=next_run,
        )
    sorted_entries = sorted(bucket, key=lambda e: e.timestamp)
    last = sorted_entries[-1]
    in_window = [e for e in bucket if e.timestamp >= cutoff_7d]
    return AgentSummary(
        scope=scope,
        project=project,
        role=role,
        tier=tier,
        seats=len(seat_labels),
        seat_labels=seat_labels,
        last_activity=last.timestamp,
        last_decision_id=last.decision_id or None,
        calls_7d=len(in_window),
        cost_7d_usd=sum(e.cost_usd for e in in_window),
        calls_total=len(bucket),
        cost_total_usd=sum(e.cost_usd for e in bucket),
        running_now=running,
        next_run=next_run,
    )


def build_sprint_board(
    *,
    project: str,
    decisions: list[Decision],
    engineer_runs: list[EngineerRunRecord] | None = None,
    activity_path: Path | None = None,
) -> SprintBoard:
    """Group a project's decisions into kanban columns.

    Column rules:
      * **Pending** — Decision.status == PENDING.
      * **Approved** — APPROVED + no engineer_runs entry yet for this id.
      * **In progress** — APPROVED with an in-flight ``crew_started`` for
        ``engineer`` on this project (activity log), OR an EngineerRunRecord
        without a ``pr_url`` (engineer started but didn't open a PR yet).
      * **PR open** — EXECUTED with a ``pr_url`` (real PR live).
      * **Done / Rejected** — REJECTED or EXECUTED+merged (we don't yet
        track merge — see Phase 6 follow-up).
    """
    runs = {r.decision_id: r for r in (engineer_runs or [])}

    def _by_status(status: DecisionStatus) -> list[Decision]:
        return sorted(
            (d for d in decisions if d.project == project and d.status is status),
            key=lambda d: d.created_at,
            reverse=True,
        )

    pending = _by_status(DecisionStatus.PENDING)

    # Use the activity log to detect APPROVED decisions where the engineer
    # crew is currently running (no run record yet, but a crew_started in
    # the last RUNNING_WINDOW_SECONDS).
    from minions.activity import running_now

    in_flight_decision_ids = {
        e.decision_id
        for e in running_now(path=activity_path)
        if e.crew == "engineer" and e.project == project and e.decision_id
    }

    approved_raw = _by_status(DecisionStatus.APPROVED)
    approved: list[Decision] = []
    in_progress: list[Decision] = []
    for d in approved_raw:
        rec = runs.get(str(d.id))
        if (
            str(d.id) in in_flight_decision_ids
            or rec is not None
            and rec.pr_url is None
            and not rec.dry_run
        ):
            in_progress.append(d)
        else:
            approved.append(d)

    pr_open: list[Decision] = []
    done: list[Decision] = []
    for d in decisions:
        if d.project != project:
            continue
        if d.status is DecisionStatus.EXECUTED:
            rec = runs.get(str(d.id))
            # Merged or closed PRs land in Done; only "open" stays in PR open.
            if rec and rec.pr_state in ("merged", "closed"):
                done.append(d)
            elif rec and rec.pr_url or d.pr_url:
                pr_open.append(d)
            else:
                in_progress.append(d)
        elif d.status is DecisionStatus.REJECTED:
            done.append(d)

    pr_open.sort(key=lambda d: d.created_at, reverse=True)
    done.sort(key=lambda d: d.created_at, reverse=True)
    in_progress.sort(key=lambda d: d.created_at, reverse=True)

    return SprintBoard(
        project=project,
        pending=pending,
        approved=approved,
        in_progress=in_progress,
        pr_open=pr_open,
        done=done,
    )


def daily_cost_series(
    *,
    days: int = 14,
    cost_log_path: Path | None = None,
    now: datetime | None = None,
) -> dict[str, list[tuple[datetime, float]]]:
    """For each project, return a list of ``(day, cost_usd)`` tuples for the
    last ``days`` days (UTC, oldest first). Days with zero cost are included
    so the resulting series is uniform — perfect for a sparkline.
    """
    now = now or datetime.now(tz=UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days_grid = [today - timedelta(days=days - 1 - i) for i in range(days)]

    entries = read_log(cost_log_path)
    if not entries:
        return {}

    # Bucket: project → day_iso → cost
    buckets: dict[str, dict[str, float]] = {}
    for e in entries:
        if not e.project:
            continue
        day = e.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if day < days_grid[0]:
            continue
        day_key = day.isoformat()
        buckets.setdefault(e.project, {})[day_key] = (
            buckets.get(e.project, {}).get(day_key, 0.0) + e.cost_usd
        )

    out: dict[str, list[tuple[datetime, float]]] = {}
    for project, days_map in buckets.items():
        out[project] = [(d, days_map.get(d.isoformat(), 0.0)) for d in days_grid]
    return out


def cost_series_for(
    project: str,
    role: str,
    *,
    days: int = 14,
    cost_log_path: Path | None = None,
    now: datetime | None = None,
) -> list[tuple[datetime, float]]:
    """Per (project, role) daily series — for the agent card sparkline."""
    now = now or datetime.now(tz=UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    days_grid = [today - timedelta(days=days - 1 - i) for i in range(days)]
    entries = read_log(cost_log_path)
    if not entries:
        return [(d, 0.0) for d in days_grid]

    by_day: dict[str, float] = {}
    for e in entries:
        if e.project != project or e.role != role:
            continue
        day = e.timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
        if day < days_grid[0]:
            continue
        by_day[day.isoformat()] = by_day.get(day.isoformat(), 0.0) + e.cost_usd
    return [(d, by_day.get(d.isoformat(), 0.0)) for d in days_grid]


def build_dashboard_data(
    *,
    projects_dir: Path,
    portfolio_config_path: Path,
    decision_store_path: Path,
    cost_log_path: Path | None = None,
    engineer_runs_store_path: Path | None = None,
    activity_log_path: Path | None = None,
    audit_findings_store_path: Path | None = None,
    now: datetime | None = None,
) -> DashboardData:
    """One-shot loader called by every Streamlit refresh."""
    now = now or datetime.now(tz=UTC)
    manifests = load_active_manifests(projects_dir)
    portfolio = load_portfolio_config(portfolio_config_path)
    from minions.approval.store_factory import make_decision_store

    decisions = make_decision_store(decision_store_path).list_all()

    engineer_runs: list[EngineerRunRecord] = []
    if engineer_runs_store_path is not None:
        from minions.crews.engineer_runs_store_factory import make_engineer_runs_store

        engineer_runs = make_engineer_runs_store(engineer_runs_store_path).list_all()

    agents = build_agent_summaries(
        manifests=manifests,
        portfolio=portfolio,
        cost_log_path=cost_log_path,
        now=now,
    )
    boards = {
        name: build_sprint_board(
            project=name,
            decisions=decisions,
            engineer_runs=[r for r in engineer_runs if r.project == name],
            activity_path=activity_log_path,
        )
        for name in sorted(manifests.keys())
    }
    audit_findings: list[AuditFinding] = []
    if audit_findings_store_path is not None:
        from minions.audit.store_factory import make_audit_findings_store

        audit_findings = make_audit_findings_store(audit_findings_store_path).list_all()

    return DashboardData(
        generated_at=now,
        agents=agents,
        decisions=decisions,
        sprint_boards=boards,
        cost_log_entries=len(read_log(cost_log_path)),
        audit_findings=audit_findings,
    )
