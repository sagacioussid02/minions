"""Postgres-backed EngineerRunStore. Drop-in for ``EngineerRunStore``."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunRecord
from minions.db.connection import connect


class PostgresEngineerRunStore:
    """Backed by the ``engineer_runs`` table, keyed by decision_id."""

    def _upsert(self, record: EngineerRunRecord) -> EngineerRunRecord:
        payload = record.model_dump(mode="json")
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO engineer_runs (
                    decision_id, project, pr_url, pr_state, completed_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (decision_id) DO UPDATE SET
                    project = EXCLUDED.project,
                    pr_url = EXCLUDED.pr_url,
                    pr_state = EXCLUDED.pr_state,
                    completed_at = EXCLUDED.completed_at,
                    payload = EXCLUDED.payload
                """,
                (
                    record.decision_id,
                    record.project,
                    record.pr_url,
                    record.pr_state,
                    record.completed_at,
                    Jsonb(payload),
                ),
            )
        return record

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
        return self._upsert(record)

    def get(self, decision_id: str) -> EngineerRunRecord | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM engineer_runs WHERE decision_id = %s",
                (decision_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return EngineerRunRecord.model_validate(
            row[0] if isinstance(row[0], dict) else json.loads(row[0])
        )

    def list_all(self) -> list[EngineerRunRecord]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM engineer_runs ORDER BY completed_at DESC")
            rows = cur.fetchall()
        return [
            EngineerRunRecord.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    def list_by_project(self, project: str) -> list[EngineerRunRecord]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM engineer_runs WHERE project = %s ORDER BY completed_at DESC",
                (project,),
            )
            rows = cur.fetchall()
        return [
            EngineerRunRecord.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    def update(self, record: EngineerRunRecord) -> EngineerRunRecord:
        return self._upsert(record)
