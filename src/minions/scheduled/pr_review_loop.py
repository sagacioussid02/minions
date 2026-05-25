"""PR review-loop sweep.

This is the explicit peer-review layer for minions-authored PRs. It assigns a
small internal reviewer set, posts structured reviewer comments, and persists
the resulting crew verdict on the ``EngineerRunRecord`` so the Sprint Board can
show real team motion instead of inferring it from CI alone.

When reviewers request changes, the creator agent gets one response/fix
iteration via a linked auto-approved fix Decision. When reviewers approve and
CI is green, the sweep asks GitHub to merge. Branch protection remains the
final gate; if GitHub blocks the merge, the sweep posts an operator handoff
comment.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from minions.approval.store import DecisionStore
from minions.crews.engineer_runs_store import (
    EngineerRunRecord,
    EngineerRunStore,
    PRReviewerAssignment,
    ReviewerVerdict,
)
from minions.github.client import GitHubError
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.manifest import Manifest, load_active_manifests

if TYPE_CHECKING:
    from minions.github.client import GitHubClient


OutcomeStatus = Literal[
    "assigned",
    "reviewed",
    "creator_responded",
    "conflict_queued",
    "superseded",
    "merged",
    "handoff",
    "skipped",
    "error",
]


class PRReviewLoopOutcome(BaseModel):
    decision_id: str
    project: str
    pr_url: str | None = None
    status: OutcomeStatus
    reason: str | None = None
    assigned_reviewers: list[str] = Field(default_factory=list)
    comments_posted: int = 0
    review_status: str | None = None
    fix_decision_id: str | None = None


class PRReviewLoopReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[PRReviewLoopOutcome] = Field(default_factory=list)

    @property
    def assigned(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "assigned")

    @property
    def reviewed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "reviewed")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")

    @property
    def creator_responded(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "creator_responded")

    @property
    def merged(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "merged")

    @property
    def handoff(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "handoff")

    @property
    def conflict_queued(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "conflict_queued")

    @property
    def superseded(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "superseded")


class StructuredReview(BaseModel):
    role: str
    verdict: ReviewerVerdict
    summary: str
    body: str


ReviewBuilder = Callable[
    [Decision, EngineerRunRecord, PRReviewerAssignment, str | None, str | None],
    StructuredReview,
]


def _make_llm_review_builder(api_key: str | None) -> ReviewBuilder:
    """Wrap ``crews.pr_reviewer.run_pr_review`` into the ReviewBuilder shape.

    Falls back to the legacy stub when ``api_key`` is None — keeps tests +
    dry-run sweeps deterministic and free.

    The wrapper closes over ``api_key`` only; ``pr_files`` + ``prior_comments``
    are fetched inside the loop body and looked up via per-PR module-level
    caches keyed on ``record.decision_id``. This is uglier than a deeper
    signature change, but preserves the existing ``ReviewBuilder`` Protocol
    so test injection sites keep working unchanged.
    """
    if api_key is None:
        return _default_review_builder

    def _builder(
        decision: Decision,
        record: EngineerRunRecord,
        reviewer: PRReviewerAssignment,
        ci_conclusion: str | None,
        details_url: str | None,
    ) -> StructuredReview:
        from minions.crews.pr_reviewer import run_pr_review

        pr_files = _LLM_CONTEXT.get((record.decision_id, "files"), [])
        prior_comments = _LLM_CONTEXT.get((record.decision_id, "comments"), [])
        return run_pr_review(
            role=reviewer.role,
            decision=decision,
            record=record,
            reviewer=reviewer,
            pr_files=pr_files,
            prior_comments=prior_comments,
            ci_conclusion=ci_conclusion,
            ci_details_url=details_url,
            api_key=api_key,
        )

    return _builder


# Per-sweep cache populated inside ``run_pr_review_loop`` before each
# reviewer dispatch — see the ``_LLM_CONTEXT`` writes in that function.
_LLM_CONTEXT: dict[tuple[str, str], list[Any]] = {}


def _is_open(record: EngineerRunRecord) -> bool:
    return record.pr_state in (None, "open") and record.pr_number is not None


def _default_reviewers(decision: Decision) -> list[PRReviewerAssignment]:
    reviewers = [
        PRReviewerAssignment(
            role="ttl",
            agent_id=f"ttl@{decision.project}",
            display_name="Tech Team Lead",
        ),
        PRReviewerAssignment(
            role="qa_engineer",
            agent_id=f"qa_engineer@{decision.project}",
            display_name="QA Engineer",
        ),
    ]
    if decision.risk in {"medium", "high"} or decision.security_review is not None:
        reviewers.append(
            PRReviewerAssignment(
                role="security_champion",
                agent_id=f"security_champion@{decision.project}",
                display_name="Security Champion",
            )
        )
    return reviewers


def _assignment_comment(record: EngineerRunRecord) -> str:
    reviewers = ", ".join(r.display_name for r in record.reviewers)
    return (
        "🤖 **Crew review started**\n\n"
        f"Assigned reviewers: {reviewers}.\n\n"
        "Each reviewer will leave one structured review comment. If anything "
        "blocks merge, the creator agent gets one response/fix iteration."
    )


def _creator_response_comment(
    record: EngineerRunRecord,
    *,
    fix_id: str | None,
) -> str:
    blockers = [r for r in record.reviewers if r.status == "changes_requested"]
    lines = [
        "🤖 **Creator response**",
        "",
        "I read the crew review feedback and found blocking items to address.",
    ]
    if blockers:
        lines += ["", "**Blocking reviewer feedback:**"]
        lines += [f"- {r.display_name}: {r.summary or 'requested changes'}" for r in blockers]
    if fix_id:
        lines += [
            "",
            f"I queued one linked fix attempt as Decision `{fix_id[:8]}`. "
            "The engineer crew will open a follow-up PR and reference this PR.",
        ]
    else:
        lines += [
            "",
            "The one allowed creator-response iteration is already used; this PR "
            "needs operator attention or a manual follow-up.",
        ]
    return "\n".join(lines)


def _human_handoff_comment(record: EngineerRunRecord, reason: str) -> str:
    return (
        "🤖 **Ready for operator merge**\n\n"
        "This PR is reviewed by us and looks good to merge. GitHub branch "
        "protection or repository rules blocked the automated merge attempt, "
        "so it is ready for your review and merge.\n\n"
        f"**GitHub response:** {reason[:500]}"
    )


def _superseded_comment(record: EngineerRunRecord, followup: EngineerRunRecord) -> str:
    return (
        "🤖 **Original PR superseded**\n\n"
        "A linked follow-up PR from the engineer crew has merged, so this older "
        "PR is no longer the active path to ship this change. I am closing it "
        "to keep the sprint board clean and the audit trail explicit.\n\n"
        f"**Superseding PR:** {followup.pr_url or 'linked follow-up PR'}\n"
        f"**Original PR:** {record.pr_url or f'PR #{record.pr_number}'}"
    )


def _conflict_comment(record: EngineerRunRecord, fix_id: str) -> str:
    return (
        "🤖 **Conflict resolution queued**\n\n"
        "GitHub reports this PR is dirty/conflicting with the base branch. The "
        "creator agent should not leave this for the operator: I queued a "
        f"conflict-resolution Decision `{fix_id[:8]}` for the engineer crew.\n\n"
        "That follow-up will re-evaluate whether the original change is still "
        "needed on current `main`. If it is needed, the crew should open a clean "
        "replacement PR; if it is not needed, the original PR will be closed as "
        "superseded."
    )


def _render_structured_review(
    *,
    reviewer: PRReviewerAssignment,
    verdict: ReviewerVerdict,
    summary: str,
    ci_conclusion: str | None,
    details_url: str | None,
) -> str:
    verdict_label = {
        "approve": "APPROVE",
        "request_changes": "REQUEST_CHANGES",
        "comment": "COMMENT",
    }[verdict]
    lines = [
        f"### {reviewer.display_name} review",
        "",
        f"**Verdict:** {verdict_label}",
        f"**Reviewer:** `{reviewer.agent_id}`",
        f"**CI:** {ci_conclusion or 'unknown'}",
        "",
        "**Summary:**",
        summary,
    ]
    if details_url:
        lines += ["", f"**CI details:** {details_url}"]
    lines += ["", f"— {reviewer.display_name}"]
    return "\n".join(lines)


def _default_review_builder(
    decision: Decision,
    record: EngineerRunRecord,
    reviewer: PRReviewerAssignment,
    ci_conclusion: str | None,
    details_url: str | None,
) -> StructuredReview:
    if ci_conclusion == "failure":
        verdict: ReviewerVerdict = "request_changes"
        summary = (
            "CI is failing, so this PR is not ready to merge. The creator agent "
            "should inspect the failed checks and post a correction before the "
            "crew approves it."
        )
    elif ci_conclusion == "pending":
        verdict = "comment"
        summary = (
            "Checks are still running. I am recording the review pass, but final "
            "approval waits for CI to settle."
        )
    elif reviewer.role == "qa_engineer" and not any(
        "test" in path.lower() for path in record.files_changed
    ):
        verdict = "comment"
        summary = (
            "No changed test files were recorded. This may be acceptable for docs "
            "or configuration work, but the creator should confirm coverage for "
            "behavioral changes."
        )
    else:
        verdict = "approve"
        summary = (
            "The PR is in acceptable shape for this review pass. No blocking "
            "concerns from this reviewer."
        )

    if reviewer.role == "security_champion" and decision.risk == "high":
        verdict = "comment" if verdict == "approve" else verdict
        summary = (
            "High-risk work needs careful operator attention even when no concrete "
            "security blocker is visible in this lightweight pass."
        )

    return StructuredReview(
        role=reviewer.role,
        verdict=verdict,
        summary=summary,
        body=_render_structured_review(
            reviewer=reviewer,
            verdict=verdict,
            summary=summary,
            ci_conclusion=ci_conclusion,
            details_url=details_url,
        ),
    )


def _advance_review_status(record: EngineerRunRecord) -> None:
    if not record.reviewers:
        record.review_status = "not_started"
        return
    if any(r.status == "changes_requested" for r in record.reviewers):
        record.review_status = "changes_requested"
        return
    if all(r.status == "approved" for r in record.reviewers):
        record.review_status = "crew_approved"
        if record.crew_approved_at is None:
            record.crew_approved_at = datetime.now(tz=UTC)
        return
    record.review_status = "reviewing"


def _fix_decision_summary(record: EngineerRunRecord) -> str:
    pr_ref = f"PR #{record.pr_number}" if record.pr_number else "the prior PR"
    return f"Address crew review feedback on {pr_ref} ({record.project})"


def _fix_decision_plan(record: EngineerRunRecord) -> str:
    feedback = [
        f"- {r.display_name}: {r.summary or 'requested changes'}"
        for r in record.reviewers
        if r.status == "changes_requested"
    ]
    if not feedback:
        feedback = ["- CI or crew review requested changes."]
    files = [f"- `{f}`" for f in record.files_changed] or ["- (none recorded)"]
    return "\n".join(
        [
            f"## Crew review requested changes on {record.pr_url or 'prior PR'}",
            "",
            "**Blocking feedback:**",
            *feedback,
            "",
            "**Files in the reviewed PR:**",
            *files,
            "",
            "## Requested change",
            "",
            "Run the engineer crew once more for this project. Address the reviewer "
            "feedback and CI failures, then open a linked follow-up PR that references "
            f"the original PR ({record.pr_url}).",
        ]
    )


def _conflict_decision_summary(record: EngineerRunRecord) -> str:
    pr_ref = f"PR #{record.pr_number}" if record.pr_number else "the prior PR"
    return f"Resolve merge conflict on {pr_ref} ({record.project})"


def _conflict_decision_plan(record: EngineerRunRecord) -> str:
    files = [f"- `{f}`" for f in record.files_changed] or ["- (none recorded)"]
    return "\n".join(
        [
            f"## Merge conflict on {record.pr_url or 'prior PR'}",
            "",
            "GitHub reports the original PR is dirty/conflicting with the base branch.",
            "This is normal engineering work: do not leave the PR stalled.",
            "",
            "**Files in the original PR:**",
            *files,
            "",
            "## Requested change",
            "",
            "Inspect the current `main` branch and the original PR intent. Decide "
            "whether the change is still needed.",
            "",
            "- If the change is still needed, produce a clean corrected change on a "
            "fresh minions branch and open a linked follow-up PR that references the "
            f"original PR ({record.pr_url}).",
            "- If the change is already covered by current `main`, leave a clear "
            "comment explaining that the original PR is superseded so the review "
            "loop can close it.",
        ]
    )


def _decision_mentions_record(decision: Decision, record: EngineerRunRecord) -> bool:
    haystack = "\n".join(
        [
            decision.summary or "",
            decision.rationale or "",
            decision.diff_or_plan or "",
        ]
    )
    return bool(
        (record.pr_url and record.pr_url in haystack)
        or (record.pr_number and f"PR #{record.pr_number}" in haystack)
    )


def _queue_creator_fix_decision(
    *,
    store: DecisionStore,
    record: EngineerRunRecord,
) -> Decision:
    existing = _find_existing_fix_decision(store=store, record=record)
    if existing is not None:
        return existing

    # In-place fields: the creator-response fix commits ONTO the original
    # PR's branch (no new PR opened). See openspec/in-place-pr-fix.
    in_place_extras: dict[str, object] = {}
    if record.pr_number is not None and record.branch_name:
        in_place_extras["existing_pr_number"] = record.pr_number
        in_place_extras["existing_pr_branch"] = record.branch_name
        in_place_extras["retry_attempt"] = record.review_round + 1
    fix = Decision(
        project=record.project,
        type=DecisionType.BUG,
        summary=_fix_decision_summary(record),
        rationale=(
            f"Creator agent is responding to crew review feedback on {record.pr_url}. "
            "Pushing the single allowed linked fix iteration as a commit on the same branch."
        ),
        diff_or_plan=_fix_decision_plan(record),
        risk="low",
        proposer_role="creator_response",
        proposer_agent_id=f"creator_response@{record.project}",
        proposer_display_name="Creator agent",
        status=DecisionStatus.APPROVED,
        priority="p2",
        expedited=True,
        requested_by_role="pr_review_loop",
        resolved_reason="auto-approved by PR review loop",
        resolved_at=datetime.now(tz=UTC),
        **in_place_extras,  # type: ignore[arg-type]
    )
    store.save(fix)
    return fix


def _find_existing_fix_decision(
    *,
    store: DecisionStore,
    record: EngineerRunRecord,
) -> Decision | None:
    pr_url = record.pr_url or ""
    if not pr_url:
        return None
    for decision in store.list_by_status(DecisionStatus.APPROVED):
        if decision.project != record.project:
            continue
        if decision.proposer_role not in {"pr_followup", "creator_response"}:
            continue
        haystack = "\n".join(
            [
                decision.summary or "",
                decision.rationale or "",
                decision.diff_or_plan or "",
            ]
        )
        if pr_url in haystack or f"PR #{record.pr_number}" in haystack:
            return decision
    return None


def _find_existing_conflict_decision(
    *,
    store: DecisionStore,
    record: EngineerRunRecord,
) -> Decision | None:
    for decision in store.list_by_status(DecisionStatus.APPROVED):
        if decision.project != record.project:
            continue
        if decision.proposer_role != "conflict_resolution":
            continue
        if _decision_mentions_record(decision, record):
            return decision
    return None


def _queue_conflict_decision(
    *,
    store: DecisionStore,
    record: EngineerRunRecord,
) -> Decision:
    existing = _find_existing_conflict_decision(store=store, record=record)
    if existing is not None:
        return existing

    # Conflict resolution also commits in-place on the original branch.
    # Engineer crew will fetch + resolve conflict markers against main HEAD.
    in_place_extras: dict[str, object] = {}
    if record.pr_number is not None and record.branch_name:
        in_place_extras["existing_pr_number"] = record.pr_number
        in_place_extras["existing_pr_branch"] = record.branch_name
        in_place_extras["conflict_resolution"] = True
    fix = Decision(
        project=record.project,
        type=DecisionType.BUG,
        summary=_conflict_decision_summary(record),
        rationale=(
            f"PR review-loop observed a merge conflict on {record.pr_url}. "
            "Resolving in-place on the same branch."
        ),
        diff_or_plan=_conflict_decision_plan(record),
        risk="low",
        proposer_role="conflict_resolution",
        proposer_agent_id=f"conflict_resolution@{record.project}",
        proposer_display_name="Conflict resolver",
        status=DecisionStatus.APPROVED,
        priority="p2",
        expedited=True,
        requested_by_role="pr_review_loop",
        resolved_reason="auto-approved by PR review loop",
        resolved_at=datetime.now(tz=UTC),
        **in_place_extras,  # type: ignore[arg-type]
    )
    store.save(fix)
    return fix


def _find_merged_linked_followup(
    *,
    store: DecisionStore,
    engineer_runs_store: EngineerRunStore,
    record: EngineerRunRecord,
    github: GitHubClient,
) -> EngineerRunRecord | None:
    for candidate in engineer_runs_store.list_all():
        if candidate.decision_id == record.decision_id:
            continue
        if candidate.project != record.project or candidate.pr_number is None:
            continue
        decision = store.get(candidate.decision_id)
        if decision is None:
            continue
        if decision.proposer_role not in {
            "pr_followup",
            "creator_response",
            "conflict_resolution",
        }:
            continue
        if not _decision_mentions_record(decision, record):
            continue
        if candidate.pr_state == "merged" or candidate.merged_at is not None:
            return candidate
        try:
            pr = github.get_pull_request(candidate.pr_number)
        except Exception:  # noqa: BLE001
            continue
        if pr.merged:
            candidate.pr_state = "merged"
            candidate.merged_at = datetime.now(tz=UTC)
            if candidate.pr_url is None:
                candidate.pr_url = pr.html_url
            engineer_runs_store.update(candidate)
            return candidate
    return None


def run_pr_review_loop(
    *,
    projects_dir: Path,
    store: DecisionStore,
    engineer_runs_store: EngineerRunStore,
    open_github_client: Callable[[Manifest], GitHubClient | None],
    dry_run: bool = False,
    review_builder: ReviewBuilder | None = None,
    api_key: str | None = None,
) -> PRReviewLoopReport:
    """Assign and run internal crew reviewers for open minions PRs.

    When ``api_key`` is provided AND no ``review_builder`` override is
    supplied, reviewers run LLM-driven via ``crews.pr_reviewer.run_pr_review``
    — they actually read the diff + prior comments. Without ``api_key``,
    the legacy deterministic stub is used (kept for tests + dry-runs).
    """

    started = datetime.now(tz=UTC).isoformat()
    manifests = load_active_manifests(projects_dir)
    outcomes: list[PRReviewLoopOutcome] = []
    build_review = review_builder or _make_llm_review_builder(api_key)

    for record in engineer_runs_store.list_all():
        if not _is_open(record) or record.dry_run or record.skipped:
            continue

        decision = store.get(record.decision_id)
        if decision is None:
            outcomes.append(
                PRReviewLoopOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    status="skipped",
                    reason="source decision not found",
                )
            )
            continue

        manifest = manifests.get(record.project)
        if manifest is None or manifest.source.kind != "github" or not manifest.source.repo:
            continue

        github = open_github_client(manifest)
        if github is None:
            outcomes.append(
                PRReviewLoopOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    status="error",
                    reason="failed to open GitHub client",
                )
            )
            continue

        try:
            with github:
                linked_followup = _find_merged_linked_followup(
                    store=store,
                    engineer_runs_store=engineer_runs_store,
                    record=record,
                    github=github,
                )
                if linked_followup is not None:
                    record.review_status = "superseded"
                    record.pr_state = "closed"
                    record.superseded_by_pr_url = linked_followup.pr_url
                    record.superseded_at = datetime.now(tz=UTC)
                    if not dry_run:
                        github.comment_on_pull_request(
                            number=record.pr_number or 0,
                            body=_superseded_comment(record, linked_followup),
                        )
                        github.close_pull_request(number=record.pr_number or 0)
                        engineer_runs_store.update(record)

                    outcomes.append(
                        PRReviewLoopOutcome(
                            decision_id=record.decision_id,
                            project=record.project,
                            pr_url=record.pr_url,
                            status="superseded",
                            reason="linked follow-up PR merged",
                            review_status=record.review_status,
                        )
                    )
                    continue

                merge_state = github.get_pr_merge_state(record.pr_number or 0)
                if merge_state == "dirty" and record.conflict_resolution_queued_at is None:
                    # pr-ownership-sweep Phase 4: do NOT file a new
                    # "Resolve merge conflict" Decision Record. The owner
                    # sweep (scheduled/pr_owner_sweep.py) walks this same
                    # record next tick, sees merge_state=dirty, and
                    # re-dispatches the original owner agent in-place on
                    # the existing branch. Sticky followup_attempts on
                    # the record itself bounds retries.
                    if not dry_run:
                        record.review_status = "conflict_queued"
                        record.conflict_resolution_queued_at = datetime.now(tz=UTC)
                        engineer_runs_store.update(record)
                    outcomes.append(
                        PRReviewLoopOutcome(
                            decision_id=record.decision_id,
                            project=record.project,
                            pr_url=record.pr_url,
                            status="conflict_queued",
                            reason="merge_state=dirty (owner sweep will retry)",
                            fix_decision_id=None,
                            review_status="conflict_queued",
                        )
                    )
                    continue

                ci_conclusion, details_url = github.get_pr_check_status(record.pr_number or 0)
                record.ci_conclusion = ci_conclusion
                record.ci_last_checked_at = datetime.now(tz=UTC)

                assigned_now = False
                if not record.reviewers:
                    record.reviewers = _default_reviewers(decision)
                    record.review_status = "assigned"
                    record.review_started_at = datetime.now(tz=UTC)
                    assigned_now = True
                    if not dry_run:
                        github.comment_on_pull_request(
                            number=record.pr_number or 0,
                            body=_assignment_comment(record),
                        )

                # Pre-fetch PR context once per PR so each reviewer dispatch
                # below sees the same diff + comment snapshot. Best-effort —
                # failures fall through (LLM reviewer treats missing context
                # as "(no files reported)").
                with suppress(Exception):
                    _LLM_CONTEXT[(record.decision_id, "files")] = github.list_pull_request_files(
                        number=record.pr_number or 0
                    )
                with suppress(Exception):
                    _LLM_CONTEXT[(record.decision_id, "comments")] = github.list_issue_comments(
                        number=record.pr_number or 0
                    )

                comments_posted = 0
                for reviewer in record.reviewers:
                    if reviewer.comment_posted_at is not None:
                        continue
                    review = build_review(decision, record, reviewer, ci_conclusion, details_url)
                    if not dry_run:
                        github.comment_on_pull_request(
                            number=record.pr_number or 0,
                            body=review.body,
                        )
                    reviewer.verdict = review.verdict
                    reviewer.summary = review.summary
                    reviewer.status = (
                        "approved"
                        if review.verdict == "approve"
                        else "changes_requested"
                        if review.verdict == "request_changes"
                        else "commented"
                    )
                    reviewer.comment_posted_at = datetime.now(tz=UTC)
                    comments_posted += 1

                _advance_review_status(record)

                fix_decision_id: str | None = None
                outcome_status: OutcomeStatus = "assigned" if assigned_now else "reviewed"

                if (
                    record.review_status == "changes_requested"
                    and record.creator_response_posted_at is None
                ):
                    if record.review_round < 1:
                        fix = None
                        if not dry_run:
                            fix = _queue_creator_fix_decision(store=store, record=record)
                            fix_decision_id = str(fix.id)
                        record.review_round += 1
                        record.creator_response_posted_at = datetime.now(tz=UTC)
                        if not dry_run:
                            github.comment_on_pull_request(
                                number=record.pr_number or 0,
                                body=_creator_response_comment(
                                    record,
                                    fix_id=fix_decision_id,
                                ),
                            )
                        outcome_status = "creator_responded"
                    else:
                        record.merge_blocked_reason = (
                            "crew requested changes after the single creator response iteration"
                        )

                if (
                    record.review_status == "crew_approved"
                    and record.merge_attempted_at is None
                    and ci_conclusion == "success"
                ):
                    record.merge_attempted_at = datetime.now(tz=UTC)
                    if not dry_run:
                        try:
                            merged = github.merge_pull_request(
                                number=record.pr_number or 0,
                                commit_title=f"Merge PR #{record.pr_number}: {decision.summary}",
                            )
                        except GitHubError as e:
                            record.review_status = "merge_blocked"
                            record.merge_blocked_reason = str(e)
                            if record.human_handoff_posted_at is None:
                                github.comment_on_pull_request(
                                    number=record.pr_number or 0,
                                    body=_human_handoff_comment(record, str(e)),
                                )
                                record.human_handoff_posted_at = datetime.now(tz=UTC)
                            outcome_status = "handoff"
                        else:
                            if merged:
                                record.review_status = "merged"
                                record.pr_state = "merged"
                                record.merged_at = datetime.now(tz=UTC)
                                outcome_status = "merged"
                            else:
                                record.review_status = "merge_blocked"
                                record.merge_blocked_reason = (
                                    "GitHub merge endpoint returned merged=false"
                                )
                                outcome_status = "handoff"
                    else:
                        outcome_status = "reviewed"

                if not dry_run:
                    engineer_runs_store.update(record)

                outcomes.append(
                    PRReviewLoopOutcome(
                        decision_id=record.decision_id,
                        project=record.project,
                        pr_url=record.pr_url,
                        status=outcome_status,
                        assigned_reviewers=[r.role for r in record.reviewers],
                        comments_posted=comments_posted,
                        review_status=record.review_status,
                        fix_decision_id=fix_decision_id,
                    )
                )
        except Exception as e:  # noqa: BLE001
            outcomes.append(
                PRReviewLoopOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    status="error",
                    reason=f"{type(e).__name__}: {e}",
                )
            )

    return PRReviewLoopReport(
        started_at=started,
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
    )
