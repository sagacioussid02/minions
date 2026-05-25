"""Per-project sprint counter — JSON-backed default.

Phase 2 of openspec/sprint-tasks-memory. First time a project is seen,
``bump()`` returns 0 (Sprint 0). Every subsequent call returns the next
integer atomically. JSON path uses an OS-level file lock; the Postgres
path uses a single atomic UPDATE.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class SprintCounter(BaseModel):
    project: str
    current_sprint_number: int
    updated_at: datetime


class SprintCounterStore:
    """JSON file backing for sprint counters. Atomic via file lock.

    File shape: `{ "<project>": {"n": int, "updated_at": iso} }`.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, default=str))

    def current(self, project: str) -> int | None:
        """Return the current sprint number, or None if the project is new."""
        data = self._load()
        record = data.get(project)
        if not record:
            return None
        return int(record["n"])

    def bump(self, project: str) -> int:
        """Atomically advance the counter and return the new value.

        First call for a project returns 0 (Sprint 0). Subsequent calls
        return 1, 2, 3, …
        """
        # File-based lock — coarse but sufficient for our cron frequency.
        # The Postgres backend uses an atomic UPDATE so it does not need this.
        import filelock
        lock_path = str(self.path) + ".lock"
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        with filelock.FileLock(lock_path, timeout=10):
            data = self._load()
            record = data.get(project)
            new_n = 0 if record is None else int(record["n"]) + 1
            data[project] = {
                "n": new_n,
                "updated_at": datetime.now(tz=UTC).isoformat(),
            }
            self._save(data)
            return new_n

    def list_all(self) -> list[SprintCounter]:
        return [
            SprintCounter(
                project=p,
                current_sprint_number=int(r["n"]),
                updated_at=datetime.fromisoformat(r["updated_at"]),
            )
            for p, r in self._load().items()
        ]
