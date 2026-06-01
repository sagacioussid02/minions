"""Tests for the agent_chat context bundler (Surface B / B1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from minions.agent_chat.context import (
    MAX_PROMPT_BYTES,
    build_agent_context,
)
from minions.agents.base import MinionAgent
from minions.models.dossier import DossierDraft, DossierStatus
from minions.models.learning import AgentLearningRecord
from minions.models.roles import ModelTier, Role
from minions.models.transcript import CrewTranscriptMessage

# --- fakes -------------------------------------------------------------------


@dataclass
class FakeLearningStore:
    by_agent: dict[str, list[AgentLearningRecord]]
    relevant: list[AgentLearningRecord]

    def list_by_agent(self, agent_id, include_inactive=False):
        return list(self.by_agent.get(agent_id, []))

    def list_relevant(self, *, role=None, project=None, kind=None, limit=10, include_global=True):
        return self.relevant[:limit]


@dataclass
class FakeDossierStore:
    drafts: dict[str, DossierDraft]

    def latest_merged(self, project):
        return self.drafts.get(project)


@dataclass
class FakeTranscriptStore:
    by_project: dict[str, list[CrewTranscriptMessage]]
    cross_project: list[CrewTranscriptMessage]

    def list_for_project(self, project, *, limit=50):
        return list(self.by_project.get(project, []))[:limit]

    def list_all(self):
        return list(self.cross_project)


# --- helpers -----------------------------------------------------------------


def _engineer_agent() -> MinionAgent:
    return MinionAgent(
        role=Role.ENGINEER,
        name="engineer@Demo#1",
        project="Demo",
        tier=ModelTier.HAIKU,
        backstory="",
        goal="",
        display_name="Vera",
        seat_index=1,
    )


def _shared_agent() -> MinionAgent:
    return MinionAgent(
        role=Role.CEO,
        name="ceo@org",
        project=None,
        tier=ModelTier.OPUS,
        backstory="",
        goal="",
        display_name="Carla",
    )


def _learning(agent_id: str, fact: str, *, confidence="high", role="engineer", project="Demo"):
    return AgentLearningRecord.model_validate(
        {
            "agent_id": agent_id,
            "role": role,
            "project": project,
            "kind": "technical",
            "fact": fact,
            "source_type": "investigation",
            "source_id": f"run-{uuid4().hex[:6]}",
            "confidence": confidence,
        }
    )


def _transcript(project: str, content: str, *, agent_role="engineer") -> CrewTranscriptMessage:
    return CrewTranscriptMessage.model_validate(
        {
            "run_id": "run-1",
            "project": project,
            "crew": "engineer",
            "agent_role": agent_role,
            "sequence": 0,
            "role_in_conversation": "task_output",
            "content": content,
        }
    )


def _dossier(project: str, body: str) -> DossierDraft:
    return DossierDraft.model_validate(
        {
            "project": project,
            "commit_sha": "abc123",
            "generated_at": datetime.now(tz=UTC),
            "status": DossierStatus.MERGED,
            "markdown": body,
        }
    )


# --- tests -------------------------------------------------------------------


def test_cold_start_agent_returns_persona_and_dossier_only() -> None:
    learning = FakeLearningStore(by_agent={}, relevant=[])
    dossier = FakeDossierStore(drafts={"Demo": _dossier("Demo", "# Demo dossier\nstuff")})
    transcripts = FakeTranscriptStore(by_project={}, cross_project=[])

    ctx = build_agent_context(
        "engineer@Demo#1",
        learning_store=learning,
        dossier_store=dossier,
        transcript_store=transcripts,
        agent=_engineer_agent(),
    )

    assert ctx.cold_start is True
    assert ctx.learning == []
    assert ctx.transcript_snippets == []
    assert "Demo dossier" in ctx.dossier_excerpt
    assert "engineer" in ctx.persona
    assert "Vera" in ctx.persona


def test_top_15_learning_records_selected_by_confidence_and_recency() -> None:
    agent_id = "engineer@Demo#1"
    records = [_learning(agent_id, f"low fact {i}", confidence="low") for i in range(20)]
    # Add high-confidence records so they should out-rank the lows.
    high = [_learning(agent_id, f"high fact {i}", confidence="high") for i in range(20)]
    learning = FakeLearningStore(by_agent={agent_id: records + high}, relevant=[])
    dossier = FakeDossierStore(drafts={})
    transcripts = FakeTranscriptStore(by_project={}, cross_project=[])

    ctx = build_agent_context(
        agent_id,
        learning_store=learning,
        dossier_store=dossier,
        transcript_store=transcripts,
        agent=_engineer_agent(),
    )

    assert len(ctx.learning) == 15
    assert all(r.confidence == "high" for r in ctx.learning)


def test_shared_bench_agent_has_no_project_dossier() -> None:
    learning = FakeLearningStore(by_agent={}, relevant=[])
    # Dossier registry has projects, but a shared agent has no project key.
    dossier = FakeDossierStore(drafts={"Demo": _dossier("Demo", "should not load")})
    transcripts = FakeTranscriptStore(
        by_project={"Demo": [_transcript("Demo", "noise")]},
        cross_project=[_transcript("Demo", "cross-project signal")],
    )

    ctx = build_agent_context(
        "ceo@org",
        learning_store=learning,
        dossier_store=dossier,
        transcript_store=transcripts,
        agent=_shared_agent(),
    )

    assert ctx.dossier_excerpt == ""
    assert ctx.project is None
    assert len(ctx.transcript_snippets) == 1
    assert "cross-project signal" in ctx.transcript_snippets[0].content


def test_prompt_bundle_never_exceeds_budget_truncates_dossier_first() -> None:
    huge_markdown = "X" * (MAX_PROMPT_BYTES * 3)
    learning = FakeLearningStore(by_agent={}, relevant=[])
    dossier = FakeDossierStore(drafts={"Demo": _dossier("Demo", huge_markdown)})
    transcripts = FakeTranscriptStore(by_project={}, cross_project=[])

    ctx = build_agent_context(
        "engineer@Demo#1",
        learning_store=learning,
        dossier_store=dossier,
        transcript_store=transcripts,
        agent=_engineer_agent(),
    )

    assert ctx.total_bytes <= MAX_PROMPT_BYTES
    # Dossier got truncated, but persona still present.
    assert "engineer" in ctx.persona


def test_unknown_agent_id_without_resolver_raises() -> None:
    learning = FakeLearningStore(by_agent={}, relevant=[])
    dossier = FakeDossierStore(drafts={})
    transcripts = FakeTranscriptStore(by_project={}, cross_project=[])

    try:
        build_agent_context(
            "engineer@does_not_exist",
            learning_store=learning,
            dossier_store=dossier,
            transcript_store=transcripts,
        )
    except LookupError:
        return
    raise AssertionError("expected LookupError")
