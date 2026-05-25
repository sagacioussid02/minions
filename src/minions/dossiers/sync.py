"""Reconcile dossier draft status against Decision + EngineerRun state.

Runs as a small idempotent pass after the PR review loop. It looks at every
non-terminal ``DossierDraft`` (status in ``drafted``, ``pr_open``) and asks:

* Is the linked Decision still pending/approved? -> leave alone or flip to
  ``pr_open`` if an EngineerRunRecord exists.
* Did the linked PR merge? -> flip the draft to ``merged`` and supersede any
  older non-terminal drafts for the same project.
* Was the Decision rejected / timed out? -> flip the draft to ``rejected``.

Designed to be called from the existing daily/pr-review cron without
mutating any of the underlying stores' contracts.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from minions.crews.engineer_runs_store import EngineerRunStore
from minions.dossiers.exec_events import record_understanding_delta
from minions.dossiers.refresh import (
    DRAFT_ID_KEY,
    is_dossier_refresh_decision,
)
from minions.dossiers.store_factory import DossierStoreLike
from minions.models.decision import Decision, DecisionStatus
from minions.models.dossier import DossierDraft, DossierStatus

if TYPE_CHECKING:
    from minions.approval.store import DecisionStore
    from minions.learning.store_factory import AgentLearningStoreLike


@dataclass(frozen=True)
class SyncOutcome:
    draft_id: str
    project: str
    from_status: DossierStatus
    to_status: DossierStatus
    reason: str


@dataclass(frozen=True)
class SyncReport:
    transitions: list[SyncOutcome]

    @property
    def merged(self) -> int:
        return sum(1 for o in self.transitions if o.to_status is DossierStatus.MERGED)

    @property
    def rejected(self) -> int:
        return sum(1 for o in self.transitions if o.to_status is DossierStatus.REJECTED)

    @property
    def superseded(self) -> int:
        return sum(1 for o in self.transitions if o.to_status is DossierStatus.SUPERSEDED)


def sync_dossier_drafts(
    *,
    dossier_store: DossierStoreLike,
    decision_store: DecisionStore,
    engineer_runs_store: EngineerRunStore,
    learning_store: AgentLearningStoreLike | None = None,
) -> SyncReport:
    """Walk non-terminal dossier drafts and reconcile against linked records.

    Idempotent — re-running on already-resolved drafts is a no-op.
    """
    transitions: list[SyncOutcome] = []
    drafts = dossier_store.list_all()

    # Index decisions by id for cheap lookup.
    decisions_by_id: dict[str, Decision] = {}
    for d in decision_store.list_all():
        if is_dossier_refresh_decision(d):
            decisions_by_id[str(d.id)] = d

    for draft in drafts:
        if draft.status in (
            DossierStatus.MERGED,
            DossierStatus.REJECTED,
            DossierStatus.SUPERSEDED,
        ):
            continue

        decision = _find_decision_for_draft(draft, decisions_by_id)
        if decision is None:
            # Draft was persisted but no Decision exists (yet). Leave alone;
            # discovery re-runs will eventually file one or the draft
            # eventually gets superseded by a newer one (see below).
            continue

        outcome = _resolve_one(
            draft=draft,
            decision=decision,
            engineer_runs_store=engineer_runs_store,
            dossier_store=dossier_store,
        )
        if outcome is not None:
            transitions.append(outcome)
            if outcome.to_status is DossierStatus.MERGED and learning_store is not None:
                # The draft reference still points at the updated row (status
                # was mutated by _transition above), so we re-fetch the prior
                # merged dossier — *excluding* the one we just flipped — for
                # the diff.
                with suppress(Exception):
                    prior = _prior_merged_excluding(
                        dossier_store=dossier_store,
                        project=draft.project,
                        exclude_id=str(draft.id),
                    )
                    record_understanding_delta(
                        prior=prior,
                        new=draft,
                        learning_store=learning_store,
                    )

    # Supersede: at most one non-terminal draft per project. If multiple
    # exist (newer drafts piled up while older ones sat unresolved), keep
    # only the newest and mark the rest superseded.
    transitions.extend(
        _supersede_older_drafts(dossier_store=dossier_store, drafts=dossier_store.list_all())
    )

    return SyncReport(transitions=transitions)


def _prior_merged_excluding(
    *,
    dossier_store: DossierStoreLike,
    project: str,
    exclude_id: str,
) -> DossierDraft | None:
    """Most-recent merged draft for ``project`` other than ``exclude_id``.

    Used by the executive-event emission after a brand-new merge — the
    just-merged draft itself is in ``latest_merged`` already, so we have to
    skip it explicitly to find the genuine predecessor.
    """
    merged = dossier_store.list_for_project(project, status=DossierStatus.MERGED, limit=10)
    for candidate in merged:
        if str(candidate.id) != exclude_id:
            return candidate
    return None


def _find_decision_for_draft(
    draft: DossierDraft, decisions_by_id: dict[str, Decision]
) -> Decision | None:
    for decision in decisions_by_id.values():
        extra = getattr(decision, "model_extra", None) or {}
        if extra.get(DRAFT_ID_KEY) == str(draft.id):
            return decision
    return None


def _resolve_one(
    *,
    draft: DossierDraft,
    decision: Decision,
    engineer_runs_store: EngineerRunStore,
    dossier_store: DossierStoreLike,
) -> SyncOutcome | None:
    # Rejected / timed-out decisions tank the draft.
    if decision.status in (DecisionStatus.REJECTED, DecisionStatus.TIMED_OUT):
        return _transition(
            draft,
            DossierStatus.REJECTED,
            f"linked decision {decision.id} ended at {decision.status.value}",
            dossier_store=dossier_store,
        )

    if decision.status in (DecisionStatus.PENDING, DecisionStatus.APPROVED):
        # No-op; the execute-approved sweep will flip drafted -> pr_open
        # when the engineer crew opens the PR.
        return None

    # EXECUTED — look up the engineer run for the merge state.
    run = engineer_runs_store.get(str(decision.id))
    if run is None:
        # Engineer run not yet recorded; leave the draft as-is for the next sync.
        return None

    pr_state = (run.pr_state or "").lower()
    if pr_state == "merged":
        return _transition(
            draft,
            DossierStatus.MERGED,
            f"linked PR {run.pr_url} merged",
            dossier_store=dossier_store,
            pr_url=run.pr_url,
        )
    if pr_state in ("closed", "abandoned"):
        return _transition(
            draft,
            DossierStatus.REJECTED,
            f"linked PR {run.pr_url} closed without merge",
            dossier_store=dossier_store,
            pr_url=run.pr_url,
        )

    # PR open / unknown — make sure the draft reflects pr_open.
    if draft.status is DossierStatus.DRAFTED:
        return _transition(
            draft,
            DossierStatus.PR_OPEN,
            f"engineer run recorded; PR at {run.pr_url}",
            dossier_store=dossier_store,
            pr_url=run.pr_url,
        )
    return None


def _transition(
    draft: DossierDraft,
    to: DossierStatus,
    reason: str,
    *,
    dossier_store: DossierStoreLike,
    pr_url: str | None = None,
) -> SyncOutcome:
    prior = draft.status
    draft.status = to
    if pr_url is not None and not draft.pr_url:
        draft.pr_url = pr_url
    if to is DossierStatus.MERGED:
        from datetime import UTC, datetime

        draft.merged_at = datetime.now(UTC)
    with suppress(Exception):
        dossier_store.save(draft)
    return SyncOutcome(
        draft_id=str(draft.id),
        project=draft.project,
        from_status=prior,
        to_status=to,
        reason=reason,
    )


def _supersede_older_drafts(
    *,
    dossier_store: DossierStoreLike,
    drafts: list[DossierDraft],
) -> list[SyncOutcome]:
    """Keep only the newest non-terminal draft per project; supersede the rest."""
    by_project: dict[str, list[DossierDraft]] = {}
    for d in drafts:
        if d.status in (DossierStatus.DRAFTED, DossierStatus.PR_OPEN):
            by_project.setdefault(d.project, []).append(d)

    out: list[SyncOutcome] = []
    for project, rows in by_project.items():
        if len(rows) <= 1:
            continue
        rows.sort(key=lambda d: d.generated_at, reverse=True)
        keep = rows[0]
        for older in rows[1:]:
            out.append(
                _transition(
                    older,
                    DossierStatus.SUPERSEDED,
                    f"newer draft {keep.id} (at {keep.commit_sha[:8]}) in flight for {project}",
                    dossier_store=dossier_store,
                )
            )
    return out


__all__ = ["SyncOutcome", "SyncReport", "sync_dossier_drafts"]
