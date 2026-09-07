"""Activity event log — append-only stream of crew-level lifecycle events.

Distinct from ``cost.py`` (one entry per LLM call) — this records the higher-
level *what's running right now* signal for the dashboard. Schema kept
deliberately tight in v0; extend cautiously.

  {"timestamp": "...", "event": "crew_started",
   "crew": "planning", "project": "demo_five",
   "decision_id": "abc-123", "agents": ["product_owner", "manager"]}

A "running right now" agent is one that appears in a ``crew_started`` event
within the last ``RUNNING_WINDOW_SECONDS`` whose matching ``crew_finished``
hasn't arrived yet.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path("data/local/activity.jsonl")

# Module-level override so the CLI can point activity logging at the repo's
# data dir without threading the path through every call site.
_log_path_override: Path | None = None
# Set to True after explicit set_log_path() — forces JSONL mode (tests).
_force_jsonl: bool = False


def set_log_path(path: Path, *, force_jsonl: bool = True) -> None:
    global _log_path_override, _force_jsonl
    _log_path_override = path
    _force_jsonl = force_jsonl


def get_log_path() -> Path:
    return _log_path_override or DEFAULT_LOG_PATH


def _use_postgres() -> bool:
    if _force_jsonl:
        return False
    import os as _os

    backend = (_os.environ.get("MINIONS_LOGS_BACKEND") or "auto").lower()
    if backend == "postgres":
        return True
    if backend == "json":
        return False
    from minions.db.connection import has_database_url

    return has_database_url()


# A crew_started event "expires" after this many seconds without a
# matching crew_finished. Crews that hang/crash beyond this don't keep
# painting the dashboard yellow forever.
RUNNING_WINDOW_SECONDS = 10 * 60  # 10 minutes


Event = Literal[
    "crew_started",
    "crew_finished",
    "crew_failed",
    "crew_checkin",
    "scrum_created",
    "sprint_planned",
    "monthly_demo_ready",
    "pm_answered",
    "spokesperson_answered",
    "agent_spoke",  # per-task transcript message (see crew-transcripts)
    "guardrail_blocked",  # safety layer refused an unsafe action
]


# Which safety layer emitted the block (Layer 1 = prompt-level refusals,
# Layer 2 = tool / GitHub client refusals). Layers 3 + 4 (branch protection,
# network egress allowlist) are enforced outside this codebase.
GuardrailLayer = Literal["layer1_prompt", "layer2_tooling"]


@dataclass(frozen=True)
class ActivityEntry:
    timestamp: datetime
    event: Event
    run_id: str  # links a started event to its finished/failed counterpart
    crew: str
    project: str
    decision_id: str
    agents: tuple[str, ...]
    error: str | None = None
    # Set for tenant-project runs (from Manifest.tenant_id); None means the
    # founder — the activity_log.tenant_id column DEFAULT already resolves
    # that case, so we simply omit the column from the INSERT.
    tenant_id: str | None = None
    # Free-form per-event context that round-trips through ``payload`` so the
    # UI feed can render real content (e.g. scrum summary + blockers) instead
    # of a generic "shared a daily scrum update" placeholder. Keep small —
    # truncate long fields at the call-site.
    extra: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "timestamp": self.timestamp.isoformat(),
            "event": self.event,
            "run_id": self.run_id,
            "crew": self.crew,
            "project": self.project,
            "decision_id": self.decision_id,
            "agents": list(self.agents),
        }
        if self.error is not None:
            d["error"] = self.error
        if self.extra:
            # Merge into the top-level dict so the UI's `event.payload[<key>]`
            # access pattern stays unchanged.
            for k, v in self.extra.items():
                if k not in d:  # never let extra clobber a core field
                    d[k] = v
        return d


def _log_path(path: Path | None) -> Path:
    return path or get_log_path()


def append(entry: ActivityEntry, *, path: Path | None = None) -> None:
    if path is None and _use_postgres():
        try:
            _pg_append(entry)
            return
        except Exception as e:  # noqa: BLE001 — observability never crashes work
            logger.debug("activity.append pg failed, falling back to JSONL: %s", e)
    p = _log_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(entry.to_dict()) + "\n")


def read_log(path: Path | None = None) -> list[ActivityEntry]:
    if path is None and _use_postgres():
        try:
            return _pg_read_log()
        except Exception as e:  # noqa: BLE001
            logger.debug("activity.read_log pg failed, falling back to JSONL: %s", e)
    p = _log_path(path)
    if not p.exists():
        return []
    out: list[ActivityEntry] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(
                ActivityEntry(
                    timestamp=datetime.fromisoformat(d["timestamp"]),
                    event=d["event"],
                    run_id=d.get("run_id", ""),
                    crew=d.get("crew", ""),
                    project=d.get("project", ""),
                    decision_id=d.get("decision_id", ""),
                    agents=tuple(d.get("agents", [])),
                    error=d.get("error"),
                )
            )
        except (ValueError, KeyError):
            continue
    return out


def _pg_append(entry: ActivityEntry) -> None:
    from minions.db.connection import connect

    with connect() as conn, conn.cursor() as cur:
        if entry.tenant_id is not None:
            cur.execute(
                """
                INSERT INTO activity_log (
                    ts, event, project, decision_id, crew, run_id, error, payload, tenant_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    entry.timestamp,
                    entry.event,
                    entry.project or None,
                    entry.decision_id or None,
                    entry.crew or None,
                    entry.run_id or None,
                    entry.error,
                    json.dumps(entry.to_dict()),
                    entry.tenant_id,
                ),
            )
            return
        cur.execute(
            """
            INSERT INTO activity_log (
                ts, event, project, decision_id, crew, run_id, error, payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                entry.timestamp,
                entry.event,
                entry.project or None,
                entry.decision_id or None,
                entry.crew or None,
                entry.run_id or None,
                entry.error,
                json.dumps(entry.to_dict()),
            ),
        )


def _pg_read_log() -> list[ActivityEntry]:
    from minions.db.connection import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ts, event, run_id, crew, project, decision_id, payload, error "
            "FROM activity_log ORDER BY ts ASC"
        )
        rows = cur.fetchall()
    out: list[ActivityEntry] = []
    for row in rows:
        ts, event, run_id, crew, project, decision_id, payload, error = row
        # Pull agents from payload (column-less by design).
        agents: tuple[str, ...] = ()
        if payload:
            d = payload if isinstance(payload, dict) else json.loads(payload)
            agents = tuple(d.get("agents", []))
        out.append(
            ActivityEntry(
                timestamp=ts,
                event=event,
                run_id=run_id or "",
                crew=crew or "",
                project=project or "",
                decision_id=decision_id or "",
                agents=agents,
                error=error,
            )
        )
    return out


def running_now(
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> list[ActivityEntry]:
    """Return the set of currently-running crew_started events.

    A run is "running" if it has a crew_started entry within the running
    window AND no crew_finished/crew_failed with the same run_id has arrived.
    """
    now = now or datetime.now(tz=UTC)
    cutoff = now.timestamp() - RUNNING_WINDOW_SECONDS
    starts: dict[str, ActivityEntry] = {}
    closed: set[str] = set()
    for e in read_log(path):
        if e.timestamp.timestamp() < cutoff and e.event == "crew_started":
            continue  # expired start
        if e.event == "crew_started":
            starts[e.run_id] = e
        elif e.event in ("crew_finished", "crew_failed"):
            closed.add(e.run_id)
    return [s for run_id, s in starts.items() if run_id not in closed]


def is_role_running(
    project: str,
    role: str,
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """True if any in-flight crew involves this (project, role)."""
    return any(e.project == project and role in e.agents for e in running_now(path=path, now=now))


def record_guardrail_block(
    *,
    layer: GuardrailLayer,
    kind: str,
    details: str,
    project: str | None = None,
    role: str | None = None,
    path: Path | None = None,
) -> None:
    """Emit a ``guardrail_blocked`` event when a safety layer refuses an action.

    Never raises — observability code must not crash the safety check that
    produced the block.
    """
    try:
        entry = ActivityEntry(
            timestamp=datetime.now(tz=UTC),
            event="guardrail_blocked",
            run_id=uuid.uuid4().hex,
            crew=f"guardrail:{layer}",
            project=project or "",
            decision_id="",
            agents=(kind,) if role is None else (kind, role),
            error=details[:200],
        )
        append(entry, path=path)
    except Exception:  # noqa: BLE001
        logger.debug("activity.record_guardrail_block failed", exc_info=True)


def guardrail_blocks(
    *,
    since: datetime | None = None,
    path: Path | None = None,
) -> list[ActivityEntry]:
    """Return ``guardrail_blocked`` events, newest first, optionally filtered by since."""
    out = [e for e in read_log(path) if e.event == "guardrail_blocked"]
    if since is not None:
        out = [e for e in out if e.timestamp >= since]
    out.sort(key=lambda e: e.timestamp, reverse=True)
    return out


def history_for_role(
    project: str,
    role: str,
    *,
    limit: int = 20,
    path: Path | None = None,
) -> list[ActivityEntry]:
    """Recent crew lifecycle entries that mention this (project, role).

    Used by the agent-detail dialog. Returns most-recent first.
    """
    matches = [e for e in read_log(path) if e.project == project and role in e.agents]
    matches.sort(key=lambda e: e.timestamp, reverse=True)
    return matches[:limit]


@contextmanager
def crew_run(
    *,
    crew: str,
    project: str,
    agents: list[str],
    decision_id: str | None = None,
    path: Path | None = None,
) -> Iterator[str]:
    """Context manager that brackets a crew run with start/finish events.

    Yields the run_id so the caller can correlate with logs / decision
    records. ``crew_failed`` is emitted with the exception message if the
    block raises; the exception is then re-raised.

    Usage:
        with crew_run(crew="planning", project="p", agents=["manager"]) as run_id:
            ...
    """
    run_id = uuid.uuid4().hex
    start = ActivityEntry(
        timestamp=datetime.now(tz=UTC),
        event="crew_started",
        run_id=run_id,
        crew=crew,
        project=project,
        decision_id=str(decision_id or ""),
        agents=tuple(agents),
    )
    try:
        append(start, path=path)
    except Exception as e:  # noqa: BLE001 — observability never crashes work
        logger.debug("activity.append start failed: %s", e)

    try:
        yield run_id
    except Exception as e:
        try:
            append(
                ActivityEntry(
                    timestamp=datetime.now(tz=UTC),
                    event="crew_failed",
                    run_id=run_id,
                    crew=crew,
                    project=project,
                    decision_id=str(decision_id or ""),
                    agents=tuple(agents),
                    error=str(e)[:200],
                ),
                path=path,
            )
        except Exception as inner:  # noqa: BLE001
            logger.debug("activity.append fail failed: %s", inner)
        raise
    else:
        try:
            append(
                ActivityEntry(
                    timestamp=datetime.now(tz=UTC),
                    event="crew_finished",
                    run_id=run_id,
                    crew=crew,
                    project=project,
                    decision_id=str(decision_id or ""),
                    agents=tuple(agents),
                ),
                path=path,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("activity.append finish failed: %s", e)
