"""Postgres-backed Task store."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.task import Task, TaskStatus


class PostgresTaskStore:
    """Backed by the ``tasks`` table created in migration 0005."""

    def save(self, task: Task) -> Task:
        task.updated_at = datetime.now(tz=UTC)
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO tasks (
                    id, decision_id, project, sprint_number, category, title,
                    description, acceptance_criteria, owner_role, owner_agent_id,
                    owner_display_name, estimated_effort, status, pr_url, pr_number,
                    created_at, updated_at, completed_at, payload
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s
                )
                ON CONFLICT (id) DO UPDATE SET
                    sprint_number = EXCLUDED.sprint_number,
                    category = EXCLUDED.category,
                    title = EXCLUDED.title,
                    description = EXCLUDED.description,
                    acceptance_criteria = EXCLUDED.acceptance_criteria,
                    owner_role = EXCLUDED.owner_role,
                    owner_agent_id = EXCLUDED.owner_agent_id,
                    owner_display_name = EXCLUDED.owner_display_name,
                    estimated_effort = EXCLUDED.estimated_effort,
                    status = EXCLUDED.status,
                    pr_url = EXCLUDED.pr_url,
                    pr_number = EXCLUDED.pr_number,
                    updated_at = EXCLUDED.updated_at,
                    completed_at = EXCLUDED.completed_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(task.id),
                    str(task.decision_id),
                    task.project,
                    task.sprint_number,
                    task.category,
                    task.title,
                    task.description,
                    task.acceptance_criteria,
                    task.owner_role,
                    task.owner_agent_id,
                    task.owner_display_name,
                    task.estimated_effort,
                    task.status,
                    task.pr_url,
                    task.pr_number,
                    task.created_at,
                    task.updated_at,
                    task.completed_at,
                    Jsonb(task.payload),
                ),
            )
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

    def get(self, task_id: UUID | str) -> Task | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(_SELECT_COLS + " WHERE id = %s::uuid", (str(task_id),))
            row = cur.fetchone()
        return _row_to_task(row) if row else None

    def list_all(self) -> list[Task]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(_SELECT_COLS + " ORDER BY created_at DESC")
            rows = cur.fetchall()
        return [_row_to_task(r) for r in rows]

    def list_by_decision(self, decision_id: UUID | str) -> list[Task]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                _SELECT_COLS + " WHERE decision_id = %s::uuid ORDER BY created_at",
                (str(decision_id),),
            )
            rows = cur.fetchall()
        return [_row_to_task(r) for r in rows]

    def list_by_project(self, project: str, *, sprint_number: int | None = None) -> list[Task]:
        with connect() as conn, conn.cursor() as cur:
            if sprint_number is None:
                cur.execute(
                    _SELECT_COLS + " WHERE project = %s ORDER BY sprint_number DESC, created_at",
                    (project,),
                )
            else:
                cur.execute(
                    _SELECT_COLS + " WHERE project = %s AND sprint_number = %s ORDER BY created_at",
                    (project, sprint_number),
                )
            rows = cur.fetchall()
        return [_row_to_task(r) for r in rows]

    def list_by_owner(self, owner_agent_id: str) -> list[Task]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                _SELECT_COLS + " WHERE owner_agent_id = %s ORDER BY created_at DESC",
                (owner_agent_id,),
            )
            rows = cur.fetchall()
        return [_row_to_task(r) for r in rows]

    def count_open_by_owner(self) -> dict[str, int]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT owner_agent_id, COUNT(*) "
                "FROM tasks "
                "WHERE status IN ('queued', 'in_progress', 'review') "
                "GROUP BY owner_agent_id"
            )
            rows = cur.fetchall()
        return {str(r[0]): int(r[1]) for r in rows}


_SELECT_COLS = (
    "SELECT id::text, decision_id::text, project, sprint_number, category, title, "
    "       description, acceptance_criteria, owner_role, owner_agent_id, "
    "       owner_display_name, estimated_effort, status, pr_url, pr_number, "
    "       created_at, updated_at, completed_at, payload "
    "FROM tasks"
)


def _row_to_task(row: tuple) -> Task:
    (
        id_,
        decision_id,
        project,
        sprint_number,
        category,
        title,
        description,
        acceptance_criteria,
        owner_role,
        owner_agent_id,
        owner_display_name,
        estimated_effort,
        status,
        pr_url,
        pr_number,
        created_at,
        updated_at,
        completed_at,
        payload,
    ) = row
    if isinstance(payload, str):
        payload = json.loads(payload)
    return Task.model_validate(
        {
            "id": id_,
            "decision_id": decision_id,
            "project": project,
            "sprint_number": sprint_number,
            "category": category,
            "title": title,
            "description": description,
            "acceptance_criteria": acceptance_criteria or "",
            "owner_role": owner_role,
            "owner_agent_id": owner_agent_id,
            "owner_display_name": owner_display_name,
            "estimated_effort": estimated_effort,
            "status": status,
            "pr_url": pr_url,
            "pr_number": pr_number,
            "created_at": created_at,
            "updated_at": updated_at,
            "completed_at": completed_at,
            "payload": payload or {},
        }
    )
