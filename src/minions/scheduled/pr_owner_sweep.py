"""PR owner sweep — sticky-owner cron that replaces the fix-Decision loop.

Walks every open minions-authored ``EngineerRunRecord`` and, when the PR
needs attention (CI failure or merge conflict), re-dispatches the
**original owner agent** in-place on the existing branch. No new Decision
Record is filed; ``record.iteration_count`` is sticky to the PR. After
``manifest.flow_control.max_iterations_per_pr`` iterations the sweep
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

import logging
import re
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

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from minions.github.client import GitHubClient
    from minions.github.models import FailingCheckLog
    from minions.questions.store_factory import QuestionStoreLike


FailureKind = Literal[
    "ci_failure",
    "security_failure",
    "merge_conflict",
    "review_changes_requested",
    "operator_takeover",
]


# Substrings that mark a failing check as a security-class workflow. When any
# failing check matches, ``ci_failure`` is promoted to ``security_failure`` so
# the engineer receives a security-aware brief instead of the generic CI-fix
# retry prompt. Trivial to extend per-project for custom check names.
_SECURITY_CHECK_NAME_RE = re.compile(r"security|codeql|trivy|semgrep|snyk|sast|dast", re.IGNORECASE)


# Substrings inside engineer-crew skip_reason that mark the skip as
# *terminal* — nothing the bot can do on the next tick will change the
# outcome until the operator does something. When matched, the sweep
# escalates ONCE via a Question Record + withdrawal comment and then
# stops re-dispatching the PR (gated by record.escalated_question_id).
# Without this list, transient + terminal skips look identical and the
# sweep loops the same comment every 2h.
TERMINAL_SKIP_PATTERNS: tuple[str, ...] = (
    "has operator-authored commits",  # operator pushed on the branch
    "no longer exists",  # branch deleted out from under us
    "already exists; resolve manually",  # fresh-PR branch collision
)


def _is_terminal_skip(skip_reason: str | None) -> bool:
    if not skip_reason:
        return False
    return any(pattern in skip_reason for pattern in TERMINAL_SKIP_PATTERNS)


# Sprint board PR 2: when crew reviewers request changes, the owner sweep
# now re-dispatches the original engineer on the SAME branch instead of
# the review loop filing a sibling "fix" Decision. This is the cap on
# how many in-place review-response rounds the owner runs before the
# review loop escalates to the operator via a Question Record.
MAX_REVIEW_ROUNDS_PER_PR = 2
OutcomeStatus = Literal["retried", "healthy", "escalated", "skipped", "throttled", "error"]
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
    to push onto). Already-escalated records still pass this gate — the
    main loop handles them with a "skipped/awaiting operator" outcome so
    the run report still shows them.
    """
    if record.pr_number is None or record.branch_name is None:
        return False
    if record.dry_run or record.skipped:
        return False
    return record.pr_state not in ("merged", "closed")


def _classify_failure(
    ci_conclusion: str | None,
    merge_state: str | None,
    review_status: str | None = None,
) -> FailureKind | None:
    """Decide whether the PR needs a retry, and why.

    Returns ``None`` when the PR is healthy enough to leave to the standard
    review loop. Priority: merge_conflict > ci_failure > review_changes_requested.
    Reviewer feedback loses to harder failures because the engineer can't
    address feedback meaningfully on a branch that won't even merge or build.

    Note: ``ci_failure`` is promoted to ``security_failure`` by the dispatch
    path (not here) via :func:`_promote_to_security_failure` because the
    promotion needs the per-check details from GitHub, which this pure
    classifier doesn't see.
    """
    if merge_state == "dirty":
        return "merge_conflict"
    if ci_conclusion == "failure":
        return "ci_failure"
    if review_status == "changes_requested":
        return "review_changes_requested"
    return None


def _promote_to_security_failure(
    failure: FailureKind | None,
    github: GitHubClient,
    pr_number: int | None,
) -> FailureKind | None:
    """Promote ``ci_failure`` to ``security_failure`` when any failing
    check's name matches ``_SECURITY_CHECK_NAME_RE``.

    Best-effort: if the GitHub check-runs endpoint errors out (rate limit,
    stale head SHA), returns the original failure unchanged so the engineer
    still retries with the generic CI-fix brief. The security path is a
    refinement on top of CI fix, never a replacement for it.
    """
    if failure != "ci_failure" or pr_number is None:
        return failure
    try:
        failing = github.get_pr_failing_check_logs(pr_number)
    except Exception:  # noqa: BLE001 — never crash the sweep on a probe error
        return failure
    for log in failing:
        if _SECURITY_CHECK_NAME_RE.search(log.check_name or ""):
            return "security_failure"
    return failure


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
            outcomes.append(
                OwnerSweepOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    status="skipped",
                    reason=f"sweep cap reached ({max_dispatches_per_sweep})",
                )
            )
            continue

        manifest = manifests.get(record.project)
        if manifest is None or manifest.source.kind != "github":
            outcomes.append(
                OwnerSweepOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    status="skipped",
                    reason="project not active or not github-hosted",
                )
            )
            continue

        # Already escalated to the operator — stay quiet until they answer.
        if record.escalated_question_id is not None:
            outcomes.append(
                OwnerSweepOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    status="skipped",
                    question_id=record.escalated_question_id,
                    reason="awaiting operator answer on escalation",
                )
            )
            continue

        outcome = _process_one(
            record=record,
            manifest=manifest,
            store=store,
            engineer_runs_store=engineer_runs_store,
            questions_store=questions_store,
            open_github_client=open_github_client,
            notifier=notifier,
            api_key=api_key,
            dry_run=dry_run,
            cost_log_path=cost_log_path,
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
            decision_id=record.decision_id,
            project=record.project,
            pr_url=record.pr_url,
            status="error",
            reason="failed to open GitHub client",
        )

    try:
        with github:
            ci_conclusion, _ = github.get_pr_check_status(record.pr_number or 0)
            merge_state = github.get_pr_merge_state(record.pr_number or 0)
            failure = _classify_failure(ci_conclusion, merge_state, record.review_status)
            # Per-check refinement: a generic ``ci_failure`` becomes
            # ``security_failure`` when any failing check is from a security
            # workflow (CodeQL, Trivy, Semgrep, …). The engineer then gets a
            # security-aware brief instead of the plain CI-fix prompt — the
            # PR owner keeps responsibility, with better context.
            failure = _promote_to_security_failure(failure, github, record.pr_number)
            if failure == "security_failure":
                record.last_security_triage_at = datetime.now(tz=UTC)

            # Always persist the fresh CI snapshot so the dashboard sees it.
            record.ci_conclusion = ci_conclusion
            record.ci_last_checked_at = datetime.now(tz=UTC)

            if failure is None:
                with suppress(Exception):
                    engineer_runs_store.update(record)
                return OwnerSweepOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    owner_agent_id=_resolve_owner(record, manifest),
                    status="healthy",
                    reason=f"ci={ci_conclusion}, merge_state={merge_state}",
                )

            record.last_failure_kind = failure

            # Hit the cap → escalate to the operator and stop dispatching.
            cap = manifest.flow_control.max_iterations_per_pr
            if record.iteration_count >= cap:
                question_id = _escalate(
                    record=record,
                    manifest=manifest,
                    failure=failure,
                    questions_store=questions_store,
                    notifier=notifier,
                    dry_run=dry_run,
                    github=github,
                )
                if not dry_run:
                    record.escalated_question_id = question_id
                    with suppress(Exception):
                        engineer_runs_store.update(record)
                return OwnerSweepOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    owner_agent_id=_resolve_owner(record, manifest),
                    status="escalated",
                    attempt=record.iteration_count,
                    failure_kind=failure,
                    question_id=question_id,
                    reason=f"reached max_iterations_per_pr={cap}",
                )

            decision = store.get(record.decision_id)
            if decision is None:
                return OwnerSweepOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    status="error",
                    reason="original Decision not found in store",
                )

            if dry_run:
                return OwnerSweepOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    owner_agent_id=_resolve_owner(record, manifest),
                    status="retried",
                    attempt=record.iteration_count + 1,
                    failure_kind=failure,
                    reason="dry-run — would re-dispatch owner",
                )

            try:
                result = runner(
                    decision,
                    manifest,
                    github=github,
                    dry_run=False,
                    api_key=api_key,
                    cost_log_path=cost_log_path,
                    target_branch=record.branch_name,
                    existing_pr_number=record.pr_number,
                    retry_attempt=record.iteration_count + 1,
                    is_conflict_resolution=(failure == "merge_conflict"),
                    is_review_response=(failure == "review_changes_requested"),
                    is_security_failure=(failure == "security_failure"),
                )
            except BudgetBreachError as e:
                return OwnerSweepOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    status="throttled",
                    reason=str(e),
                )

            # Terminal-skip handling — when the engineer crew declines for a
            # reason that won't change on the next tick (operator pushed to
            # the branch, branch deleted, etc.), escalate ONCE and stop
            # dispatching. Without this branch the sweep would re-post the
            # same "retry skipped" comment every 2h forever (see the
            # 2026-05-27 PR #74 incident).
            if result.skipped and _is_terminal_skip(result.skip_reason):
                # When the original failure was security_failure, the engineer
                # was blocked by operator commits, not by a true takeover —
                # so leave a read-only triage comment + escalate as
                # security_failure (operator-blocked) instead of the generic
                # operator_takeover. Closes the user's #1 ask: PR owner still
                # tells you what it found about the security failure.
                escalation_failure: FailureKind = (
                    "security_failure" if failure == "security_failure" else "operator_takeover"
                )
                if escalation_failure == "security_failure":
                    _post_security_triage_comment(
                        record=record,
                        github=github,
                        dry_run=False,
                        now=datetime.now(tz=UTC),
                    )
                question_id = _escalate(
                    record=record,
                    manifest=manifest,
                    failure=escalation_failure,
                    questions_store=questions_store,
                    notifier=notifier,
                    dry_run=False,
                    github=github,
                    was_operator_takeover_blocked=(escalation_failure == "security_failure"),
                )
                record.escalated_question_id = question_id
                record.last_failure_kind = escalation_failure
                with suppress(Exception):
                    engineer_runs_store.update(record)
                return OwnerSweepOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    owner_agent_id=_resolve_owner(record, manifest),
                    status="escalated",
                    attempt=record.iteration_count,
                    failure_kind=escalation_failure,
                    question_id=question_id,
                    reason=result.skip_reason,
                )

            # Only count REAL attempts. Skipped/errored runner results
            # don't burn the per-PR budget — they'll get another shot next
            # tick (which is the existing engineer-crew contract).
            if not result.skipped:
                record.iteration_count += 1
                record.last_followup_at = datetime.now(tz=UTC)

            with suppress(Exception):
                engineer_runs_store.update(record)

            with suppress(Exception):
                github.comment_on_pull_request(
                    number=record.pr_number or 0,
                    body=_retry_comment(record, failure, result, cap),
                )

            return OwnerSweepOutcome(
                decision_id=record.decision_id,
                project=record.project,
                pr_url=record.pr_url,
                owner_agent_id=_resolve_owner(record, manifest),
                # Distinguish "did real work" from "engineer crew skipped" so
                # operator sees the truth in the report.
                status="skipped" if result.skipped else "retried",
                attempt=record.iteration_count,
                failure_kind=failure,
                reason=result.skip_reason if result.skipped else None,
            )
    except Exception as e:  # noqa: BLE001 — per-record isolation
        return OwnerSweepOutcome(
            decision_id=record.decision_id,
            project=record.project,
            pr_url=record.pr_url,
            status="error",
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
    was_operator_takeover_blocked: bool = False,
) -> str:
    """File ONE Question Record + post the handoff comment on the PR.

    Three escalation flavors share this path:
      * ``failure="operator_takeover"`` — bot withdraws because the operator
        pushed to the branch (or the branch is unrecoverable). No iteration
        cap involved; this is a "stepping back" hand-off, not a "tried hard
        and failed" one.
      * ``failure="security_failure"`` with ``was_operator_takeover_blocked=True``
        — bot analyzed the security finding and posted a read-only triage
        comment, but the branch has operator-authored commits so it
        cannot push the fix. Operator decides next.
      * any other ``failure`` (or ``security_failure`` at the iteration cap)
        — bot tried ``max_iterations_per_pr`` times and gave up.
    """
    owner = _resolve_owner(record, manifest)
    is_takeover = failure == "operator_takeover"
    is_security_takeover = failure == "security_failure" and was_operator_takeover_blocked

    if is_takeover:
        question_text = (
            f"PR {record.pr_url} — operator-authored commits detected on "
            f"branch {record.branch_name}. Bot ({owner}) has withdrawn; "
            "you own this PR now."
        )
        context_text = (
            f"Branch: {record.branch_name}\n"
            f"Owner agent: {owner}\n"
            f"Last CI conclusion: {record.ci_conclusion}\n\n"
            "The engineer crew refuses to overwrite operator commits "
            "(safety rule). The owner sweep would otherwise re-dispatch "
            "every 2h forever — this Question marks the PR as 'operator "
            "has taken over' so the loop stops.\n\n"
            "Suggested actions:\n"
            "  - finish + merge the PR yourself\n"
            "  - close the PR if the work is no longer needed\n"
            "  - to re-engage the bot: revert your commits OR push a fresh "
            "branch and re-file the original Decision"
        )
        escalation_reason = "operator-authored commits on bot branch"
    elif is_security_takeover:
        question_text = (
            f"PR {record.pr_url} — security CI workflow failed. Bot "
            f"({owner}) has analyzed the finding and posted a triage "
            "comment with the details and recommendation on the PR. The "
            "branch has operator-authored commits, so the bot cannot push "
            "the fix automatically — you decide."
        )
        context_text = (
            f"Branch: {record.branch_name}\n"
            f"Owner agent: {owner}\n"
            f"Failure kind: security_failure (operator-commits blocked)\n"
            f"Last CI conclusion: {record.ci_conclusion}\n\n"
            "The bot ran its security-aware analysis and produced the "
            "finding + recommended fix as a PR comment — read that first. "
            "It refused to push code because the branch has operator-"
            "authored commits (safety rule).\n\n"
            "Suggested actions:\n"
            "  - apply the bot's suggested fix yourself (see the PR comment)\n"
            "  - if it's a true false positive: suppress narrowly + explain\n"
            "  - to let the bot push the fix: revert your commits OR open "
            "a fresh Decision on a new branch"
        )
        escalation_reason = "security CI failed; operator commits prevent bot fix"
    else:
        question_text = (
            f"PR {record.pr_url} ({failure}) — owner {owner} hit "
            f"max_iterations_per_pr={manifest.flow_control.max_iterations_per_pr}. "
            "Operator action required."
        )
        context_text = (
            f"Branch: {record.branch_name}\n"
            f"Owner: {owner}\n"
            f"Failure kind: {failure}\n"
            f"Last CI conclusion: {record.ci_conclusion}\n"
            f"Iterations: {record.iteration_count}\n"
            f"Last iteration at: {record.last_followup_at}\n\n"
            "Suggested actions:\n"
            "  - merge after manual fix\n"
            "  - close the PR and let the next sprint replan\n"
            "  - extend flow_control.max_iterations_per_pr and re-run "
            "`minions cron pr-owner-sweep`"
        )
        escalation_reason = f"reached max_iterations_per_pr after {failure}"

    question = QuestionRecord(
        project=record.project,
        asker_role="pr_owner_sweep",
        asker_agent_id=f"pr_owner_sweep@{record.project}",
        target_role="operator",
        question=question_text,
        context=context_text,
        related_decision_id=None,
        related_pr_url=record.pr_url,
        status=QuestionStatus.ESCALATED,
        escalated_at=datetime.now(tz=UTC),
        escalation_reason=escalation_reason,
    )
    if dry_run:
        # Don't write or notify; report what we WOULD have done.
        return str(question.id)

    with suppress(Exception):
        questions_store.save(question)
    with suppress(Exception):
        github.comment_on_pull_request(
            number=record.pr_number or 0,
            body=_handoff_comment(
                record,
                failure,
                owner,
                str(question.id),
                was_operator_takeover_blocked=was_operator_takeover_blocked,
            ),
        )
    with suppress(Exception):
        notifier.notify_text(
            subject=f"[minions] PR escalation: {record.project} — {failure}",
            body=question.question + "\n\n" + (question.context or ""),
        )
    return str(question.id)


def _build_security_triage_comment(
    failing_logs: list[FailingCheckLog],
    owner: str,
) -> str:
    """Render a read-only triage comment for an operator-takeover-blocked
    security failure. Surfaces the failing security check(s), the tail of
    each log, and a "what to do next" recommendation — without pushing
    any code. Consumer: ``_post_security_triage_comment``."""
    security_logs = [
        log for log in failing_logs if _SECURITY_CHECK_NAME_RE.search(log.check_name or "")
    ]
    if not security_logs:
        # Defensive — we only post when the dispatch already classified
        # security_failure, but the failing-check set may have shifted by
        # the time we re-fetch. Render a minimal-but-honest message.
        return (
            f"🛡️ **Security triage (read-only)** — bot (`{owner}`) "
            "intended to post details of the failing security check on "
            "this PR, but by the time it re-fetched the check runs no "
            "security check was failing. Re-run the workflow and the bot "
            "will re-triage on the next sweep tick."
        )

    blocks: list[str] = []
    for log in security_logs:
        header_bits = [f"### `{log.check_name}`"]
        if log.app_slug:
            header_bits.append(f"app=`{log.app_slug}`")
        header_bits.append(f"conclusion=`{log.conclusion}`")
        if log.html_url:
            header_bits.append(f"[details]({log.html_url})")
        excerpt = (log.log_excerpt or "(no log content available)").rstrip()
        if log.was_truncated:
            excerpt += f"\n\n_… truncated (original was {log.original_bytes} bytes)_"
        blocks.append(f"{' · '.join(header_bits)}\n\n```\n{excerpt}\n```")

    body = "\n\n".join(blocks)
    return (
        f"🛡️ **Security triage (read-only)** — bot (`{owner}`) is **not** "
        "pushing code to this PR because the branch already has "
        "operator-authored commits (safety rule). Here is what the bot "
        "found and what it would do.\n\n"
        f"{body}\n\n"
        "---\n\n"
        "**Recommended next steps:**\n\n"
        "1. Read each failing security check above; identify the root "
        "cause from the tool's output (CWE / rule id / file / line).\n"
        "2. Fix the root cause directly — do not silence the warning, "
        "comment it out, or add to an allow-list.\n"
        "3. If it is genuinely a known false positive on one line, "
        "suppress with the tool's narrowest mechanism (e.g. CodeQL "
        "`// lgtm[…]` on that line) **and** explain why in a code "
        "comment **and** in the PR body.\n"
        "4. If a dependency advisory triggered it, bump the dependency "
        "in `package.json` / `pyproject.toml` and update the lockfile "
        "in the same patch.\n\n"
        "_To let the bot push the fix instead: revert your commits on "
        "this branch, or open a fresh Decision on a new branch._"
    )


def _post_security_triage_comment(
    *,
    record: EngineerRunRecord,
    github: GitHubClient,
    dry_run: bool,
    now: datetime,
) -> bool:
    """Idempotently post the read-only security triage comment.

    Returns True if a comment was posted this call, False otherwise
    (already posted; or dry_run; or PR number missing). Sets
    ``record.security_triage_comment_posted_at`` on first successful post
    so subsequent sweep ticks no-op — the dedup gate that stops the
    email storm.
    """
    if record.security_triage_comment_posted_at is not None:
        return False
    if record.pr_number is None:
        return False
    if dry_run:
        return False

    try:
        failing_logs = github.get_pr_failing_check_logs(record.pr_number)
    except Exception:  # noqa: BLE001 — never crash the sweep on a probe error
        failing_logs = []

    owner = record.owner_agent_id or "engineer"
    body = _build_security_triage_comment(failing_logs, owner)
    # Only mark the comment posted (and burn the dedup gate) when GitHub
    # actually accepted the comment. Setting the timestamp on a swallowed
    # failure would leave the operator permanently silent on a real
    # security finding after a single transient API hiccup.
    try:
        github.comment_on_pull_request(number=record.pr_number, body=body)
    except Exception as e:  # noqa: BLE001 — never crash the sweep
        logger.warning(
            "site_sentry: security triage comment post failed for %s #%s: %s",
            record.project,
            record.pr_number,
            e,
        )
        return False
    record.security_triage_comment_posted_at = now
    return True


def _retry_comment(
    record: EngineerRunRecord,
    failure: FailureKind,
    result: EngineerResult,
    cap: int,
) -> str:
    n = record.iteration_count
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
    *,
    was_operator_takeover_blocked: bool = False,
) -> str:
    if failure == "operator_takeover":
        return (
            "🤖 **Creator is taking care of this with awesomeness** — "
            f"bot (`{owner}`) is stepping back.\n\n"
            "I noticed operator commits on this branch and the safety rule "
            "is to never overwrite human work. The owner sweep has stopped "
            "re-dispatching this PR; no more retry comments will land here.\n\n"
            f"Question id: `{question_id}`\n\n"
            "To re-engage the bot: revert your commits OR open a fresh "
            "Decision Record on a new branch."
        )
    if failure == "security_failure" and was_operator_takeover_blocked:
        return (
            f"🛡️ **Security triage posted** — bot (`{owner}`) analyzed the "
            "failing security workflow and left the finding + recommended "
            "fix as a separate comment on this PR. It is **not** pushing "
            "code because the branch already has operator-authored "
            "commits.\n\n"
            f"Question id: `{question_id}`\n\n"
            "Your call: apply the suggested fix yourself, suppress the "
            "finding narrowly (with an explanation), or revert your "
            "commits to let the bot push the fix."
        )
    return (
        f"🚨 **Operator handoff required**\n\n"
        f"Owner `{owner}` has reached `max_iterations_per_pr` "
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
