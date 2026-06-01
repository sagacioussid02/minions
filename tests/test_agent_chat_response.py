"""Tests for the agent_chat LLM dispatcher (Surface B / B2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from minions.agent_chat.chat import (
    DEFAULT_MODEL,
    EXEC_MODEL,
    render_system_prompt,
    respond,
    select_model_for,
)
from minions.agent_chat.context import ChatContext
from minions.models.agent_chat import AgentChatMessage
from minions.models.learning import AgentLearningRecord


def _learning(fact: str, *, confidence="high") -> AgentLearningRecord:
    return AgentLearningRecord.model_validate(
        {
            "agent_id": "engineer@Demo#1",
            "role": "engineer",
            "project": "Demo",
            "kind": "technical",
            "fact": fact,
            "source_type": "investigation",
            "source_id": "run-x",
            "confidence": confidence,
        }
    )


def _ctx(*, role="engineer", display_name="Vera", learning=None, cold_start=False) -> ChatContext:
    ctx = ChatContext(
        agent_id=f"{role}@Demo#1",
        role=role,
        display_name=display_name,
        project="Demo",
        persona=f"You are an agent with role '{role}'.\nYour name is {display_name}.",
        dossier_excerpt="# Demo dossier\n- next.js\n- vercel",
        learning=list(learning or []),
        transcript_snippets=[],
        cold_start=cold_start,
    )
    return ctx


@dataclass
class FakeUsage:
    input_tokens: int
    output_tokens: int


@dataclass
class FakeTextBlock:
    text: str


@dataclass
class FakeResponse:
    content: list[FakeTextBlock]
    usage: FakeUsage


class RecordingClient:
    def __init__(self, text: str = "Hi, I'm Vera."):
        self.last_kwargs: dict | None = None
        self._text = text

    class _Messages:
        def __init__(self, parent: RecordingClient):
            self._parent = parent

        def create(self, **kwargs):
            self._parent.last_kwargs = kwargs
            return FakeResponse(
                content=[FakeTextBlock(text=self._parent._text)],
                usage=FakeUsage(input_tokens=120, output_tokens=42),
            )

    @property
    def messages(self):
        return RecordingClient._Messages(self)


def test_model_selection_defaults_to_haiku() -> None:
    assert select_model_for(_ctx(role="engineer")) == DEFAULT_MODEL


def test_exec_roles_upgrade_to_sonnet() -> None:
    for role in ("ceo", "cto", "managing_director"):
        assert select_model_for(_ctx(role=role, display_name="X")) == EXEC_MODEL


def test_env_override_wins(monkeypatch) -> None:
    monkeypatch.setenv("MINIONS_AGENT_CHAT_MODEL", "claude-test-model")
    assert select_model_for(_ctx()) == "claude-test-model"


def test_system_prompt_contains_persona_dossier_and_learning() -> None:
    ctx = _ctx(learning=[_learning("Deploys go through Vercel.")])
    prompt = render_system_prompt(ctx)
    assert "Vera" in prompt
    assert "Demo dossier" in prompt
    assert "Deploys go through Vercel." in prompt


def test_cold_start_prompt_includes_hint() -> None:
    ctx = _ctx(cold_start=True)
    prompt = render_system_prompt(ctx)
    assert "don't invent details" in prompt


def test_respond_passes_history_and_returns_token_counts() -> None:
    ctx = _ctx(learning=[_learning("Vera fixed flaky CI last week.")])
    client = RecordingClient(text="Sure, here's what I shipped.")

    thread_id = uuid4()
    history = [
        AgentChatMessage(
            thread_id=thread_id,
            role="user",
            content="What did you ship?",
            created_at=datetime(2026, 5, 27, tzinfo=UTC),
        ),
        AgentChatMessage(
            thread_id=thread_id,
            role="agent",
            content="A few PRs.",
            created_at=datetime(2026, 5, 27, tzinfo=UTC),
        ),
    ]

    reply = respond(
        history=history,
        user_message="Anything broke?",
        context=ctx,
        api_key="test-key",
        client=client,
    )

    assert reply.text == "Sure, here's what I shipped."
    assert reply.model == DEFAULT_MODEL
    assert reply.prompt_tokens == 120
    assert reply.response_tokens == 42

    sent = client.last_kwargs
    assert sent is not None
    assert sent["model"] == DEFAULT_MODEL
    assert sent["max_tokens"] == 1024
    assert "Vera" in sent["system"]
    msgs = sent["messages"]
    # history (2) + current user turn (1)
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1]["content"] == "Anything broke?"
