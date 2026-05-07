"""High-level approval service used by the CLI and crews.

The cross-process flow is intentionally simple: the planner persists the
Decision and notifies; later, the operator's ``decisions approve/reject``
command updates the store directly. The LangGraph graph in
:mod:`minions.approval.graph` represents the same flow as a durable
in-process state machine and is invoked by long-running orchestrator
processes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from minions.approval.store import DecisionStore
from minions.models.decision import Decision, DecisionStatus
from minions.notify.base import Notifier

DEFAULT_TIMEOUT_HOURS = 72


def submit_for_approval(
    decision: Decision,
    *,
    store: DecisionStore,
    notifier: Notifier,
) -> Decision:
    """Persist a Decision as PENDING and notify the operator.

    No-ops for the actual gate are intentional here — the gate lives in the
    operator's response (CLI or magic-link click), which arrives later.
    """
    decision.status = DecisionStatus.PENDING
    store.save(decision)
    notifier.notify_approval_request(decision)
    return decision


def resolve(
    decision_id: UUID | str,
    *,
    store: DecisionStore,
    notifier: Notifier,
    action: str,
    reason: str | None = None,
) -> Decision:
    """Resolve a pending Decision.

    ``action`` is ``"approve"`` or ``"reject"``. The store is updated and
    the notifier is informed (proposing agent / audit log).
    """
    if action not in {"approve", "reject"}:
        raise ValueError(f"action must be 'approve' or 'reject', got {action!r}")
    new_status = DecisionStatus.APPROVED if action == "approve" else DecisionStatus.REJECTED
    decision = store.update_status(decision_id, new_status, reason=reason)
    notifier.notify_decision_resolved(decision)
    return decision


def sweep_timeouts(
    *,
    store: DecisionStore,
    notifier: Notifier,
    ttl_hours: float = DEFAULT_TIMEOUT_HOURS,
    now: datetime | None = None,
) -> list[Decision]:
    """Auto-reject pending decisions older than ``ttl_hours``.

    Default 72h matches the magic-link token TTL — once the link expires,
    the decision is no longer actionable from email anyway, so we close it
    out and surface a clean queue. The auto-rejected decision still flows
    through the standard ``resolve`` path so the notifier sees it and the
    audit log is consistent.

    Returns the list of decisions that were timed out (empty if none).
    """
    now = now or datetime.now(tz=UTC)
    cutoff = now - timedelta(hours=ttl_hours)
    timed_out: list[Decision] = []
    for d in store.list_by_status(DecisionStatus.PENDING):
        if d.created_at <= cutoff:
            resolved = resolve(
                d.id,
                store=store,
                notifier=notifier,
                action="reject",
                reason=f"timeout (no operator response in {ttl_hours:g}h)",
            )
            timed_out.append(resolved)
    return timed_out
