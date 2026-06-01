"""Anthropic dispatch for Surface B agent chat.

One Anthropic call per operator turn. The system prompt is rendered from a
:class:`ChatContext` (built by :mod:`minions.agent_chat.context`); the
``messages`` array carries the full thread so far. Default model is Haiku
4.5; CEO/CTO/MD seats upgrade to Sonnet so executive replies have the
right gravity.

Langfuse instrumentation is best-effort — missing credentials are a no-op,
never a failure.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from minions.agent_chat.context import ChatContext
from minions.observability import add_metadata, has_credentials

if TYPE_CHECKING:
    from minions.models.agent_chat import AgentChatMessage

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
EXEC_MODEL = "claude-sonnet-4-6"
EXEC_ROLES = frozenset({"ceo", "cto", "managing_director"})
MAX_REPLY_TOKENS = 1024
COLD_START_HINT = (
    "If the operator asks about specific past work and you don't see notes on "
    "it in your context, say so plainly — don't invent details."
)


@dataclass
class ChatReply:
    """Result of one dispatch call."""

    text: str
    model: str
    prompt_tokens: int
    response_tokens: int


def select_model_for(context: ChatContext) -> str:
    """Per-role model selection. Env override wins."""
    override = os.environ.get("MINIONS_AGENT_CHAT_MODEL")
    if override:
        return override
    if context.role in EXEC_ROLES:
        return EXEC_MODEL
    return DEFAULT_MODEL


def render_system_prompt(context: ChatContext) -> str:
    """Render the ChatContext into a system prompt string."""
    parts: list[str] = [context.persona.rstrip()]

    if context.dossier_excerpt:
        parts.append(
            "# Project dossier (excerpt)\n"
            "Use this as background on the project you work on. "
            "Do not quote it verbatim; refer to it naturally.\n\n"
            f"{context.dossier_excerpt.rstrip()}"
        )

    if context.learning:
        lines = ["# Your active notes (most-confident first)"]
        for i, record in enumerate(context.learning, 1):
            tag = f"({record.kind}, {record.confidence})"
            lines.append(f"{i}. {tag} {record.fact}")
        parts.append("\n".join(lines))

    if context.transcript_snippets:
        lines = ["# Recent work you and teammates produced"]
        for m in context.transcript_snippets:
            snippet = m.content[:400].replace("\n", " ").strip()
            lines.append(f"- [{m.crew}/{m.agent_role}] {snippet}")
        parts.append("\n".join(lines))

    if context.cold_start:
        parts.append(f"# Note\n{COLD_START_HINT}")

    parts.append(
        "When the operator addresses you, answer in first person as "
        f"{context.display_name}. Keep replies tight — a few sentences unless "
        "the question genuinely needs more."
    )
    return "\n\n".join(parts)


def respond(
    *,
    history: list[AgentChatMessage],
    user_message: str,
    context: ChatContext,
    api_key: str,
    model: str | None = None,
    client: object | None = None,
) -> ChatReply:
    """Dispatch one turn. ``client`` is injectable for tests."""
    chosen = model or select_model_for(context)
    system_prompt = render_system_prompt(context)

    messages: list[dict[str, str]] = []
    for m in history:
        messages.append(
            {
                "role": "user" if m.role == "user" else "assistant",
                "content": m.content,
            }
        )
    messages.append({"role": "user", "content": user_message})

    anthropic_client = client if client is not None else _build_client(api_key)
    response = anthropic_client.messages.create(  # type: ignore[attr-defined]
        model=chosen,
        max_tokens=MAX_REPLY_TOKENS,
        system=system_prompt,
        messages=messages,
    )

    text = _extract_text(response)
    prompt_tokens, response_tokens = _extract_usage(response)

    if has_credentials():
        add_metadata(
            surface="agent_chat",
            agent_id=context.agent_id,
            role=context.role,
            model=chosen,
            cold_start=context.cold_start,
            prompt_tokens=prompt_tokens,
            response_tokens=response_tokens,
        )

    return ChatReply(
        text=text,
        model=chosen,
        prompt_tokens=prompt_tokens,
        response_tokens=response_tokens,
    )


def _build_client(api_key: str) -> object:
    from anthropic import Anthropic

    return Anthropic(api_key=api_key)


def _extract_text(response: object) -> str:
    """Pull the first text block out of an Anthropic Messages response."""
    blocks = getattr(response, "content", None) or []
    chunks: list[str] = []
    for block in blocks:
        # SDK objects expose ``.text``; dicts use ``["text"]``.
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if isinstance(text, str):
            chunks.append(text)
    return "".join(chunks).strip()


def _extract_usage(response: object) -> tuple[int, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return (0, 0)
    prompt = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    return (int(prompt), int(out))
