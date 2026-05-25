"""Postgres-backed deployment store (mirrors ``dossiers/store_postgres.py``)."""

from __future__ import annotations

import json

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.deployment import DeploymentRecord, DeploymentStatus


class PostgresDeploymentStore:
    """Backed by the ``deployments`` table (migration 0009)."""

    def save(self, record: DeploymentRecord) -> DeploymentRecord:
        payload = json.loads(record.model_dump_json())
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO deployments (
                    id, project, merge_sha, status, deploy_target,
                    pr_number, detected_at, verified_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    status = EXCLUDED.status,
                    verified_at = EXCLUDED.verified_at,
                    payload = EXCLUDED.payload
                """,
                (
                    str(record.id),
                    record.project,
                    record.merge_sha,
                    record.status.value,
                    record.deploy_target,
                    record.pr_number,
                    record.detected_at,
                    record.verified_at,
                    Jsonb(payload),
                ),
            )
        return record

    def get(self, record_id: str) -> DeploymentRecord | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM deployments WHERE id = %s",
                (str(record_id),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return DeploymentRecord.model_validate(
            row[0] if isinstance(row[0], dict) else json.loads(row[0])
        )

    def list_all(self) -> list[DeploymentRecord]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM deployments ORDER BY detected_at DESC LIMIT 1000")
            rows = cur.fetchall()
        return [
            DeploymentRecord.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    def find_by_sha(self, project: str, merge_sha: str) -> DeploymentRecord | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM deployments "
                "WHERE project = %s AND merge_sha = %s "
                "ORDER BY detected_at DESC LIMIT 1",
                (project, merge_sha),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return DeploymentRecord.model_validate(
            row[0] if isinstance(row[0], dict) else json.loads(row[0])
        )

    def list_for_project(
        self,
        project: str,
        status: DeploymentStatus | None = None,
        limit: int = 100,
    ) -> list[DeploymentRecord]:
        with connect() as conn, conn.cursor() as cur:
            if status is None:
                cur.execute(
                    "SELECT payload FROM deployments WHERE project = %s "
                    "ORDER BY detected_at DESC LIMIT %s",
                    (project, limit),
                )
            else:
                cur.execute(
                    "SELECT payload FROM deployments "
                    "WHERE project = %s AND status = %s "
                    "ORDER BY detected_at DESC LIMIT %s",
                    (project, status.value, limit),
                )
            rows = cur.fetchall()
        return [
            DeploymentRecord.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]
