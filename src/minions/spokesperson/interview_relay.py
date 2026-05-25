"""Relay engineer-crew SPIKE findings back into the Leadership Room thread.

When the spokesperson opens a SPIKE Decision in response to a low-confidence
question (e.g. CTO asks "where is demo_four deployed?" and there is no stored
evidence), the engineer crew investigates and opens a PR with the answer.
Without this module, the answer is invisible in the chat — the operator
has to find the PR by hand.

This module closes the loop:

  1. Read the raw ``decisions.payload`` JSON (TS writes ``spike_source``,
     ``thread_id``, ``question`` — fields not on the Python ``Decision``
     pydantic). Bypass ``Decision.model_validate`` on purpose.
  2. Compose a one-message answer from the engineer's result (PR title, PR
     URL, files inspected) plus a confidence note.
  3. INSERT a new row into ``interview_messages`` so the next time the
     operator opens the Leadership Room, the answer is sitting in the
     thread like any other reply.

Strictly best-effort: failures here MUST NOT block the engineer crew's
status transition. Logged and swallowed.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from minions.crews.engineer import EngineerResult
from minions.db.connection import has_database_url

logger = logging.getLogger(__name__)


def relay_spike_answer(
    *,
    decision_id: str,
    project: str,
    engineer_result: EngineerResult,
) -> str | None:
    """Insert the SPIKE answer into ``interview_messages``, return new id.

    Returns ``None`` if the Decision is not a spokesperson SPIKE, the
    thread cannot be located, the database is unreachable, or the insert
    fails. All paths are no-ops on the caller's side.
    """
    if not has_database_url():
        logger.debug("interview_relay: no database URL — skipping relay")
        return None

    try:
        payload = _load_decision_payload(decision_id)
    except Exception as e:  # noqa: BLE001
        logger.debug("interview_relay: load_decision_payload failed: %s", e)
        return None

    if payload is None:
        return None
    if payload.get("spike_source") != "spokesperson_interview":
        return None

    thread_id = payload.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        # TS didn't stash thread_id (pre-relay version of createSpikeDecision).
        # Best-effort fallback: scan interview_messages for an operator message
        # whose content matches this SPIKE's recorded question text. Used by
        # the backfill CLI to retroactively relay answers for legacy SPIKEs.
        question = payload.get("question")
        if isinstance(question, str) and question.strip():
            thread_id = _find_thread_for_question(question)
        if not thread_id:
            logger.debug(
                "interview_relay: decision %s has no thread_id and "
                "no matching prior operator message",
                decision_id[:8],
            )
            return None
        logger.info(
            "interview_relay: decision %s — inferred thread %s from matching operator question",
            decision_id[:8],
            thread_id[:8],
        )

    answer_text = _compose_answer(
        question=str(payload.get("question") or ""),
        owner_role=str(payload.get("proposer_role") or "engineer"),
        project=project,
        result=engineer_result,
    )
    if not answer_text:
        return None

    message_id = str(uuid.uuid4())
    spokesperson_role = str(payload.get("requested_by_role") or "spokesperson")
    answer_message = {
        "id": message_id,
        "thread_id": thread_id,
        "role": "spokesperson",
        "agent_role": str(payload.get("proposer_role") or "engineer"),
        "content": answer_text,
        "citations": _citations_from_result(engineer_result),
        "consulted_roles": list(payload.get("consulted_roles") or []),
        "confidence": _confidence_label(engineer_result),
        "follow_up_actions": [],
        "task_proposal_id": None,
        "spike_decision_id": decision_id,
        "pr_url": engineer_result.pr_url,
        "created_at": datetime.now(tz=UTC).isoformat(),
    }

    try:
        _insert_message_and_touch_thread(
            message_id=message_id,
            thread_id=thread_id,
            agent_role=str(payload.get("proposer_role") or "engineer"),
            payload=answer_message,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "interview_relay: insert failed for decision %s thread %s: %s",
            decision_id[:8],
            thread_id[:8],
            e,
        )
        return None

    logger.info(
        "interview_relay: relayed answer for decision %s into thread %s "
        "(spokesperson=%s, message=%s)",
        decision_id[:8],
        thread_id[:8],
        spokesperson_role,
        message_id[:8],
    )
    return message_id


# ---- Internals ------------------------------------------------------------


def _load_decision_payload(decision_id: str) -> dict[str, Any] | None:
    from minions.db.connection import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payload FROM decisions WHERE id = %s::uuid",
            (decision_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    raw = row[0]
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
    return None


def _find_thread_for_question(question: str) -> str | None:
    """Look up thread_id by matching an operator message content (legacy backfill).

    Returns the most recent thread that has an operator message whose
    ``payload.content`` equals the SPIKE's recorded ``question``. Used by
    ``spokesperson-backfill`` for SPIKEs created before the TS-side change
    that stashes ``thread_id`` directly in the Decision payload.
    """
    from minions.db.connection import connect

    try:
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT thread_id::text
                FROM interview_messages
                WHERE role = 'operator'
                  AND payload->>'content' = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (question,),
            )
            row = cur.fetchone()
    except Exception as e:  # noqa: BLE001
        logger.debug("interview_relay: _find_thread_for_question failed: %s", e)
        return None
    if row is None:
        return None
    return str(row[0]) if row[0] else None


def _insert_message_and_touch_thread(
    *,
    message_id: str,
    thread_id: str,
    agent_role: str,
    payload: dict[str, Any],
) -> None:
    from psycopg.types.json import Jsonb

    from minions.db.connection import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO interview_messages
                (id, thread_id, role, agent_role, created_at, payload)
            VALUES (%s::uuid, %s::uuid, 'spokesperson', %s, NOW(), %s)
            """,
            (message_id, thread_id, agent_role, Jsonb(payload)),
        )
        cur.execute(
            "UPDATE interview_threads SET updated_at = NOW() WHERE id = %s::uuid",
            (thread_id,),
        )


def _compose_answer(
    *,
    question: str,
    owner_role: str,
    project: str,
    result: EngineerResult,
) -> str:
    """One-paragraph reply suitable for the chat thread."""
    role_pretty = owner_role.replace("_", " ").title()

    if result.skipped:
        return (
            f"{role_pretty} reporting back on the investigation into "
            f'"{question}".\n\nThe investigation was skipped: '
            f"{result.skip_reason or 'unknown reason'}. The question is "
            "still open — re-ask or escalate."
        )

    if not result.pr_url:
        return (
            f"{role_pretty} reporting back on the investigation into "
            f'"{question}". The engineer crew finished but did not open a '
            "PR with the findings."
        )

    files_line = ""
    if result.files_changed:
        files_preview = ", ".join(result.files_changed[:6])
        suffix = (
            "" if len(result.files_changed) <= 6 else f" (+{len(result.files_changed) - 6} more)"
        )
        files_line = f"\n\nFiles inspected: {files_preview}{suffix}."

    confidence_line = ""
    if result.files_rejected:
        confidence_line = (
            f"\n\nNote: {len(result.files_rejected)} proposed change(s) were "
            "rejected by the safety layer; the report may be incomplete."
        )

    return (
        f"{role_pretty} reporting back on the investigation into "
        f'"{question}".\n\nFindings posted in {result.pr_url}.'
        f"{files_line}{confidence_line}\n\nOpen the PR for the full discovery."
    )


def _citations_from_result(result: EngineerResult) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    if result.pr_url:
        citations.append(
            {
                "source_type": "pr",
                "label": _pr_label(result.pr_url),
                "reference": result.pr_url,
                "excerpt": "Investigation PR (engineer crew findings).",
            }
        )
    for f in result.files_changed[:8]:
        citations.append(
            {
                "source_type": "code_scan",
                "label": f,
                "reference": f,
                "excerpt": f"Inspected during the SPIKE: {f}",
            }
        )
    return citations


def _pr_label(pr_url: str) -> str:
    import re

    m = re.search(r"/pull/(\d+)", pr_url)
    return f"PR #{m.group(1)}" if m else "PR"


def _confidence_label(result: EngineerResult) -> str:
    if result.skipped or not result.pr_url:
        return "low"
    if result.files_rejected:
        return "medium"
    return "medium"  # human still needs to read the PR; never auto-claim "high"
