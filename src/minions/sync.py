"""GitHub PR state sync — closes the sprint board loop.

When the operator merges a PR on GitHub, nothing tells the local Decision
Store. ``sync_pr_status`` walks every persisted engineer run that has a
``pr_url`` but isn't yet marked merged, hits the GitHub API to learn the
real PR state, and writes back ``pr_state`` / ``merged_at``.

The sprint board reads those fields:
  * ``pr_state == "merged"`` → 📦 Done
  * ``pr_state == "closed"`` (without merge) → 📦 Done (closed without merge)
  * otherwise → 🔍 PR open
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from minions.approval.store import DecisionStore
from minions.crews.engineer_runs_store import EngineerRunRecord, EngineerRunStore
from minions.models.decision import DecisionStatus

if TYPE_CHECKING:
    from minions.github.client import GitHubClient
    from minions.models.manifest import Manifest

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncOutcome:
    decision_id: str
    project: str
    before: str | None  # previous pr_state
    after: str | None   # new pr_state
    error: str | None = None

    @property
    def changed(self) -> bool:
        return self.before != self.after


@dataclass(frozen=True)
class SyncReport:
    outcomes: list[SyncOutcome]

    @property
    def merged(self) -> int:
        return sum(1 for o in self.outcomes if o.after == "merged")

    @property
    def changed(self) -> int:
        return sum(1 for o in self.outcomes if o.changed)

    @property
    def errors(self) -> int:
        return sum(1 for o in self.outcomes if o.error is not None)


def _pr_state_from_response(*, merged: bool, state: str) -> str:
    if merged:
        return "merged"
    if state == "closed":
        return "closed"
    return "open"


def sync_record(
    record: EngineerRunRecord,
    *,
    github: "GitHubClient",
    store: EngineerRunStore,
    now: datetime | None = None,
) -> SyncOutcome:
    """Sync a single record. Returns the outcome (changed or unchanged)."""
    if record.pr_number is None:
        return SyncOutcome(
            decision_id=record.decision_id,
            project=record.project,
            before=record.pr_state,
            after=record.pr_state,
            error="no pr_number on record",
        )
    now = now or datetime.now(tz=UTC)
    try:
        pr = github.get_pull_request(record.pr_number)
    except Exception as e:  # noqa: BLE001 — sync must not crash on transient errors
        return SyncOutcome(
            decision_id=record.decision_id,
            project=record.project,
            before=record.pr_state,
            after=record.pr_state,
            error=str(e)[:200],
        )

    new_state = _pr_state_from_response(merged=pr.merged, state=pr.state)
    merged_at: datetime | None = record.merged_at
    if pr.merged and pr.merged_at and merged_at is None:
        try:
            merged_at = datetime.fromisoformat(pr.merged_at.replace("Z", "+00:00"))
        except ValueError:
            merged_at = None

    updated = record.model_copy(
        update={
            "pr_state": new_state,
            "merged_at": merged_at,
            "last_synced_at": now,
        }
    )
    store.update(updated)
    return SyncOutcome(
        decision_id=record.decision_id,
        project=record.project,
        before=record.pr_state,
        after=new_state,
    )


_PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def _pr_number_from_url(url: str | None) -> int | None:
    if not url:
        return None
    m = _PR_NUMBER_RE.search(url)
    return int(m.group(1)) if m else None


def _backfill_records_from_decisions(
    *,
    store: EngineerRunStore,
    decision_store: DecisionStore,
) -> int:
    """Create stub run records for EXECUTED decisions that don't have one.

    Lets ``sync_pr_status`` work for decisions implemented before the
    EngineerRunStore landed (anything pre-Phase-B.2). Parses ``pr_number``
    from the decision's ``pr_url``.
    """
    existing = {r.decision_id for r in store.list_all()}
    created = 0
    for d in decision_store.list_all():
        if d.status is not DecisionStatus.EXECUTED:
            continue
        if d.pr_url is None:
            continue
        if str(d.id) in existing:
            continue
        pr_number = _pr_number_from_url(d.pr_url)
        if pr_number is None:
            continue
        store.update(
            EngineerRunRecord(
                decision_id=str(d.id),
                project=d.project,
                completed_at=datetime.now(tz=UTC),
                pr_url=d.pr_url,
                pr_number=pr_number,
                branch_name=None,  # unknown — pre-store decision
            )
        )
        created += 1
    return created


def sync_pr_status(
    *,
    store: EngineerRunStore,
    open_github_client: "Callable[[Manifest], GitHubClient | None]",
    manifests: dict[str, "Manifest"],
    decision_store: DecisionStore | None = None,
    now: datetime | None = None,
) -> SyncReport:
    """Walk every record with a pr_url that isn't yet marked merged.

    ``open_github_client`` is the same factory used by the cron entrypoints
    (CLI's ``_open_github_client``). Records for projects whose manifest is
    no longer active or whose source isn't GitHub are skipped silently.

    When ``decision_store`` is supplied, pre-Phase-B.2 EXECUTED decisions
    (which have a ``pr_url`` on the Decision but no run record) are backfilled
    with a stub record so they participate in the sync.
    """
    if decision_store is not None:
        _backfill_records_from_decisions(store=store, decision_store=decision_store)

    outcomes: list[SyncOutcome] = []
    # Cache one client per project so we don't reopen for each run.
    clients: dict[str, "GitHubClient | None"] = {}

    for record in store.list_all():
        if record.pr_url is None:
            continue
        if record.pr_state == "merged":
            continue  # terminal — no point re-checking

        manifest = manifests.get(record.project)
        if manifest is None:
            outcomes.append(
                SyncOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    before=record.pr_state,
                    after=record.pr_state,
                    error="manifest not found for project",
                )
            )
            continue
        if record.project not in clients:
            try:
                clients[record.project] = open_github_client(manifest)
            except Exception as e:  # noqa: BLE001
                clients[record.project] = None
                logger.debug("sync: client open failed for %s: %s", record.project, e)
        gh = clients[record.project]
        if gh is None:
            outcomes.append(
                SyncOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    before=record.pr_state,
                    after=record.pr_state,
                    error="no GitHub client (project may be local-only or repo TBD)",
                )
            )
            continue
        outcomes.append(sync_record(record, github=gh, store=store, now=now))

    # Best-effort close all the clients we opened.
    for c in clients.values():
        if c is not None:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    return SyncReport(outcomes=outcomes)
