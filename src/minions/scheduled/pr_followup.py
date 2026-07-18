"""PR follow-up sweep — watches minions-authored PRs and reacts to CI failures.

Schedule: every ~30 min (per `.github/workflows/pr_followup.yml`).

For each open engineer-run PR:
  1. Fetch the latest CI conclusion (success / failure / pending / None).
  2. Persist it on the EngineerRunRecord.
  3. On *failure* (and ``followup_attempts < max_attempts``):
       * Post a comment on the PR explaining the agent noticed the failure.
       * Write a new "fix" Decision Record (auto-approved, low risk) referencing
         the failing PR. The existing ``execute-approved`` sweep then runs the
         engineer crew against it on its next tick.
       * Bump ``followup_attempts`` on the original record.

This is the autonomy lever that turns the org from "one-shot PRs" into "agents
iterating on their own work". It does **not** push commits to the existing
branch — fix attempts go through a fresh Decision/PR so every change is still
audited through the normal pipeline.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.approval.store import DecisionStore
from minions.crews.engineer_runs_store import EngineerRunRecord, EngineerRunStore
from minions.crews.qa import render_pr_comment as render_qa_comment
from minions.crews.qa import run_qa_review
from minions.models.manifest import Manifest, load_active_manifests
from minions.notify.base import Notifier

if TYPE_CHECKING:
    from minions.github.client import GitHubClient


class PRFollowupOutcome(BaseModel):
    decision_id: str
    project: str
    pr_url: str | None = None
    ci_conclusion: str | None = None
    status: Literal["ok", "queued_fix", "skipped", "error"]
    reason: str | None = None
    fix_decision_id: str | None = None


class PRFollowupReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[PRFollowupOutcome] = Field(default_factory=list)

    @property
    def queued_fixes(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "queued_fix")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")


def _is_open(record: EngineerRunRecord) -> bool:
    # Treat unsynced records (pr_state=None) as still open — daily monitor will
    # eventually sync them, but we don't want to skip them in the meantime.
    return record.pr_state in (None, "open")


def _fix_decision_summary(record: EngineerRunRecord) -> str:
    pr_ref = f"PR #{record.pr_number}" if record.pr_number else "the prior PR"
    return f"Fix CI failure on {pr_ref} ({record.project})"


def _fix_decision_plan(record: EngineerRunRecord, ci_details_url: str | None) -> str:
    file_lines = (
        [f"- `{f}`" for f in record.files_changed]
        if record.files_changed
        else ["- (none recorded)"]
    )
    lines = [
        f"## CI failed on {record.pr_url or 'prior PR'}",
        "",
        "The PR follow-up agent observed a failed check run on the head SHA of "
        f"branch `{record.branch_name or '?'}`.",
        "",
        "**Files in the failing PR:**",
        *file_lines,
        "",
    ]
    if ci_details_url:
        lines += [f"**CI failure details:** {ci_details_url}", ""]
    lines += [
        "## Requested change",
        "",
        "Re-run the engineer crew against the same project root. Fetch the failing "
        "check's logs, identify the root cause, and produce a corrected change. "
        "Open a fresh PR (do not push to the failing branch). Reference the failing "
        f"PR ({record.pr_url}) in the PR body so the operator can audit the chain.",
    ]
    return "\n".join(lines)


def run_pr_followup(
    *,
    projects_dir: Path,
    store: DecisionStore,
    engineer_runs_store: EngineerRunStore,
    notifier: Notifier,
    open_github_client: Callable[[Manifest], GitHubClient | None],
    max_attempts: int = 1,
    dry_run: bool = False,
    api_key: str | None = None,
    qa_enabled: bool = True,
) -> PRFollowupReport:
    """Scan open minions PRs, react to CI failures by queuing fix Decisions."""
    from datetime import UTC, datetime

    started = datetime.now(tz=UTC).isoformat()
    manifests = load_active_manifests(projects_dir)
    outcomes: list[PRFollowupOutcome] = []

    for record in engineer_runs_store.list_all():
        if not _is_open(record) or record.pr_number is None:
            continue

        manifest = manifests.get(record.project)
        if manifest is None or manifest.source.kind != "github" or not manifest.source.repo:
            continue

        github = open_github_client(manifest)
        if github is None:
            outcomes.append(
                PRFollowupOutcome(
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
                conclusion, details_url = github.get_pr_check_status(record.pr_number)
                # Always persist the CI snapshot so the dashboard sees fresh state.
                record.ci_conclusion = conclusion
                record.ci_last_checked_at = datetime.now(tz=UTC)

                if conclusion != "failure":
                    # CI green (or no checks). Run QA review once per PR.
                    if (
                        qa_enabled
                        and conclusion == "success"
                        and record.qa_review_posted_at is None
                        and api_key is not None
                        and not dry_run
                    ):
                        decision = store.get(record.decision_id)
                        if decision is not None:
                            try:
                                qa_review = run_qa_review(
                                    decision,
                                    files_changed=record.files_changed,
                                    api_key=api_key,
                                )
                                if qa_review is not None:
                                    github.comment_on_pull_request(
                                        number=record.pr_number,
                                        body=render_qa_comment(qa_review),
                                    )
                                    record.qa_review_posted_at = datetime.now(tz=UTC)
                            except Exception:  # noqa: BLE001 — QA is advisory
                                pass

                    engineer_runs_store.update(record)
                    outcomes.append(
                        PRFollowupOutcome(
                            decision_id=record.decision_id,
                            project=record.project,
                            pr_url=record.pr_url,
                            ci_conclusion=conclusion,
                            status="ok",
                        )
                    )
                    continue

                # Dedupe + cap guards: prevent the runaway PR loop where
                # each fix Decision spawns a new engineer_runs row whose
                # followup_attempts counter starts at 0, defeating the
                # per-record max_attempts cap.
                from minions.crews.flow_control import (
                    distinct_open_pr_count,
                    has_open_fix_decision_for_pr,
                )

                if has_open_fix_decision_for_pr(
                    project=record.project,
                    pr_number=record.pr_number,
                    store=store,
                ):
                    outcomes.append(
                        PRFollowupOutcome(
                            decision_id=record.decision_id,
                            project=record.project,
                            pr_url=record.pr_url,
                            ci_conclusion=conclusion,
                            status="skipped",
                            reason=(
                                f"a fix Decision is already pending/approved for "
                                f"PR #{record.pr_number}"
                            ),
                        )
                    )
                    continue

                open_prs = distinct_open_pr_count(
                    project=record.project,
                    engineer_runs_store=engineer_runs_store,
                    tenant_id=manifest.tenant_id,
                )
                cap = manifest.flow_control.max_open_prs
                if open_prs >= cap:
                    outcomes.append(
                        PRFollowupOutcome(
                            decision_id=record.decision_id,
                            project=record.project,
                            pr_url=record.pr_url,
                            ci_conclusion=conclusion,
                            status="skipped",
                            reason=(
                                f"open_pr_cap={cap} reached "
                                f"({open_prs} open) — merge or close PRs first"
                            ),
                        )
                    )
                    continue

                if record.followup_attempts >= max_attempts:
                    engineer_runs_store.update(record)
                    outcomes.append(
                        PRFollowupOutcome(
                            decision_id=record.decision_id,
                            project=record.project,
                            pr_url=record.pr_url,
                            ci_conclusion=conclusion,
                            status="skipped",
                            reason=f"followup_attempts={record.followup_attempts} ≥ max={max_attempts}",
                        )
                    )
                    continue

                # pr-ownership-sweep Phase 4: do NOT file a new "Fix CI
                # failure" Decision Record. The owner sweep
                # (scheduled/pr_owner_sweep.py) is now solely responsible
                # for CI-failure retries: it walks the same record, sees
                # ci_conclusion=failure, re-dispatches the original owner
                # agent in-place on the existing branch, bumps its own
                # sticky followup_attempts, and escalates to the operator
                # via a Question Record after the cap.
                #
                # pr_followup's remaining job is the CI snapshot above +
                # the QA review on the success path. Don't touch the
                # counter here — owner sweep owns it.
                engineer_runs_store.update(record)
                outcomes.append(
                    PRFollowupOutcome(
                        decision_id=record.decision_id,
                        project=record.project,
                        pr_url=record.pr_url,
                        ci_conclusion=conclusion,
                        status="ok",
                        reason="ci=failure (owner sweep will retry)",
                    )
                )
                continue
        except Exception as e:  # noqa: BLE001
            outcomes.append(
                PRFollowupOutcome(
                    decision_id=record.decision_id,
                    project=record.project,
                    pr_url=record.pr_url,
                    status="error",
                    reason=f"{type(e).__name__}: {e}",
                )
            )

    return PRFollowupReport(
        started_at=started,
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
    )
