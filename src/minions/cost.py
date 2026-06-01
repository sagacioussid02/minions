"""Per-call cost accounting middleware.

Captures one JSONL line per LLM call into ``data/local/cost_log.jsonl``:

  {"timestamp": "...", "project": "demo_five", "decision_id": "...",
   "role": "manager", "model": "claude-sonnet-4-6",
   "input_tokens": 1234, "output_tokens": 567, "cost_usd": 0.012}

Hooked into LiteLLM's ``success_callback`` (same path as Langfuse). Crews
call ``set_attribution(...)`` before kicking off so the callback knows
which project / role / decision the cost belongs to.

Phase 6 swap: replace JSONL with a Neon Postgres table. Aggregation API
stays identical.
"""

from __future__ import annotations

import contextvars
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_LOG_PATH = Path("data/local/cost_log.jsonl")

# Anthropic public pricing as of 2026-Q2 (USD per 1M tokens). Update when
# prices change. Unknown models cost 0 — we'd rather under-report than block.
PRICING: dict[str, dict[str, float]] = {
    "haiku-4.5": {"input": 1.00, "output": 5.00},
    "sonnet-4.6": {"input": 3.00, "output": 15.00},
    "opus-4.7": {"input": 15.00, "output": 75.00},
    # legacy / earlier tier ids — best-effort match
    "haiku": {"input": 1.00, "output": 5.00},
    "sonnet": {"input": 3.00, "output": 15.00},
    "opus": {"input": 15.00, "output": 75.00},
}


# ---------------------------------------------------------------------------
# Attribution context (set by crews before kickoff, read by the callback).
# ---------------------------------------------------------------------------

_attribution: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    "minions_cost_attribution", default=None
)
_log_path: contextvars.ContextVar[Path] = contextvars.ContextVar(
    "minions_cost_log_path", default=DEFAULT_LOG_PATH
)
# True after the first explicit ``set_log_path()`` — forces JSONL mode in
# tests and any code that pins a specific file.
_force_jsonl: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "minions_cost_force_jsonl", default=False
)


def _use_postgres() -> bool:
    """Decide whether to read/write the Postgres ``cost_log`` table.

    Honors:
      * ``set_log_path()`` having been called → False (test mode).
      * ``MINIONS_LOGS_BACKEND={postgres,json,auto}`` env override.
      * Default ``auto``: True iff a database URL resolves.
    """
    if _force_jsonl.get():
        return False
    import os as _os

    backend = (_os.environ.get("MINIONS_LOGS_BACKEND") or "auto").lower()
    if backend == "postgres":
        return True
    if backend == "json":
        return False
    from minions.db.connection import has_database_url

    return has_database_url()


def set_attribution(
    *, project: str, decision_id: str | None = None, role: str | None = None
) -> None:
    _attribution.set(
        {
            "project": project,
            "decision_id": str(decision_id or ""),
            "role": role or "",
        }
    )


def clear_attribution() -> None:
    _attribution.set(None)


def get_attribution() -> dict[str, str]:
    return _attribution.get() or {"project": "", "decision_id": "", "role": ""}


def set_log_path(path: Path, *, force_jsonl: bool = True) -> None:
    """Pin the JSONL fallback path.

    ``force_jsonl`` defaults True for test/explicit-path callers that want a
    deterministic file. The production CLI passes ``force_jsonl=False`` so a
    resolvable ``MINIONS_DATABASE_URL`` still routes writes to the Postgres
    ``cost_log`` — mirrors ``activity.set_log_path``. Without this, every
    cost write in CI/cron lands in an ephemeral runner file and the shared
    ledger stays empty.
    """
    _log_path.set(path)
    _force_jsonl.set(force_jsonl)


def get_log_path() -> Path:
    return _log_path.get()


# ---------------------------------------------------------------------------
# Pricing.
# ---------------------------------------------------------------------------


def resolve_tier(model: str) -> str | None:
    """Best-effort tier resolution from a LiteLLM-style model id.

    Examples that should resolve:
      * 'anthropic/claude-haiku-4-5'
      * 'claude-sonnet-4-6'
      * 'opus-4.7'
    """
    m = model.lower().replace("_", "-")
    for key in ("haiku", "sonnet", "opus"):
        if key in m:
            return key
    return None


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    tier = resolve_tier(model)
    if tier is None:
        return 0.0
    p = PRICING[tier]
    return (input_tokens * p["input"] + output_tokens * p["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Append-only JSONL log.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CostEntry:
    timestamp: datetime
    project: str
    decision_id: str
    role: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "project": self.project,
            "decision_id": self.decision_id,
            "role": self.role,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
        }


def append_entry(entry: CostEntry, *, path: Path | None = None) -> None:
    # Explicit path overrides everything → JSONL mode.
    if path is None and _use_postgres():
        try:
            _pg_append(entry)
            return
        except Exception as e:  # noqa: BLE001 — observability never crashes work
            logger.debug("cost.append_entry pg failed, falling back to JSONL: %s", e)
    p = path or get_log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(entry.to_dict()) + "\n")


def read_log(path: Path | None = None) -> list[CostEntry]:
    if path is None and _use_postgres():
        try:
            return _pg_read_log()
        except Exception as e:  # noqa: BLE001
            logger.debug("cost.read_log pg failed, falling back to JSONL: %s", e)
    p = path or get_log_path()
    if not p.exists():
        return []
    out: list[CostEntry] = []
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(
                CostEntry(
                    timestamp=datetime.fromisoformat(d["timestamp"]),
                    project=d.get("project", ""),
                    decision_id=d.get("decision_id", ""),
                    role=d.get("role", ""),
                    model=d.get("model", ""),
                    input_tokens=int(d.get("input_tokens", 0)),
                    output_tokens=int(d.get("output_tokens", 0)),
                    cost_usd=float(d.get("cost_usd", 0.0)),
                )
            )
        except (ValueError, KeyError):
            continue  # skip malformed lines, never crash the cron
    return out


def _pg_append(entry: CostEntry) -> None:
    from minions.db.connection import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO cost_log (
                ts, project, role, decision_id, model,
                in_tokens, out_tokens, cost_usd, payload
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                entry.timestamp,
                entry.project or None,
                entry.role or None,
                entry.decision_id or None,
                entry.model,
                entry.input_tokens,
                entry.output_tokens,
                round(entry.cost_usd, 6),
                json.dumps(entry.to_dict()),
            ),
        )


def _pg_read_log() -> list[CostEntry]:
    from minions.db.connection import connect

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT ts, project, role, decision_id, model, "
            "in_tokens, out_tokens, cost_usd FROM cost_log ORDER BY ts ASC"
        )
        rows = cur.fetchall()
    return [
        CostEntry(
            timestamp=row[0],
            project=row[1] or "",
            role=row[2] or "",
            decision_id=row[3] or "",
            model=row[4],
            input_tokens=int(row[5] or 0),
            output_tokens=int(row[6] or 0),
            cost_usd=float(row[7] or 0.0),
        )
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Aggregation.
# ---------------------------------------------------------------------------


def cost_by_project(*, since: datetime | None = None, path: Path | None = None) -> dict[str, float]:
    """Sum costs per project, optionally filtered to entries on/after ``since``."""
    out: dict[str, float] = {}
    for e in read_log(path):
        if since is not None and e.timestamp < since:
            continue
        if not e.project:
            continue
        out[e.project] = out.get(e.project, 0.0) + e.cost_usd
    return out


def month_to_date_cost(
    project: str, *, now: datetime | None = None, path: Path | None = None
) -> float:
    now = now or datetime.now(tz=UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return cost_by_project(since=month_start, path=path).get(project, 0.0)


def week_to_date_cost(
    project: str, *, now: datetime | None = None, path: Path | None = None
) -> float:
    """Cost since the most recent Monday 00:00 UTC."""
    from datetime import timedelta

    now = now or datetime.now(tz=UTC)
    week_start = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return cost_by_project(since=week_start, path=path).get(project, 0.0)


def cost_by_surface(*, since: datetime | None = None) -> dict[str, float]:
    """Sum costs grouped by ``payload->>'surface'``. Postgres-only.

    Rows without a surface tag (the historical default — crew runs from
    LiteLLM) bucket as ``"crew"``. New surfaces (e.g. ``agent_chat`` from
    the public-console Surface B route) appear as separate rows. Returns
    an empty dict when the cost ledger is JSONL (test mode / no DB), so
    callers can degrade gracefully.
    """
    if not _use_postgres():
        return {}
    from minions.db.connection import connect

    if since is None:
        since = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE(payload->>'surface', 'crew') AS surface,
                   COALESCE(SUM(cost_usd), 0)::float8 AS total
            FROM cost_log
            WHERE ts >= %s
            GROUP BY surface
            """,
            (since,),
        )
        rows = cur.fetchall()
    return {str(row[0]): float(row[1] or 0.0) for row in rows}


# ---------------------------------------------------------------------------
# LiteLLM callback registration.
# ---------------------------------------------------------------------------


def _litellm_cost_callback(
    kwargs: dict[str, Any],
    completion_response: Any,
    start_time: Any,
    end_time: Any,
) -> None:
    """LiteLLM ``success_callback`` — records one CostEntry per LLM call."""
    try:
        model = (kwargs.get("model") or "") if isinstance(kwargs, dict) else ""
        usage = getattr(completion_response, "usage", None)
        if usage is None:
            return
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        cost = estimate_cost_usd(model, input_tokens, output_tokens)
        attr = get_attribution()
        entry = CostEntry(
            timestamp=datetime.now(tz=UTC),
            project=attr["project"],
            decision_id=attr["decision_id"],
            role=attr["role"],
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
        append_entry(entry)
    except Exception as e:  # noqa: BLE001 — observability must never crash work
        logger.debug("cost callback failed: %s", e)


def record_llm_call(*, model: str, usage: Any, role: str | None = None) -> None:
    """Record one CostEntry from an LLM call's token usage.

    ``usage`` is a mapping (or any object exposing the keys) with token
    counts; both LiteLLM (``prompt_tokens`` / ``completion_tokens``) and
    Anthropic-native (``input_tokens`` / ``output_tokens``) namings are
    accepted. Attribution falls back to the supplied ``role`` (e.g. the
    event's ``agent_role``) when no crew attribution is set.
    """

    def _tok(*keys: str) -> int:
        for k in keys:
            v = usage.get(k) if isinstance(usage, dict) else getattr(usage, k, None)
            if v:
                return int(v)
        return 0

    input_tokens = _tok("prompt_tokens", "input_tokens")
    output_tokens = _tok("completion_tokens", "output_tokens")
    if input_tokens == 0 and output_tokens == 0:
        return
    attr = get_attribution()
    append_entry(
        CostEntry(
            timestamp=datetime.now(tz=UTC),
            project=attr["project"],
            decision_id=attr["decision_id"],
            role=attr["role"] or (role or ""),
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=estimate_cost_usd(model, input_tokens, output_tokens),
        )
    )


# CrewAI ≥1.x manages LiteLLM's callback list itself (stripping function
# callbacks, overwriting ``litellm.callbacks``) and routes native providers
# off LiteLLM entirely — so the ``success_callback`` hook never fires for crew
# runs. The event bus is the reliable surface: both the LiteLLM and native
# paths emit ``LLMCallCompletedEvent`` with per-call model + usage.
_crewai_listener_registered = False


def _on_crewai_llm_completed(source: Any, event: Any) -> None:  # noqa: ARG001
    """Bus handler for ``LLMCallCompletedEvent`` — records one CostEntry."""
    try:
        if getattr(event, "usage", None) is None or not getattr(event, "model", None):
            return
        record_llm_call(
            model=event.model,
            usage=event.usage,
            role=getattr(event, "agent_role", None),
        )
    except Exception as e:  # noqa: BLE001 — observability must never crash work
        logger.debug("crewai cost listener failed: %s", e)


def _register_crewai_cost_listener(*, verbose: bool = False) -> bool:
    global _crewai_listener_registered
    if _crewai_listener_registered:
        return True
    try:
        from crewai.events.event_bus import crewai_event_bus
        from crewai.events.types.llm_events import LLMCallCompletedEvent
    except Exception:  # noqa: BLE001 — crewai missing/older: skip silently
        return False

    crewai_event_bus.on(LLMCallCompletedEvent)(_on_crewai_llm_completed)
    _crewai_listener_registered = True
    if verbose:
        logger.info("crewai cost event listener registered")
    return True


def init_cost_tracking(
    *, log_path: Path | None = None, force_jsonl: bool = True, verbose: bool = False
) -> bool:
    """Register the cost hooks. Idempotent.

    Registers both the CrewAI event-bus listener (the path that actually fires
    for crew LLM calls) and the legacy LiteLLM ``success_callback`` (kept as a
    fallback for any direct LiteLLM use).

    ``force_jsonl`` is forwarded to :func:`set_log_path`. The production CLI
    passes ``force_jsonl=False`` so cost writes reach the Postgres ledger
    when a database URL resolves; tests keep the default to stay on JSONL.

    Returns True if at least one hook was registered.
    """
    if log_path is not None:
        set_log_path(log_path, force_jsonl=force_jsonl)

    crew_ok = _register_crewai_cost_listener(verbose=verbose)

    litellm_ok = False
    try:
        import litellm  # type: ignore[import-not-found]

        callbacks = list(litellm.success_callback or [])
        if _litellm_cost_callback not in callbacks:
            callbacks.append(_litellm_cost_callback)
            litellm.success_callback = callbacks
        litellm_ok = True
    except ImportError:
        if verbose:
            logger.warning("litellm not installed; relying on the crewai event listener")

    if verbose and (crew_ok or litellm_ok):
        logger.info("cost tracking enabled (log: %s)", get_log_path())
    return crew_ok or litellm_ok
