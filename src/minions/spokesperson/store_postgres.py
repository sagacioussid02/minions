"""Postgres-backed spokesperson interview store."""

from __future__ import annotations

import json
from uuid import UUID

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.interview import (
    ConsultationRecord,
    InterviewMessageRecord,
    InterviewTaskProposal,
    InterviewThreadRecord,
)


class PostgresInterviewStore:
    def _ensure_tables(self) -> None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS interview_threads (
                    id uuid PRIMARY KEY,
                    scope text NOT NULL,
                    project text,
                    spokesperson_role text NOT NULL,
                    created_at timestamptz NOT NULL,
                    updated_at timestamptz NOT NULL,
                    payload jsonb NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS interview_messages (
                    id uuid PRIMARY KEY,
                    thread_id uuid NOT NULL,
                    role text NOT NULL,
                    agent_role text,
                    created_at timestamptz NOT NULL,
                    payload jsonb NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS interview_consultations (
                    id uuid PRIMARY KEY,
                    thread_id uuid NOT NULL,
                    message_id uuid NOT NULL,
                    project text,
                    consulted_role text NOT NULL,
                    status text NOT NULL,
                    created_at timestamptz NOT NULL,
                    updated_at timestamptz NOT NULL,
                    payload jsonb NOT NULL
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS interview_task_proposals (
                    id uuid PRIMARY KEY,
                    thread_id uuid NOT NULL,
                    message_id uuid NOT NULL,
                    project text,
                    owner_role text NOT NULL,
                    status text NOT NULL,
                    created_at timestamptz NOT NULL,
                    payload jsonb NOT NULL
                )
                """
            )

    def save_thread(self, record: InterviewThreadRecord) -> InterviewThreadRecord:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO interview_threads (
                    id, scope, project, spokesperson_role, created_at, updated_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    scope = EXCLUDED.scope,
                    project = EXCLUDED.project,
                    spokesperson_role = EXCLUDED.spokesperson_role,
                    updated_at = EXCLUDED.updated_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(record.id),
                    record.scope,
                    record.project,
                    record.spokesperson_role,
                    record.created_at,
                    record.updated_at,
                    Jsonb(record.model_dump(mode="json")),
                ),
            )
        return record

    def get_thread(self, thread_id: UUID | str) -> InterviewThreadRecord | None:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM interview_threads WHERE id = %s", (str(thread_id),))
            row = cur.fetchone()
        return InterviewThreadRecord.model_validate(_payload(row)) if row else None

    def list_threads(self, project: str | None = None) -> list[InterviewThreadRecord]:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            if project is None:
                cur.execute("SELECT payload FROM interview_threads ORDER BY updated_at DESC")
            else:
                cur.execute(
                    "SELECT payload FROM interview_threads WHERE project = %s ORDER BY updated_at DESC",
                    (project,),
                )
            rows = cur.fetchall()
        return [InterviewThreadRecord.model_validate(_payload(row)) for row in rows]

    def save_message(self, record: InterviewMessageRecord) -> InterviewMessageRecord:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO interview_messages (
                    id, thread_id, role, agent_role, created_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload
                """,
                (
                    str(record.id),
                    str(record.thread_id),
                    record.role,
                    record.agent_role,
                    record.created_at,
                    Jsonb(record.model_dump(mode="json")),
                ),
            )
        return record

    def list_messages(self, thread_id: UUID | str) -> list[InterviewMessageRecord]:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM interview_messages WHERE thread_id = %s ORDER BY created_at ASC",
                (str(thread_id),),
            )
            rows = cur.fetchall()
        return [InterviewMessageRecord.model_validate(_payload(row)) for row in rows]

    def save_consultation(self, record: ConsultationRecord) -> ConsultationRecord:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO interview_consultations (
                    id, thread_id, message_id, project, consulted_role, status,
                    created_at, updated_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(record.id),
                    str(record.thread_id),
                    str(record.message_id),
                    record.project,
                    record.consulted_role,
                    record.status,
                    record.created_at,
                    record.updated_at,
                    Jsonb(record.model_dump(mode="json")),
                ),
            )
        return record

    def list_consultations(self, thread_id: UUID | str) -> list[ConsultationRecord]:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM interview_consultations WHERE thread_id = %s ORDER BY created_at ASC",
                (str(thread_id),),
            )
            rows = cur.fetchall()
        return [ConsultationRecord.model_validate(_payload(row)) for row in rows]

    def save_task(self, record: InterviewTaskProposal) -> InterviewTaskProposal:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO interview_task_proposals (
                    id, thread_id, message_id, project, owner_role, status, created_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    payload = EXCLUDED.payload
                """,
                (
                    str(record.id),
                    str(record.thread_id),
                    str(record.message_id),
                    record.project,
                    record.owner_role,
                    record.status,
                    record.created_at,
                    Jsonb(record.model_dump(mode="json")),
                ),
            )
        return record

    def list_tasks(self, thread_id: UUID | str | None = None) -> list[InterviewTaskProposal]:
        self._ensure_tables()
        with connect() as conn, conn.cursor() as cur:
            if thread_id is None:
                cur.execute("SELECT payload FROM interview_task_proposals ORDER BY created_at DESC")
            else:
                cur.execute(
                    "SELECT payload FROM interview_task_proposals WHERE thread_id = %s ORDER BY created_at DESC",
                    (str(thread_id),),
                )
            rows = cur.fetchall()
        return [InterviewTaskProposal.model_validate(_payload(row)) for row in rows]


def _payload(row: tuple[object, ...]) -> dict:
    raw = row[0]
    return raw if isinstance(raw, dict) else json.loads(raw)  # type: ignore[arg-type]
