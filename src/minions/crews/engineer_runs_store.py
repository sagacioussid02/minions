"""Persistence for ``EngineerResult`` objects.

Today the engineer crew returns results in-memory only; the dashboard's
sprint board can't tell "approved decision waiting for engineer" from
"PR open" because the decision status is the same (``EXECUTED``) once the
PR is opened. This store closes that gap.

Phase 6 swap: replace JSON with the Neon Postgres engineer_runs table.
"""

from __future__ import annotations

import json
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from minions.crews.engineer import EngineerResult

ReviewStatus = Literal[
    "not_started",
    "assigned",
    "reviewing",
    "changes_requested",
    "creator_responded",
    "crew_approved",
    "merge_attempted",
    "merge_blocked",
    "merged",
    "conflict_queued",
    "superseded",
]
ReviewerStatus = Literal["assigned", "commented", "approved", "changes_requested"]
ReviewerVerdict = Literal["approve", "request_changes", "comment"]


class PRReviewerAssignment(BaseModel):
    """One internal crew reviewer assigned to a PR."""

    role: str
    agent_id: str
    display_name: str
    status: ReviewerStatus = "assigned"
    verdict: ReviewerVerdict | None = None
    summary: str | None = None
    comment_posted_at: datetime | None = None


class EngineerRunRecord(BaseModel):
    """One persisted engineer run, keyed by decision_id (last write wins)."""

    decision_id: str
    task_id: str | None = None
    project: str
    # Set for tenant-project runs (from Manifest.tenant_id); None means the
    # founder. Two tenants can independently pick the same project display
    # name, so anything that groups runs by `project` alone (e.g.
    # distinct_open_pr_count) must also match on this.
    tenant_id: str | None = None
    completed_at: datetime
    pr_url: str | None = None
    pr_number: int | None = None
    branch_name: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    files_rejected: list[str] = Field(default_factory=list)
    operator_comment_posted: bool = False
    skipped: bool = False
    skip_reason: str | None = None
    dry_run: bool = False

    # Populated by sync_pr_status — None until the first sync, then either
    # "open" / "merged" / "closed" with merged_at when merged.
    pr_state: str | None = None
    merged_at: datetime | None = None
    last_synced_at: datetime | None = None

    # Populated by the PR follow-up sweep.
    ci_conclusion: str | None = None  # "success" / "failure" / "pending" / None
    ci_last_checked_at: datetime | None = None
    followup_attempts: int = 0
    last_followup_at: datetime | None = None

    # Set once the QA crew has posted a review comment on the PR.
    qa_review_posted_at: datetime | None = None

    # Populated by the PR review-loop sweep. This is the explicit state that the
    # Sprint Board should prefer over inferred reviewer status.
    review_status: ReviewStatus = "not_started"
    review_round: int = 0
    reviewers: list[PRReviewerAssignment] = Field(default_factory=list)
    review_started_at: datetime | None = None
    creator_response_posted_at: datetime | None = None
    crew_approved_at: datetime | None = None
    merge_attempted_at: datetime | None = None
    merge_blocked_reason: str | None = None
    human_handoff_posted_at: datetime | None = None
    conflict_resolution_queued_at: datetime | None = None
    superseded_by_pr_url: str | None = None
    superseded_at: datetime | None = None

    # PR ownership — set at PR-open time, sticky for the PR's life. The
    # owner sweep dispatches THIS exact agent on every retry, so a single
    # accountable engineer ships the PR from open to merge regardless of
    # how many CI failures or conflicts happen in between.
    #
    # Legacy rows (created before this field shipped) default to the
    # canonical engineer seat. The first owner-sweep tick after rollout
    # backfills any None values via the same default.
    owner_agent_id: str | None = None
    # Set once the owner-sweep escalated this PR to the operator after
    # ``flow_control.max_retries_per_pr`` failed retries. Idempotent
    # gate: subsequent ticks see this and skip.
    escalated_question_id: str | None = None
    # Cached classification of the last failure observed by the owner
    # sweep, used to phrase the next prompt + the operator handoff.
    last_failure_kind: str | None = None  # "ci_failure" | "merge_conflict" | None


class EngineerRunStore:
    """JSON file at ``data/local/engineer_runs.json`` keyed by decision_id."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load_all(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_all(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, default=str))

    def save(
        self, result: EngineerResult, *, project: str, tenant_id: str | None = None
    ) -> EngineerRunRecord:
        # Sticky-owner default: prefer the agent the engineer crew reported;
        # fall back to the canonical engineer seat so legacy callsites
        # (older test fixtures, replays) still get a non-None owner.
        owner = getattr(result, "owner_agent_id", None) or f"engineer@{project}"
        record = EngineerRunRecord(
            decision_id=result.decision_id,
            task_id=result.task_id,
            project=project,
            tenant_id=tenant_id,
            completed_at=datetime.now(tz=UTC),
            pr_url=result.pr_url,
            pr_number=result.pr_number,
            branch_name=result.branch_name,
            files_changed=list(result.files_changed),
            files_rejected=list(result.files_rejected),
            operator_comment_posted=result.operator_comment_posted,
            skipped=result.skipped,
            skip_reason=result.skip_reason,
            dry_run=result.dry_run,
            owner_agent_id=owner,
        )
        all_data = self._load_all()
        all_data[result.decision_id] = record.model_dump(mode="json")
        self._save_all(all_data)
        self._capture_learning(record)
        return record

    def get(self, decision_id: str) -> EngineerRunRecord | None:
        raw = self._load_all().get(decision_id)
        if raw is None:
            return None
        return EngineerRunRecord.model_validate(raw)

    def list_all(self) -> list[EngineerRunRecord]:
        return [EngineerRunRecord.model_validate(v) for v in self._load_all().values()]

    def list_by_project(self, project: str) -> list[EngineerRunRecord]:
        return [r for r in self.list_all() if r.project == project]

    def update(self, record: EngineerRunRecord) -> EngineerRunRecord:
        """Replace the record for ``record.decision_id``. Use after sync."""
        all_data = self._load_all()
        all_data[record.decision_id] = record.model_dump(mode="json")
        self._save_all(all_data)
        self._capture_learning(record)
        return record

    def _capture_learning(self, record: EngineerRunRecord) -> None:
        with suppress(Exception):
            from minions.learning.capture import capture_engineer_run
            from minions.learning.store import AgentLearningStore

            capture_engineer_run(
                record,
                AgentLearningStore(self.path.parent / "agent_learning.json"),
            )
