"""JSON-backed Task store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from minions.models.task import Task, TaskStatus


class TaskStore:
    """Tasks keyed by id; iterable by decision_id / project / owner."""

    def __init__(self, path: Path) -> None:
        self.path = path

    # ---- low-level ----

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, default=str))

    # ---- writes ----

    def save(self, task: Task) -> Task:
        """Upsert — last write wins."""
        data = self._load()
        task.updated_at = datetime.now(tz=UTC)
        data[str(task.id)] = task.model_dump(mode="json")
        self._save(data)
        return task

    def update_status(
        self,
        task_id: UUID | str,
        status: TaskStatus,
        *,
        pr_url: str | None = None,
        pr_number: int | None = None,
    ) -> Task:
        t = self.get(task_id)
        if t is None:
            raise KeyError(task_id)
        t.status = status
        if pr_url is not None:
            t.pr_url = pr_url
        if pr_number is not None:
            t.pr_number = pr_number
        if status == "done":
            t.completed_at = datetime.now(tz=UTC)
        self.save(t)
        return t

    # ---- reads ----

    def get(self, task_id: UUID | str) -> Task | None:
        data = self._load()
        raw = data.get(str(task_id))
        return Task.model_validate(raw) if raw else None

    def list_all(self) -> list[Task]:
        return [Task.model_validate(v) for v in self._load().values()]

    def list_by_decision(self, decision_id: UUID | str) -> list[Task]:
        return [t for t in self.list_all() if str(t.decision_id) == str(decision_id)]

    def list_by_project(self, project: str, *, sprint_number: int | None = None) -> list[Task]:
        out = [t for t in self.list_all() if t.project == project]
        if sprint_number is not None:
            out = [t for t in out if t.sprint_number == sprint_number]
        return out

    def list_by_owner(self, owner_agent_id: str) -> list[Task]:
        return [t for t in self.list_all() if t.owner_agent_id == owner_agent_id]

    def count_open_by_owner(self) -> dict[str, int]:
        """Owner load — count of non-terminal tasks per agent_id.

        Used by the refinement crew's round-robin to break ties when
        multiple agents share a role.
        """
        out: dict[str, int] = {}
        for t in self.list_all():
            if t.status in ("queued", "in_progress", "review"):
                out[t.owner_agent_id] = out.get(t.owner_agent_id, 0) + 1
        return out
