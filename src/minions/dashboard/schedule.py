"""Schedule resolver — answers "when is this agent next expected to run?"

Today's invocation rules are static (encoded below) since the cron schedule
lives in `config/portfolio.yaml.cadence_profiles.<profile>` and the role-to-
crew mapping lives in code (planning crew uses PO/Principal/Manager, etc.).
When we move scheduling to a runtime host the source of truth shifts, but
the resolver's API stays the same.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Literal

# Map (crew → cron expression) for v0_frugal cadence. Fields match standard
# 5-field cron (minute hour day-of-month month day-of-week, where 0/7 = Sunday
# but we use 1=Mon for clarity below — the resolver handles both).
V0_FRUGAL_SCHEDULES: dict[str, str] = {
    "weekly_planning": "0 9 * * 1",  # Mon 09:00 local
    "daily_monitor": "0 9 * * *",  # 09:00 local every day
    "friday_digest": "0 16 * * 5",  # Fri 16:00 local
}

# Which roles are invoked by which crews. event-driven roles (engineer,
# senior_engineer, tech_team_lead) fire when a Decision is approved, not on
# a clock. Audit/exec layers are not yet wired (Phase 9).
ROLE_TO_CREWS: dict[str, list[str]] = {
    "product_owner": ["weekly_planning"],
    "principal_engineer": ["weekly_planning"],
    "manager": ["weekly_planning"],
    "engineer": ["__on_approval__"],
    "senior_engineer": ["__on_approval__"],
    "tech_team_lead": ["__on_approval__"],
    "intern": ["__on_approval__"],
    "senior_devops": ["__on_demand__"],
    "security_champion": ["__on_demand__"],
    # Shared layers — not yet wired
    "ceo": ["__not_scheduled__"],
    "cto": ["__not_scheduled__"],
    "managing_director": ["__not_scheduled__"],
    "org_owner": ["__not_scheduled__"],
    "cloud_devops": ["__on_demand__"],
    "devsecops": ["__on_demand__"],
    "team_architect": ["__on_demand__"],
    "chief_auditor": ["__not_scheduled__"],
    "process_auditor": ["__not_scheduled__"],
    "code_auditor": ["__not_scheduled__"],
    "cost_auditor": ["__not_scheduled__"],
    "devils_advocate": ["__not_scheduled__"],
}

NextRunKind = Literal["scheduled", "on_approval", "on_demand", "not_scheduled"]


@dataclass(frozen=True)
class NextRun:
    kind: NextRunKind
    crew: str | None  # crew name when scheduled
    next_at: datetime | None
    description: str  # human-readable, e.g. "Mon 2026-05-11 09:00 local"


def next_run_for_role(
    role: str,
    *,
    now: datetime | None = None,
) -> NextRun:
    """Return the next scheduled run for ``role``, or describe its trigger."""
    crews = ROLE_TO_CREWS.get(role, ["__not_scheduled__"])
    if "__on_approval__" in crews:
        return NextRun(
            kind="on_approval",
            crew=None,
            next_at=None,
            description="On approved decision (event-driven)",
        )
    if "__on_demand__" in crews:
        return NextRun(
            kind="on_demand",
            crew=None,
            next_at=None,
            description="On-demand consult (not on a clock)",
        )
    if "__not_scheduled__" in crews:
        return NextRun(
            kind="not_scheduled",
            crew=None,
            next_at=None,
            description="Not yet scheduled (Phase 9)",
        )

    # Earliest scheduled crew wins.
    now = now or datetime.now(tz=UTC)
    best_at: datetime | None = None
    best_crew: str | None = None
    for crew in crews:
        cron = V0_FRUGAL_SCHEDULES.get(crew)
        if cron is None:
            continue
        when = _next_fire(cron, now=now)
        if when is None:
            continue
        if best_at is None or when < best_at:
            best_at = when
            best_crew = crew
    if best_at is None or best_crew is None:
        return NextRun(
            kind="not_scheduled",
            crew=None,
            next_at=None,
            description="No matching schedule",
        )
    return NextRun(
        kind="scheduled",
        crew=best_crew,
        next_at=best_at,
        description=_format_next_at(best_at, best_crew),
    )


def _format_next_at(when: datetime, crew: str) -> str:
    weekday = when.strftime("%a")
    date = when.strftime("%Y-%m-%d %H:%M")
    return f"{weekday} {date} UTC · {crew}"


def _next_fire(cron: str, *, now: datetime) -> datetime | None:
    """Resolve the next firing of a 5-field cron expression after ``now``.

    Supports the subset we actually use: integer minute, integer hour,
    ``*`` for day/month, and integer or ``*`` for day-of-week. Good enough
    for the three v0 schedules; extend if we add unusual ones.
    """
    parts = cron.split()
    if len(parts) != 5:
        return None
    try:
        minute = int(parts[0])
        hour = int(parts[1])
    except ValueError:
        return None
    dow = parts[4]  # day-of-week — we only care about this
    target_dow: int | None = None
    if dow != "*":
        try:
            n = int(dow)
            # 0/7 = Sunday in cron, Python: Monday=0..Sunday=6
            target_dow = 6 if n in (0, 7) else n - 1
        except ValueError:
            return None

    fire_time = time(hour=hour, minute=minute)
    today_at = now.replace(hour=fire_time.hour, minute=fire_time.minute, second=0, microsecond=0)
    candidate = today_at if today_at > now else today_at + timedelta(days=1)

    # Walk forward up to 7 days to find the matching weekday.
    for _ in range(8):
        if target_dow is None or candidate.weekday() == target_dow:
            return candidate
        candidate += timedelta(days=1)
    return None
