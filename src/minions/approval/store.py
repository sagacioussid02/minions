"""Decision Store — JSON-file backed in v0; swaps for Neon Postgres in Phase 6.

Source of truth for Decision Records across CLI invocations. The CLI's
``decisions approve/reject`` commands update this store; long-running
orchestrator processes (when present) poll for newly-resolved Decisions
to advance their workflows.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from minions.models.decision import Decision, DecisionStatus


class DecisionStore:
    """JSON-file backed store. Atomic writes via tempfile + os.replace
    (good enough for single-operator v0; Postgres in Phase 6 handles
    concurrent writes with row-level locking).
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}")

    def _load_all(self) -> dict[str, dict]:
        text = self.path.read_text()
        if not text.strip():
            return {}
        return json.loads(text)

    def _save_all(self, data: dict[str, dict]) -> None:
        # Atomic-ish: write to .tmp then rename.
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str))
        tmp.replace(self.path)

    def save(self, decision: Decision) -> None:
        data = self._load_all()
        data[str(decision.id)] = decision.model_dump(mode="json")
        self._save_all(data)

    def get(self, decision_id: UUID | str) -> Decision | None:
        raw = self._load_all().get(str(decision_id))
        if raw is None:
            return None
        return Decision.model_validate(raw)

    def list_all(self) -> list[Decision]:
        return [Decision.model_validate(raw) for raw in self._load_all().values()]

    def list_by_status(self, status: DecisionStatus) -> list[Decision]:
        return [d for d in self.list_all() if d.status == status]

    def update_status(
        self,
        decision_id: UUID | str,
        status: DecisionStatus,
        reason: str | None = None,
    ) -> Decision:
        d = self.get(decision_id)
        if d is None:
            raise KeyError(decision_id)
        d.status = status
        d.resolved_at = datetime.now(UTC)
        d.resolved_reason = reason
        self.save(d)
        return d
