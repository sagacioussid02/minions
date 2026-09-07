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

from minions.scheduled.agent_memory_demote import (
    AgentMemoryDemoteReport,
    run_agent_memory_demote,
)
from minions.scheduled.assign_backlog_tasks import (
    AssignBacklogReport,
    AssignmentOutcome,
    run_assign_backlog_tasks,
)
from minions.scheduled.crew_heartbeat import (
    CrewHeartbeatReport,
    HeartbeatOutcome,
    run_crew_heartbeat,
)
from minions.scheduled.daily_monitor import (
    DailyMonitorReport,
    ProjectMonitorEntry,
    run_daily_monitor,
)
from minions.scheduled.discovery import (
    DiscoveryOutcome,
    DiscoverySweepReport,
    run_discovery_sweep,
)
from minions.scheduled.execute_approved import (
    ExecuteApprovedReport,
    ExecuteOutcome,
    run_execute_approved,
)
from minions.scheduled.friday_digest import FridayDigestReport, run_friday_digest
from minions.scheduled.monthly_portfolio_review import (
    MonthlyReviewReport,
    run_monthly_portfolio_review,
)
from minions.scheduled.pr_followup import (
    PRFollowupOutcome,
    PRFollowupReport,
    run_pr_followup,
)
from minions.scheduled.pr_review_loop import (
    PRReviewLoopOutcome,
    PRReviewLoopReport,
    run_pr_review_loop,
)
from minions.scheduled.scrum import ScrumOutcome, ScrumReport, run_scrum
from minions.scheduled.site_sentry import (
    ProbeSample,
    RenewalStatus,
    SiteSentryOutcome,
    SiteSentryReport,
    renewal_statuses,
    run_site_sentry,
)
from minions.scheduled.weekly_planning import (
    PlanningOutcome,
    WeeklyPlanningReport,
    run_weekly_planning,
)

__all__ = [
    "DailyMonitorReport",
    "AgentMemoryDemoteReport",
    "AssignBacklogReport",
    "AssignmentOutcome",
    "CrewHeartbeatReport",
    "DiscoveryOutcome",
    "DiscoverySweepReport",
    "ExecuteApprovedReport",
    "ExecuteOutcome",
    "FridayDigestReport",
    "HeartbeatOutcome",
    "MonthlyReviewReport",
    "PRFollowupOutcome",
    "PRFollowupReport",
    "PRReviewLoopOutcome",
    "PRReviewLoopReport",
    "PlanningOutcome",
    "ProjectMonitorEntry",
    "ProbeSample",
    "RenewalStatus",
    "ScrumOutcome",
    "ScrumReport",
    "SiteSentryOutcome",
    "SiteSentryReport",
    "WeeklyPlanningReport",
    "run_daily_monitor",
    "run_agent_memory_demote",
    "run_assign_backlog_tasks",
    "run_crew_heartbeat",
    "run_discovery_sweep",
    "run_execute_approved",
    "run_friday_digest",
    "run_monthly_portfolio_review",
    "run_pr_followup",
    "run_pr_review_loop",
    "renewal_statuses",
    "run_scrum",
    "run_site_sentry",
    "run_weekly_planning",
]
