"""Persistence for ``EngineerResult`` objects.

Today the engineer crew returns results in-memory only; the dashboard's
sprint board can't tell "approved decision waiting for engineer" from
"PR open" because the decision status is the same (``EXECUTED``) once the
PR is opened. This store closes that gap.

Phase 6 swap: replace JSON with the Neon Postgres engineer_runs table.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from minions.crews.engineer import EngineerResult


class EngineerRunRecord(BaseModel):
    """One persisted engineer run, keyed by decision_id (last write wins)."""

    decision_id: str
    project: str
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

    def save(self, result: EngineerResult, *, project: str) -> EngineerRunRecord:
        record = EngineerRunRecord(
            decision_id=result.decision_id,
            project=project,
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
        )
        all_data = self._load_all()
        all_data[result.decision_id] = record.model_dump(mode="json")
        self._save_all(all_data)
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
        return record
