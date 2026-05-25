"""Postgres-backed AuditFindingStore. Drop-in for ``AuditFindingStore``."""

from __future__ import annotations

import json

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.audit import AuditFinding, FindingStatus


class PostgresAuditFindingStore:
    """Backed by the ``audit_findings`` table."""

    def save(self, finding: AuditFinding) -> AuditFinding:
        payload = json.loads(finding.model_dump_json())
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_findings (
                    id, source_project, source_pr_url, source_decision_id,
                    category, severity, status, created_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    source_project = EXCLUDED.source_project,
                    source_pr_url = EXCLUDED.source_pr_url,
                    source_decision_id = EXCLUDED.source_decision_id,
                    category = EXCLUDED.category,
                    severity = EXCLUDED.severity,
                    status = EXCLUDED.status,
                    payload = EXCLUDED.payload
                """,
                (
                    str(finding.id),
                    finding.source_project,
                    finding.source_pr_url,
                    str(finding.source_decision_id) if finding.source_decision_id else None,
                    getattr(finding.category, "value", str(finding.category)),
                    getattr(finding.severity, "value", str(finding.severity)),
                    finding.status.value,
                    finding.created_at,
                    Jsonb(payload),
                ),
            )
        return finding

    def get(self, finding_id: str) -> AuditFinding | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM audit_findings WHERE id = %s",
                (str(finding_id),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return AuditFinding.model_validate(
            row[0] if isinstance(row[0], dict) else json.loads(row[0])
        )

    def list_all(self) -> list[AuditFinding]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM audit_findings ORDER BY created_at DESC")
            rows = cur.fetchall()
        return [
            AuditFinding.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    def list_open(self) -> list[AuditFinding]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM audit_findings WHERE status = %s ORDER BY created_at DESC",
                (FindingStatus.OPEN.value,),
            )
            rows = cur.fetchall()
        return [
            AuditFinding.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    def list_by_pr_url(self, pr_url: str) -> list[AuditFinding]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM audit_findings WHERE source_pr_url = %s "
                "ORDER BY created_at DESC",
                (pr_url,),
            )
            rows = cur.fetchall()
        return [
            AuditFinding.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    def has_finding_for_pr(self, pr_url: str) -> bool:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM audit_findings WHERE source_pr_url = %s LIMIT 1",
                (pr_url,),
            )
            return cur.fetchone() is not None
