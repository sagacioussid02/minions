"""Scheduled entrypoints for the autonomous loop.

Each function here is a pure Python entrypoint that a runtime host (Modal,
Fly.io, system cron) wraps in a scheduled invocation. They are also callable
manually via the `minions cron <weekly|daily|friday>` CLI.

Design rules:
  * No `argparse`/CLI parsing inside these functions — the entrypoints take
    plain Python args, return a structured summary, and never call `sys.exit`.
  * No process-global state — everything threaded through the args.
  * Failures are caught per-project so one bad project does not abort the
    whole sweep.
"""

from minions.scheduled.daily_monitor import (
    DailyMonitorReport,
    ProjectMonitorEntry,
    run_daily_monitor,
)
from minions.scheduled.execute_approved import (
    ExecuteApprovedReport,
    ExecuteOutcome,
    run_execute_approved,
)
from minions.scheduled.friday_digest import FridayDigestReport, run_friday_digest
from minions.scheduled.pr_followup import (
    PRFollowupOutcome,
    PRFollowupReport,
    run_pr_followup,
)
from minions.scheduled.weekly_planning import (
    PlanningOutcome,
    WeeklyPlanningReport,
    run_weekly_planning,
)

__all__ = [
    "DailyMonitorReport",
    "ExecuteApprovedReport",
    "ExecuteOutcome",
    "FridayDigestReport",
    "PRFollowupOutcome",
    "PRFollowupReport",
    "PlanningOutcome",
    "ProjectMonitorEntry",
    "WeeklyPlanningReport",
    "run_daily_monitor",
    "run_execute_approved",
    "run_friday_digest",
    "run_pr_followup",
    "run_weekly_planning",
]
