"""PR owner sweep — sticky-owner cron that replaces the fix-Decision loop.

Walks every open minions-authored ``EngineerRunRecord`` and, when the PR
needs attention (CI failure or merge conflict), re-dispatches the
**original owner agent** in-place on the existing branch. No new Decision
Record is filed; ``record.followup_attempts`` is sticky to the PR. After
``manifest.flow_control.max_retries_per_pr`` failed retries the sweep
files exactly ONE Question Record to the operator and stops dispatching
that PR until the operator answers.

This is the durable fix for the runaway-loop class of failures (see the
demo_three incident 2026-05-20). Pairs with engineer preflight (which stops
broken PRs from opening in the first place).

Design parallels ``scheduled/execute_approved.py``:
* No CLI imports inside the entrypoint; ``open_github_client`` is injected.
* Per-record try/except so one bad PR doesn't abort the sweep.
* Hard cap on per-sweep dispatches so cron never blasts the whole queue.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.approval.store import DecisionStore
from minions.budget import BudgetBreachError
from minions.crews.engineer import EngineerResult, run_engineer_crew
from minions.crews.engineer_runs_store import EngineerRunRecord, EngineerRunStore
from minions.models.manifest import Manifest, load_active_manifests
from minions.models.question import QuestionRecord, QuestionStatus
from minions.notify.base import Notifier

if TYPE_CHECKING:
    from minions.github.client import GitHubClient
    from minions.questions.store_factory import QuestionStoreLike


FailureKind = Literal["ci_failure", "merge_conflict"]
OutcomeStatus = Literal[
    "retried", "healthy", "escalated", "skipped", "throttled", "error"
]
EngineerRunner = Callable[..., EngineerResult]


class OwnerSweepOutcome(BaseModel):
    decision_id: str
    project: str
    pr_url: str | None = None
    owner_agent_id: str | None = None
    status: OutcomeStatus
    attempt: int | None = None
    failure_kind: FailureKind | None = None
    reason: str | None = None
    question_id: str | None = None


class OwnerSweepReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[OwnerSweepOutcome] = Field(default_factory=list)

    @property
    def retried(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "retried")

    @property
    def escalated(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "escalated")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")


# ---------------------------------------------------------------------------
# Pure helpers — easy to test, no IO.
# ---------------------------------------------------------------------------


def _is_owner_actionable(record: EngineerRunRecord) -> bool:
    """True iff this PR is one the owner sweep is responsible for.

    Closed/merged PRs are out. Records with no ``pr_number`` (skipped or
    pre-PR-open) are out. Branches without names are out (we'd have nothing
    to push onto).
    """
    if record.pr_number is None or record.branch_name is None:
        return False
    if record.dry_run or record.skipped:
        return False
    return record.pr_state not in ("merged", "closed")


def _classify_failure(
    ci_conclusion: str | None, merge_state: str | None
) -> FailureKind | None:
    """Decide whether the PR needs a retry, and why.

    Returns ``None`` when the PR is healthy enough to leave to the standard
    review loop (CI green / pending, merge clean). ``"merge_conflict"`` wins
    over ``"ci_failure"`` so the engineer's prompt knows to resolve the
    conflict first.
    """
    if merge_state == "dirty":
        return "merge_conflict"
    if ci_conclusion == "failure":
        return "ci_failure"
    return None


def _resolve_owner(record: EngineerRunRecord, manifest: Manifest) -> str:
    """Sticky owner — never None. Legacy rows backfill to canonical seat."""
    return record.owner_agent_id or f"engineer@{manifest.name}"


# ---------------------------------------------------------------------------
# Sweep entrypoint.
# ---------------------------------------------------------------------------


def run_pr_owner_sweep(
    *,
    projects_dir: Path,
    store: DecisionStore,
    engineer_runs_store: EngineerRunStore,
    questions_store: QuestionStoreLike,
    open_github_client: Callable[[Manifest], GitHubClient | None],
    notifier: Notifier,
    api_key: str | None = None,
    dry_run: bool = True,
    cost_log_path: Path | None = None,
    max_dispatches_per_sweep: int = 5,
    runner: EngineerRunner | None = None,
) -> OwnerSweepReport:
    """Re-dispatch the original owner against any actionable open PR.

    Never files a new Decision Record. Failures past the per-PR retry cap
    surface as ONE Question Record per PR, then stay quiet.
    """
    started = datetime.now(tz=UTC).isoformat()
    runner = runner or run_engineer_crew

    manifests = load_active_manifests(projects_dir)
    outcomes: list[OwnerSweepOutcome] = []
    dispatched = 0

    for record in engineer_runs_store.list_all():
        if not _is_owner_actionable(record):
            continue
        if dispatched >= max_dispatches_per_sweep:
            outcomes.append(OwnerSweepOutcome(
                decision_id=record.decision_id, project=record.project,
                pr_url=record.pr_url, status="skipped",
                reason=f"sweep cap reached ({max_dispatches_per_sweep})",
            ))
            continue

        manifest = manifests.get(record.project)
        if manifest is None or manifest.source.kind != "github":
            outcomes.append(OwnerSweepOutcome(
                decision_id=record.decision_id, project=record.project,
                pr_url=record.pr_url, status="skipped",
                reason="project not active or not github-hosted",
            ))
            continue

        # Already escalated to the operator — stay quiet until they answer.
        if record.escalated_question_id is not None:
            outcomes.append(OwnerSweepOutcome(
                decision_id=record.decision_id, project=record.project,
                pr_url=record.pr_url, status="skipped",
                question_id=record.escalated_question_id,
                reason="awaiting operator answer on escalation",
            ))
            continue

        outcome = _process_one(
            record=record, manifest=manifest, store=store,
            engineer_runs_store=engineer_runs_store,
            questions_store=questions_store,
            open_github_client=open_github_client, notifier=notifier,
            api_key=api_key, dry_run=dry_run, cost_log_path=cost_log_path,
            runner=runner,
        )
        outcomes.append(outcome)
        if outcome.status == "retried":
            dispatched += 1

    return OwnerSweepReport(
        started_at=started,
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
    )


def _process_one(
    *,
    record: EngineerRunRecord,
    manifest: Manifest,
    store: DecisionStore,
    engineer_runs_store: EngineerRunStore,
    questions_store: QuestionStoreLike,
    open_github_client: Callable[[Manifest], GitHubClient | None],
    notifier: Notifier,
    api_key: str | None,
    dry_run: bool,
    cost_log_path: Path | None,
    runner: EngineerRunner,
) -> OwnerSweepOutcome:
    github = open_github_client(manifest)
    if github is None:
        return OwnerSweepOutcome(
            decision_id=record.decision_id, project=record.project,
            pr_url=record.pr_url, status="error",
            reason="failed to open GitHub client",
        )

    try:
        with github:
            ci_conclusion, _ = github.get_pr_check_status(record.pr_number or 0)
            merge_state = github.get_pr_merge_state(record.pr_number or 0)
            failure = _classify_failure(ci_conclusion, merge_state)

            # Always persist the fresh CI snapshot so the dashboard sees it.
            record.ci_conclusion = ci_conclusion
            record.ci_last_checked_at = datetime.now(tz=UTC)

            if failure is None:
                with suppress(Exception):
                    engineer_runs_store.update(record)
                return OwnerSweepOutcome(
                    decision_id=record.decision_id, project=record.project,
                    pr_url=record.pr_url,
                    owner_agent_id=_resolve_owner(record, manifest),
                    status="healthy",
                    reason=f"ci={ci_conclusion}, merge_state={merge_state}",
                )

            record.last_failure_kind = failure

            # Hit the cap → escalate to the operator and stop dispatching.
            cap = manifest.flow_control.max_retries_per_pr
            if record.followup_attempts >= cap:
                question_id = _escalate(
                    record=record, manifest=manifest, failure=failure,
                    questions_store=questions_store, notifier=notifier,
                    dry_run=dry_run, github=github,
                )
                if not dry_run:
                    record.escalated_question_id = question_id
                    with suppress(Exception):
                        engineer_runs_store.update(record)
                return OwnerSweepOutcome(
                    decision_id=record.decision_id, project=record.project,
                    pr_url=record.pr_url,
                    owner_agent_id=_resolve_owner(record, manifest),
                    status="escalated",
                    attempt=record.followup_attempts,
                    failure_kind=failure,
                    question_id=question_id,
                    reason=f"reached max_retries_per_pr={cap}",
                )

            decision = store.get(record.decision_id)
            if decision is None:
                return OwnerSweepOutcome(
                    decision_id=record.decision_id, project=record.project,
                    pr_url=record.pr_url, status="error",
                    reason="original Decision not found in store",
                )

            if dry_run:
                return OwnerSweepOutcome(
                    decision_id=record.decision_id, project=record.project,
                    pr_url=record.pr_url,
                    owner_agent_id=_resolve_owner(record, manifest),
                    status="retried",
                    attempt=record.followup_attempts + 1,
                    failure_kind=failure,
                    reason="dry-run — would re-dispatch owner",
                )

            try:
                result = runner(
                    decision, manifest,
                    github=github, dry_run=False, api_key=api_key,
                    cost_log_path=cost_log_path,
                    target_branch=record.branch_name,
                    existing_pr_number=record.pr_number,
                    retry_attempt=record.followup_attempts + 1,
                    is_conflict_resolution=(failure == "merge_conflict"),
                )
            except BudgetBreachError as e:
                return OwnerSweepOutcome(
                    decision_id=record.decision_id, project=record.project,
                    pr_url=record.pr_url, status="throttled", reason=str(e),
                )

            # Only count REAL attempts. Skipped/errored runner results
            # don't burn the per-PR budget — they'll get another shot next
            # tick (which is the existing engineer-crew contract).
            if not result.skipped:
                record.followup_attempts += 1
                record.last_followup_at = datetime.now(tz=UTC)

            with suppress(Exception):
                engineer_runs_store.update(record)

            with suppress(Exception):
                github.comment_on_pull_request(
                    number=record.pr_number or 0,
                    body=_retry_comment(record, failure, result, cap),
                )

            return OwnerSweepOutcome(
                decision_id=record.decision_id, project=record.project,
                pr_url=record.pr_url,
                owner_agent_id=_resolve_owner(record, manifest),
                # Distinguish "did real work" from "engineer crew skipped" so
                # operator sees the truth in the report.
                status="skipped" if result.skipped else "retried",
                attempt=record.followup_attempts,
                failure_kind=failure,
                reason=result.skip_reason if result.skipped else None,
            )
    except Exception as e:  # noqa: BLE001 — per-record isolation
        return OwnerSweepOutcome(
            decision_id=record.decision_id, project=record.project,
            pr_url=record.pr_url, status="error",
            reason=f"{type(e).__name__}: {e}",
        )


# ---------------------------------------------------------------------------
# Escalation + PR comments.
# ---------------------------------------------------------------------------


def _escalate(
    *,
    record: EngineerRunRecord,
    manifest: Manifest,
    failure: FailureKind,
    questions_store: QuestionStoreLike,
    notifier: Notifier,
    dry_run: bool,
    github: GitHubClient,
) -> str:
    """File ONE Question Record + post the handoff comment on the PR."""
    owner = _resolve_owner(record, manifest)
    question = QuestionRecord(
        project=record.project,
        asker_role="pr_owner_sweep",
        asker_agent_id=f"pr_owner_sweep@{record.project}",
        target_role="operator",
        question=(
            f"PR {record.pr_url} ({failure}) — owner {owner} hit "
            f"max_retries_per_pr={manifest.flow_control.max_retries_per_pr}. "
            "Operator action required."
        ),
        context=(
            f"Branch: {record.branch_name}\n"
            f"Owner: {owner}\n"
            f"Failure kind: {failure}\n"
            f"Last CI conclusion: {record.ci_conclusion}\n"
            f"Followup attempts: {record.followup_attempts}\n"
            f"Last followup at: {record.last_followup_at}\n\n"
            "Suggested actions:\n"
            "  - merge after manual fix\n"
            "  - close the PR and let the next sprint replan\n"
            "  - extend flow_control.max_retries_per_pr and re-run "
            "`minions cron pr-owner-sweep`"
        ),
        related_decision_id=None,
        related_pr_url=record.pr_url,
        status=QuestionStatus.ESCALATED,
        escalated_at=datetime.now(tz=UTC),
        escalation_reason=f"reached max_retries_per_pr after {failure}",
    )
    if dry_run:
        # Don't write or notify; report what we WOULD have done.
        return str(question.id)

    with suppress(Exception):
        questions_store.save(question)
    with suppress(Exception):
        github.comment_on_pull_request(
            number=record.pr_number or 0,
            body=_handoff_comment(record, failure, owner, str(question.id)),
        )
    with suppress(Exception):
        notifier.notify_text(
            subject=f"[minions] PR escalation: {record.project} — {failure}",
            body=question.question + "\n\n" + (question.context or ""),
        )
    return str(question.id)


def _retry_comment(
    record: EngineerRunRecord,
    failure: FailureKind,
    result: EngineerResult,
    cap: int,
) -> str:
    n = record.followup_attempts
    head = (
        f"🤖 **PR owner sweep — retry #{n}/{cap}**\n\n"
        f"Owner: `{record.owner_agent_id or 'engineer'}`\n"
        f"Failure: `{failure}`\n"
    )
    if result.skipped:
        return head + (
            f"\nThe engineer crew skipped this retry: {result.skip_reason}. "
            "Counter not incremented; the next sweep will try again."
        )
    return head + (
        "\nA fix commit has been pushed to this branch; CI will re-run "
        "on the new commit. No new Decision Record is filed — the same "
        "engineer owns this PR until it merges or escalates."
    )


def _handoff_comment(
    record: EngineerRunRecord,
    failure: FailureKind,
    owner: str,
    question_id: str,
) -> str:
    return (
        f"🚨 **Operator handoff required**\n\n"
        f"Owner `{owner}` has reached `max_retries_per_pr` "
        f"(last failure: `{failure}`). The owner sweep has stopped "
        "dispatching this PR until you answer the linked Question Record.\n\n"
        f"Question id: `{question_id}`\n\n"
        f"Inspect with `minions questions show {question_id[:8]}` and "
        "answer / extend / close as appropriate."
    )


__all__ = [
    "OwnerSweepOutcome",
    "OwnerSweepReport",
    "run_pr_owner_sweep",
]
