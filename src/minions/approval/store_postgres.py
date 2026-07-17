"""Postgres-backed Decision Store. Same interface as ``DecisionStore``."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

from psycopg.types.json import Jsonb

from minions.db.connection import connect
from minions.models.decision import Decision, DecisionStatus


class PostgresDecisionStore:
    """Backed by the ``decisions`` table. Drop-in for ``DecisionStore``."""

    def save(self, decision: Decision) -> None:
        payload = decision.model_dump(mode="json")
        structured_plan_jsonb = (
            Jsonb(decision.structured_plan.model_dump(mode="json"))
            if decision.structured_plan is not None
            else None
        )
        with connect() as conn, conn.cursor() as cur:
            if decision.tenant_id is not None:
                cur.execute(
                    """
                    INSERT INTO decisions (
                        id, project, status, type, risk, created_at, resolved_at,
                        sprint_number, structured_plan, payload, tenant_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        project = EXCLUDED.project,
                        status = EXCLUDED.status,
                        type = EXCLUDED.type,
                        risk = EXCLUDED.risk,
                        resolved_at = EXCLUDED.resolved_at,
                        sprint_number = EXCLUDED.sprint_number,
                        structured_plan = EXCLUDED.structured_plan,
                        payload = EXCLUDED.payload
                    """,
                    (
                        str(decision.id),
                        decision.project,
                        decision.status.value,
                        decision.type.value,
                        decision.risk,
                        decision.created_at,
                        decision.resolved_at,
                        decision.sprint_number,
                        structured_plan_jsonb,
                        Jsonb(payload),
                        decision.tenant_id,
                    ),
                )
                return
            cur.execute(
                """
                INSERT INTO decisions (
                    id, project, status, type, risk, created_at, resolved_at,
                    sprint_number, structured_plan, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    project = EXCLUDED.project,
                    status = EXCLUDED.status,
                    type = EXCLUDED.type,
                    risk = EXCLUDED.risk,
                    resolved_at = EXCLUDED.resolved_at,
                    sprint_number = EXCLUDED.sprint_number,
                    structured_plan = EXCLUDED.structured_plan,
                    payload = EXCLUDED.payload
                """,
                (
                    str(decision.id),
                    decision.project,
                    decision.status.value,
                    decision.type.value,
                    decision.risk,
                    decision.created_at,
                    decision.resolved_at,
                    decision.sprint_number,
                    structured_plan_jsonb,
                    Jsonb(payload),
                ),
            )

    def get(self, decision_id: UUID | str) -> Decision | None:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM decisions WHERE id = %s",
                (str(decision_id),),
            )
            row = cur.fetchone()
        if row is None:
            return None
        payload = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        return Decision.model_validate(payload)

    def list_all(self) -> list[Decision]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM decisions ORDER BY created_at DESC")
            rows = cur.fetchall()
        return [
            Decision.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

    def list_by_status(self, status: DecisionStatus) -> list[Decision]:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT payload FROM decisions WHERE status = %s ORDER BY created_at DESC",
                (status.value,),
            )
            rows = cur.fetchall()
        return [
            Decision.model_validate(r[0] if isinstance(r[0], dict) else json.loads(r[0]))
            for r in rows
        ]

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
