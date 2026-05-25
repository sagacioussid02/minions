"""Postgres-backed sprint counter."""

from __future__ import annotations

from datetime import datetime

from minions.db.connection import connect
from minions.sprints.store import SprintCounter


class PostgresSprintCounterStore:
    """Backed by the ``sprint_counters`` table.

    Atomicity comes from a single ``INSERT … ON CONFLICT DO UPDATE`` round
    trip — no file lock needed.
    """

    def current(self, project: str) -> int | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT current_sprint_number FROM sprint_counters WHERE project = %s",
                (project,),
            )
            row = cur.fetchone()
        return None if row is None else int(row[0])

    def bump(self, project: str) -> int:
        """Atomic upsert + increment. First call returns 0, then 1, 2, …"""
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO sprint_counters (project, current_sprint_number, updated_at)
                VALUES (%s, 0, NOW())
                ON CONFLICT (project) DO UPDATE
                  SET current_sprint_number = sprint_counters.current_sprint_number + 1,
                      updated_at = NOW()
                RETURNING current_sprint_number
                """,
                (project,),
            )
            row = cur.fetchone()
        return int(row[0])

    def list_all(self) -> list[SprintCounter]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT project, current_sprint_number, updated_at "
                "FROM sprint_counters ORDER BY project"
            )
            rows = cur.fetchall()
        return [
            SprintCounter(
                project=str(r[0]),
                current_sprint_number=int(r[1]),
                updated_at=r[2]
                if isinstance(r[2], datetime)
                else datetime.fromisoformat(str(r[2])),
            )
            for r in rows
        ]
