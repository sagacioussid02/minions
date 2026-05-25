"""Per-project flow-control helpers — open-PR cap + fix-decision dedupe.

These guard against the runaway-PR-loop failure mode where ``pr_followup``
queues a new fix Decision for every engineer-run row, and each fix in turn
becomes a fresh row with its own ``followup_attempts`` counter. The
in-place fix path masks the bug while it works; the moment a branch is
gone or has operator commits, the fix falls through to a fresh PR and the
loop unfolds.

Two pure functions:

* :func:`distinct_open_pr_count` collapses ``EngineerRunRecord`` rows by
  ``pr_number`` (the GitHub-side identity) so callers see "PRs the sweep
  considers open" rather than "engineer runs whose pr_state happens to be
  open". A record is open when ``pr_state in (None, "open")`` AND it has
  a ``pr_number``.
* :func:`has_open_fix_decision_for_pr` checks the Decision store for an
  already-queued pr_followup fix targeting the same PR number, so we
  never double-queue.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from minions.models.decision import DecisionStatus

if TYPE_CHECKING:
    from minions.approval.store import DecisionStore
    from minions.crews.engineer_runs_store import EngineerRunStore


def distinct_open_pr_count(
    *, project: str, engineer_runs_store: EngineerRunStore,
) -> int:
    """Count *distinct* open PR numbers the sweep is aware of for ``project``.

    Rows without ``pr_number`` (e.g. dry-runs, skipped runs) do not count.
    Multiple engineer-run rows pointing at the same ``pr_number`` collapse
    to one — that is what makes this the right number to cap on.
    """
    open_numbers: set[int] = set()
    for r in engineer_runs_store.list_all():
        if r.project != project:
            continue
        if r.pr_number is None:
            continue
        if (r.pr_state or "open") in ("open",) or r.pr_state is None:
            open_numbers.add(r.pr_number)
    return len(open_numbers)


def has_open_fix_decision_for_pr(
    *, project: str, pr_number: int, store: DecisionStore,
) -> bool:
    """True when a pending/approved pr_followup fix already targets ``pr_number``.

    The match is by the extras payload ``existing_pr_number`` (set by
    ``pr_followup`` when it queues the fix). Falls back to a substring scan
    of the Decision summary as a belt-and-braces check for older Decisions
    that were filed before the extras pattern landed.
    """
    needle = f"PR #{pr_number}"
    statuses = (DecisionStatus.PENDING, DecisionStatus.APPROVED)
    for d in store.list_all():
        if d.project != project:
            continue
        if d.status not in statuses:
            continue
        if d.proposer_role not in ("pr_followup", "conflict_resolution"):
            continue
        extras = getattr(d, "model_extra", None) or {}
        if extras.get("existing_pr_number") == pr_number:
            return True
        if needle in (d.summary or ""):
            return True
    return False


__all__ = ["distinct_open_pr_count", "has_open_fix_decision_for_pr"]
