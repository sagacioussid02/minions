"""Sprint counter backend selector. Same pattern as the other stores."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from minions.db.connection import has_database_url
from minions.sprints.store import SprintCounter


class SprintCounterStoreLike(Protocol):
    def current(self, project: str) -> int | None: ...
    def bump(self, project: str) -> int: ...
    def list_all(self) -> list[SprintCounter]: ...


def make_sprint_counter_store(json_path: Path) -> SprintCounterStoreLike:
    """Pick a sprint-counter backend per env. ``json_path`` is JSON fallback."""
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.sprints.store_postgres import PostgresSprintCounterStore
        return PostgresSprintCounterStore()
    if backend == "json":
        from minions.sprints.store import SprintCounterStore
        return SprintCounterStore(json_path)
    if has_database_url():
        from minions.sprints.store_postgres import PostgresSprintCounterStore
        return PostgresSprintCounterStore()
    from minions.sprints.store import SprintCounterStore
    return SprintCounterStore(json_path)
