"""Build the ChatContext that drives an agent's Surface B persona.

The bundle is the *only* place persona is assembled. The chat dispatcher
(``chat.py``) renders the result into a system prompt and ships it to the
Anthropic SDK. Keep this module pure — no LLM calls, no IO beyond the
stores it's handed.

Budget contract: total prompt material is capped at ``MAX_PROMPT_BYTES``
(UTF-8). The dossier excerpt is truncated first to honour the cap; learning
records are clipped second; transcripts third.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from minions.agents.base import MinionAgent
from minions.agents.safety import safety_preamble_for
from minions.models.learning import AgentLearningRecord
from minions.models.transcript import CrewTranscriptMessage

if TYPE_CHECKING:
    from minions.dossiers.store_factory import DossierStoreLike
    from minions.learning.store_factory import AgentLearningStoreLike
    from minions.transcripts.store_factory import TranscriptStoreLike

MAX_PROMPT_BYTES = 8 * 1024
MAX_DOSSIER_BYTES = 2 * 1024
MAX_LEARNING_RECORDS = 15
MAX_TRANSCRIPT_SNIPPETS = 5
TRANSCRIPT_SNIPPET_CHARS = 400


@dataclass
class ChatContext:
    """Everything ``chat.py`` needs to render a system prompt."""

    agent_id: str
    role: str
    display_name: str
    project: str | None
    persona: str  # safety preamble + role/display framing
    dossier_excerpt: str  # first ~2 KB of the latest merged PROJECT_DOSSIER.md
    learning: list[AgentLearningRecord] = field(default_factory=list)
    transcript_snippets: list[CrewTranscriptMessage] = field(default_factory=list)
    cold_start: bool = False  # True when no learning AND no transcripts loaded
    total_bytes: int = 0


def build_agent_context(
    agent_id: str,
    *,
    learning_store: AgentLearningStoreLike,
    dossier_store: DossierStoreLike,
    transcript_store: TranscriptStoreLike,
    agent: MinionAgent | None = None,
) -> ChatContext:
    """Assemble the persona+context bundle for ``agent_id``.

    ``agent`` may be passed in directly (tests); otherwise resolved from
    manifests + portfolio config via :func:`resolve_agent_from_id`.
    """
    resolved = agent if agent is not None else resolve_agent_from_id(agent_id)
    if resolved is None:
        raise LookupError(f"Unknown agent_id: {agent_id}")

    project = resolved.project
    role = resolved.role.value
    # Display name resolution: prefer the MinionAgent's own field (set from
    # the project manifest when the operator named the seat there), then
    # fall back to the shared registry in ``config/agent_names.yaml`` so
    # "Vera" surfaces even when demo_three.yaml's ``agents:`` block is empty.
    display_name = resolved.display_name
    if not display_name:
        try:
            from minions.agents.naming import resolve_display_name

            display_name = resolve_display_name(agent_id, fallback=resolved.name)
        except Exception:  # noqa: BLE001 — naming lookup is best-effort
            display_name = resolved.name

    persona = _render_persona(role=role, display_name=display_name, project=project)

    # Dossier — only for project-scoped agents; shared agents have no project.
    dossier_excerpt = ""
    if project is not None:
        latest = dossier_store.latest_merged(project)
        if latest is not None:
            dossier_excerpt = _truncate_utf8(latest.markdown, MAX_DOSSIER_BYTES)

    # Learning — agent-specific first, then role-relevant cross-agent.
    agent_records = learning_store.list_by_agent(agent_id)
    agent_records = sorted(agent_records, key=_learning_sort_key)
    learning_records: list[AgentLearningRecord] = list(agent_records[:MAX_LEARNING_RECORDS])
    if len(learning_records) < MAX_LEARNING_RECORDS:
        # Top up with role-relevant records (e.g. shared-bench cross-project).
        extra = learning_store.list_relevant(
            role=role,
            project=project,
            limit=MAX_LEARNING_RECORDS,
        )
        seen = {r.id for r in learning_records}
        for record in sorted(extra, key=_learning_sort_key):
            if record.id in seen:
                continue
            learning_records.append(record)
            if len(learning_records) >= MAX_LEARNING_RECORDS:
                break

    # Recent transcripts — most-recent first, capped.
    transcripts: list[CrewTranscriptMessage] = []
    if project is not None:
        transcripts = list(
            transcript_store.list_for_project(project, limit=MAX_TRANSCRIPT_SNIPPETS)
        )
    else:
        # Shared agent: cross-project transcripts. Fall back to list_all() and trim.
        all_msgs = transcript_store.list_all()
        transcripts = list(all_msgs[:MAX_TRANSCRIPT_SNIPPETS])

    cold_start = not learning_records and not transcripts

    ctx = ChatContext(
        agent_id=agent_id,
        role=role,
        display_name=display_name,
        project=project,
        persona=persona,
        dossier_excerpt=dossier_excerpt,
        learning=learning_records,
        transcript_snippets=transcripts,
        cold_start=cold_start,
    )
    _enforce_budget(ctx)
    return ctx


def _render_persona(*, role: str, display_name: str, project: str | None) -> str:
    safety = safety_preamble_for(role=role, project=project)
    name_line = f"Your operator-facing name is {display_name}. Speak in first person.\n\n"
    return name_line + safety


def _learning_sort_key(record: AgentLearningRecord) -> tuple[int, float]:
    # Confidence first (high > medium > low), recency second.
    rank = {"high": 0, "medium": 1, "low": 2}.get(record.confidence, 3)
    when = record.last_used_at or record.created_at
    # Negative timestamp so newer comes first under ascending sort.
    return (rank, -when.timestamp())


def _truncate_utf8(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    # Decode-truncate-safely on a byte slice; ignore partial trailing codepoints.
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _snippet(msg: CrewTranscriptMessage) -> str:
    body = msg.content[:TRANSCRIPT_SNIPPET_CHARS]
    return f"[{msg.crew}/{msg.agent_role}] {body}"


def _bundle_bytes(ctx: ChatContext) -> int:
    parts = [ctx.persona, ctx.dossier_excerpt]
    parts.extend(r.fact for r in ctx.learning)
    parts.extend(_snippet(m) for m in ctx.transcript_snippets)
    return sum(len(p.encode("utf-8")) for p in parts)


def _enforce_budget(ctx: ChatContext) -> None:
    """Trim from most-disposable backward until the bundle fits MAX_PROMPT_BYTES.

    Order of sacrifice: dossier → learning tail → transcripts. The persona
    block is sacrosanct (safety preamble must always reach the model).
    """
    # Pass 1: shrink dossier.
    while _bundle_bytes(ctx) > MAX_PROMPT_BYTES and ctx.dossier_excerpt:
        new_len = max(0, len(ctx.dossier_excerpt.encode("utf-8")) // 2)
        ctx.dossier_excerpt = _truncate_utf8(ctx.dossier_excerpt, new_len)
        if new_len == 0:
            ctx.dossier_excerpt = ""
            break

    # Pass 2: drop learning records from the tail.
    while _bundle_bytes(ctx) > MAX_PROMPT_BYTES and ctx.learning:
        ctx.learning.pop()

    # Pass 3: drop transcript snippets.
    while _bundle_bytes(ctx) > MAX_PROMPT_BYTES and ctx.transcript_snippets:
        ctx.transcript_snippets.pop()

    ctx.total_bytes = _bundle_bytes(ctx)


# --- agent_id resolver --------------------------------------------------------


def resolve_agent_from_id(agent_id: str) -> MinionAgent | None:
    """Walk active manifests + portfolio config to find the seat owning ``agent_id``.

    Returns None when the id doesn't match any current roster seat.
    """
    from minions.agents.roster import (
        AUDIT,
        SHARED_EXECUTIVE,
        SHARED_SPECIALIST,
        build_project_agents,
        build_shared_agents,
    )
    from minions.config.portfolio import load_portfolio_config
    from minions.models.manifest import load_active_manifests

    repo_root = Path(__file__).resolve().parents[3]
    try:
        manifests = load_active_manifests(repo_root / "projects")
        portfolio = load_portfolio_config(repo_root / "config" / "portfolio.yaml")
    except Exception:  # noqa: BLE001 — best-effort lookup; missing config returns None
        return None

    for manifest in manifests.values():
        for agent in build_project_agents(manifest):
            if agent.name == agent_id:
                return agent
    for layer in (SHARED_EXECUTIVE, SHARED_SPECIALIST, AUDIT):
        for agent in build_shared_agents(portfolio, layer):
            if agent.name == agent_id:
                return agent
    return None
