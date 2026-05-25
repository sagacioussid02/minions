"""Task store backend selector. Same pattern as the other stores."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from uuid import UUID

from minions.db.connection import has_database_url
from minions.models.task import Task, TaskStatus


class TaskStoreLike(Protocol):
    def save(self, task: Task) -> Task: ...
    def update_status(
        self, task_id: UUID | str, status: TaskStatus, *,
        pr_url: str | None = None, pr_number: int | None = None,
    ) -> Task: ...
    def get(self, task_id: UUID | str) -> Task | None: ...
    def list_all(self) -> list[Task]: ...
    def list_by_decision(self, decision_id: UUID | str) -> list[Task]: ...
    def list_by_project(self, project: str, *, sprint_number: int | None = None) -> list[Task]: ...
    def list_by_owner(self, owner_agent_id: str) -> list[Task]: ...
    def count_open_by_owner(self) -> dict[str, int]: ...


def make_task_store(json_path: Path) -> TaskStoreLike:
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.tasks.store_postgres import PostgresTaskStore
        return PostgresTaskStore()
    if backend == "json":
        from minions.tasks.store import TaskStore
        return TaskStore(json_path)
    if has_database_url():
        from minions.tasks.store_postgres import PostgresTaskStore
        return PostgresTaskStore()
    from minions.tasks.store import TaskStore
    return TaskStore(json_path)
