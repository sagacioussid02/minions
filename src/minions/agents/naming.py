"""Agent display-name registry.

Phase 4 of openspec/sprint-tasks-memory. Resolves an ``agent_id``
(``role@project`` or ``role@shared``) to a human first-name. Renames write
back to ``config/agent_names.yaml``. Already-stamped Decision / Task
records keep their original display_name — renaming only affects new
emissions.

The agent_id format is intentionally simple: split on ``@``, project is
everything after. ``shared`` is the sentinel for executives and the
specialist/audit pool. Synthetic crew agents (pr_followup, etc.) live
under shared too.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PATH = REPO_ROOT / "config" / "agent_names.yaml"


def _load(path: Path | None = None) -> dict[str, str]:
    p = path or DEFAULT_PATH
    if not p.exists():
        return {}
    try:
        raw = yaml.safe_load(p.read_text()) or {}
    except (yaml.YAMLError, OSError):
        return {}
    agents = raw.get("agents") if isinstance(raw, dict) else None
    if not isinstance(agents, dict):
        return {}
    return {str(k): str(v) for k, v in agents.items() if v}


def _save(names: dict[str, str], path: Path | None = None) -> None:
    p = path or DEFAULT_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    body: dict[str, Any] = {"agents": {k: names[k] for k in sorted(names)}}
    # Preserve the header comment so the file stays self-documenting.
    header = (
        "# Agent name registry. See src/minions/agents/naming.py for the API.\n"
        '# Manage via:  minions agents name <agent_id> "<Display Name>"\n'
    )
    p.write_text(header + yaml.safe_dump(body, sort_keys=False))


def _prettify_role(role: str) -> str:
    return " ".join(part.capitalize() for part in role.split("_"))


def resolve_display_name(
    agent_id: str,
    *,
    path: Path | None = None,
    fallback: str | None = None,
) -> str:
    """Return the human name for ``agent_id``.

    Order of resolution:
      1. Exact match in the registry
      2. ``role@project`` base name when the requested id has a seat
         suffix (``role@project#2`` → "Sasha #2") — so the operator gets
         a sensible distinct name even before they explicitly seed every
         multi-seat name. Multi-seat support per openspec/multi-seat-roster.
      3. ``role@shared`` if ``agent_id`` is ``role@<project>`` and the
         per-project entry is missing — lets shared bench cover gaps
      4. ``fallback`` (caller-provided)
      5. PrettyRole (e.g. "Cloud Devops")
    """
    names = _load(path)
    if agent_id in names:
        return names[agent_id]
    # Strip the seat suffix and try the base id, so the operator can name
    # an agent once and get "Sasha", "Sasha #2", "Sasha #3" automatically.
    base_id, sep, suffix = agent_id.partition("#")
    if sep and suffix.isdigit() and base_id in names:
        return f"{names[base_id]} #{suffix}"
    if "@" in agent_id:
        role, _, project = base_id.partition("@") if sep else agent_id.partition("@")
        if project != "shared":
            shared = names.get(f"{role}@shared")
            if shared:
                return f"{shared} #{suffix}" if sep and suffix.isdigit() else shared
        if fallback:
            return fallback
        pretty = _prettify_role(role)
        return f"{pretty} #{suffix}" if sep and suffix.isdigit() else pretty
    return fallback or _prettify_role(agent_id)


def set_display_name(agent_id: str, name: str, *, path: Path | None = None) -> None:
    """Write a new display name for ``agent_id`` to the registry."""
    if not agent_id or "@" not in agent_id:
        raise ValueError(f"invalid agent_id {agent_id!r}: expected role@project")
    if not name or not name.strip():
        raise ValueError("name must be non-empty")
    names = _load(path)
    names[agent_id] = name.strip()
    _save(names, path)
    _sync_to_db(agent_id, name.strip())


def _sync_to_db(agent_id: str, name: str) -> None:
    """Best-effort mirror into the Postgres ``agent_names`` table so the
    dashboard roster/Live views resolve the new name. No-op without a DB."""
    role, _, project = agent_id.partition("@")
    project = (project or "shared").partition("#")[0]  # drop seat suffix
    try:
        from minions.db.connection import connect, has_database_url

        if not has_database_url():
            return
        with connect() as conn, conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS agent_names ("
                "role text NOT NULL, project text NOT NULL, "
                "display_name text NOT NULL, PRIMARY KEY (role, project))"
            )
            cur.execute(
                "INSERT INTO agent_names (role, project, display_name) "
                "VALUES (%s, %s, %s) ON CONFLICT (role, project) "
                "DO UPDATE SET display_name = EXCLUDED.display_name",
                (role, project, name),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 — naming never blocks on DB availability
        pass


def list_all(path: Path | None = None) -> dict[str, str]:
    """Return the full agent_id → display_name map."""
    return _load(path)
