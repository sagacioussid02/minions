"""Decision Store backend selector.

Behavior:
- ``MINIONS_STORE_BACKEND=postgres`` → PostgresDecisionStore (raises if no URL).
- ``MINIONS_STORE_BACKEND=json`` → JSON DecisionStore at ``path``.
- unset → Postgres if a database URL resolves cleanly, else JSON.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from uuid import UUID

from minions.db.connection import has_database_url
from minions.models.decision import Decision, DecisionStatus


class DecisionStoreLike(Protocol):
    def save(self, decision: Decision) -> None: ...
    def get(self, decision_id: UUID | str) -> Decision | None: ...
    def list_all(self) -> list[Decision]: ...
    def list_by_status(self, status: DecisionStatus) -> list[Decision]: ...
    def update_status(
        self,
        decision_id: UUID | str,
        status: DecisionStatus,
        reason: str | None = None,
    ) -> Decision: ...


def make_decision_store(json_path: Path) -> DecisionStoreLike:
    """Pick a Decision Store backend per env. ``json_path`` is the JSON fallback."""
    backend = (os.environ.get("MINIONS_STORE_BACKEND") or "").lower()
    if backend == "postgres":
        from minions.approval.store_postgres import PostgresDecisionStore

        return PostgresDecisionStore()
    if backend == "json":
        from minions.approval.store import DecisionStore

        return DecisionStore(json_path)
    if has_database_url():
        from minions.approval.store_postgres import PostgresDecisionStore

        return PostgresDecisionStore()
    from minions.approval.store import DecisionStore

    return DecisionStore(json_path)
