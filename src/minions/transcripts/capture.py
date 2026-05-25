"""Per-task transcript capture for multi-agent crews.

Single entrypoint: ``record_task(...)``. Called from the planning,
discoverer, and engineer crews after each task's LLM call returns.
Best-effort — a failure to persist the transcript or emit the activity
event never aborts the crew run (both writes wrapped in ``suppress``).
"""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from minions.activity import ActivityEntry, append
from minions.models.transcript import (
    MAX_MESSAGE_CHARS,
    PREVIEW_CHARS,
    CrewTranscriptMessage,
    RoleInConversation,
)

if TYPE_CHECKING:
    from minions.transcripts.store_factory import TranscriptStoreLike

logger = logging.getLogger(__name__)


def record_task(
    *,
    store: TranscriptStoreLike,
    run_id: str,
    project: str,
    crew: str,
    agent_role: str,
    agent_display_name: str | None,
    sequence: int,
    role_in_conversation: RoleInConversation,
    task_output: object,
    decision_id: str = "",
    activity_log_path: Path | None = None,
) -> CrewTranscriptMessage | None:
    """Persist a CrewAI task output as a transcript message + emit `agent_spoke`.

    ``task_output`` is the ``crewai.Task.output`` attribute (or any object
    with a ``.raw`` string attribute, with ``str()`` fallback). Returns
    None when no content could be extracted OR when capture is silenced
    via ``MINIONS_CREW_TRANSCRIPTS_DISABLED=1`` (escape hatch for tests
    or operator triage).
    """
    if _disabled():
        return None

    content = _extract_content(task_output)
    if not content:
        return None
    content = _redact(content)[:MAX_MESSAGE_CHARS]

    msg = CrewTranscriptMessage(
        run_id=run_id,
        project=project,
        crew=crew,
        agent_role=agent_role,
        agent_display_name=agent_display_name,
        sequence=sequence,
        role_in_conversation=role_in_conversation,
        content=content,
    )

    saved = False
    try:
        store.save(msg)
        saved = True
    except Exception:  # noqa: BLE001 — capture is best-effort
        logger.warning(
            "transcripts.capture: failed to persist %s/%s sequence=%d",
            crew,
            agent_role,
            sequence,
            exc_info=True,
        )

    with suppress(Exception):
        append(
            ActivityEntry(
                timestamp=msg.created_at,
                event="agent_spoke",
                run_id=run_id,
                crew=crew,
                project=project,
                decision_id=decision_id,
                agents=(agent_role,),
                extra={
                    "agent_role": agent_role,
                    "agent_display_name": agent_display_name,
                    "role_in_conversation": role_in_conversation,
                    "preview": content[:PREVIEW_CHARS],
                    "transcript_message_id": str(msg.id),
                    "sequence": sequence,
                },
            ),
            path=activity_log_path,
        )

    return msg if saved else None


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _extract_content(task_output: object) -> str | None:
    """Pull the text payload from a CrewAI Task.output (or string fallback)."""
    raw = getattr(task_output, "raw", None)
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    if isinstance(task_output, str) and task_output.strip():
        return task_output.strip()
    fallback = str(task_output).strip()
    return fallback or None


def _redact(text: str) -> str:
    """Run the spokesperson redaction surface so transcripts can't leak secrets.

    Fallback to the unredacted text if the redaction module isn't available
    (keeps capture functional for tests / partial installs).
    """
    try:
        from minions.spokesperson.redaction import redact_secrets

        return redact_secrets(text)
    except Exception:  # noqa: BLE001
        return text


def _disabled() -> bool:
    import os

    return os.environ.get("MINIONS_CREW_TRANSCRIPTS_DISABLED", "").lower() in {"1", "true", "yes"}


def record_task_default(
    *,
    run_id: str,
    project: str,
    crew: str,
    agent_role: str,
    agent_display_name: str | None,
    sequence: int,
    role_in_conversation: RoleInConversation,
    task_output: object,
    decision_id: str = "",
) -> CrewTranscriptMessage | None:
    """Convenience wrapper: resolves the store via the factory + delegates.

    For call-sites inside crews that don't already hold a store reference.
    Factory picks Postgres when ``MINIONS_DATABASE_URL`` resolves; falls
    back to JSON. All failures degrade silently so a path-resolution
    glitch can never crash a live crew run.
    """
    try:
        from minions.transcripts.store_factory import make_transcript_store

        default_path = (
            Path(__file__).resolve().parents[3] / "data" / "local" / "crew_transcripts.json"
        )
        store = make_transcript_store(default_path)
    except Exception:  # noqa: BLE001
        logger.warning(
            "transcripts.capture: could not build default store",
            exc_info=True,
        )
        return None
    return record_task(
        store=store,
        run_id=run_id,
        project=project,
        crew=crew,
        agent_role=agent_role,
        agent_display_name=agent_display_name,
        sequence=sequence,
        role_in_conversation=role_in_conversation,
        task_output=task_output,
        decision_id=decision_id,
    )


__all__ = ["record_task", "record_task_default"]
