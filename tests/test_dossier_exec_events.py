"""Tests for src/minions/dossiers/exec_events.py + sync integration.

Covers the delta computation matrix (first dossier, no-change refresh,
single-section change, multi-section change) and the emit + dedupe contract
through ``record_understanding_delta`` / ``record_backlog_proposed``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from minions.approval.store import DecisionStore
from minions.crews.engineer_runs_store import EngineerRunRecord, EngineerRunStore
from minions.dossiers.exec_events import (
    CEO_AGENT_ID,
    CTO_AGENT_ID,
    SOURCE_TYPE_BACKLOG,
    SOURCE_TYPE_UNDERSTANDING,
    compute_understanding_delta,
    record_backlog_proposed,
    record_understanding_delta,
)
from minions.dossiers.refresh import (
    COMMIT_SHA_KEY,
    DRAFT_ID_KEY,
    TARGET_PATH_KEY,
)
from minions.dossiers.store import DossierDraftStore
from minions.dossiers.sync import sync_dossier_drafts
from minions.learning.store import AgentLearningStore
from minions.models.backlog import BacklogCandidate, BacklogKind, BacklogProposal
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.dossier import DossierDraft, DossierStatus

# --------------------------- fixtures ---------------------------------------


def _md_full(
    *,
    hot_spots: str = "x `a.py:1`",
    tech_debt: str = "y `b.py:2`",
    architecture: str = "arch `m.py:1`",
    incidents: str = "i `c.py:3`",
) -> str:
    return (
        f"---\ncommit_sha: abc\n---\n\n"
        f"# Architecture\n{architecture}\n\n"
        f"# Data model & flows\nd `d.py:1`\n\n"
        f"# Infra & deploy topology\ne `e.py:1`\n\n"
        f"# Security posture\nsec `s.py:1`\n\n"
        f"# Hot spots\n{hot_spots}\n\n"
        f"# Tech-debt register\n{tech_debt}\n\n"
        f"# Recent incidents (last 90d)\n{incidents}\n\n"
        f"# Open questions for operator\nq?\n"
    )


def _draft(
    project: str,
    *,
    commit: str,
    markdown: str | None = None,
    status: DossierStatus = DossierStatus.MERGED,
    generated_at: datetime | None = None,
) -> DossierDraft:
    return DossierDraft(
        project=project,
        commit_sha=commit,
        markdown=markdown or _md_full(),
        status=status,
        generated_at=generated_at or datetime.now(UTC),
    )


# --------------------------- delta matrix -----------------------------------


def test_delta_first_dossier_marks_all_sections_changed() -> None:
    delta = compute_understanding_delta(prior=None, new=_draft("p", commit="a"))
    assert delta.first_dossier
    assert delta.any_change
    # Every section in the fixture has content, so all flags are True.
    assert delta.architecture_changed
    assert delta.data_changed
    assert delta.infra_changed
    assert delta.hot_spots_changed
    assert delta.tech_debt_changed
    assert delta.security_changed
    assert delta.incidents_changed
    assert delta.questions_changed


def test_delta_identical_dossier_reports_no_change() -> None:
    same = _md_full()
    delta = compute_understanding_delta(
        prior=_draft("p", commit="old", markdown=same),
        new=_draft("p", commit="new", markdown=same),
    )
    assert not delta.first_dossier
    assert not delta.any_change


def test_delta_single_section_change_isolated() -> None:
    old = _md_full(tech_debt="old debt `b.py:2`")
    new = _md_full(tech_debt="new debt `b.py:9`")
    delta = compute_understanding_delta(
        prior=_draft("p", commit="o", markdown=old),
        new=_draft("p", commit="n", markdown=new),
    )
    assert delta.tech_debt_changed
    assert not delta.architecture_changed
    assert not delta.data_changed
    assert delta.technical_changes == ["tech-debt register"]
    assert delta.functional_changes == []


def test_delta_multi_section_change_routes_to_both_audiences() -> None:
    old = _md_full()
    new = _md_full(
        architecture="new arch `m.py:99`",  # CTO sees
        hot_spots="hot `a.py:50`",  # CEO sees
        incidents="2026-05-19 prod hit",  # CEO sees
    )
    delta = compute_understanding_delta(
        prior=_draft("p", commit="o", markdown=old),
        new=_draft("p", commit="n", markdown=new),
    )
    assert "architecture" in delta.technical_changes
    assert "hot spots" in delta.functional_changes
    assert "recent incidents" in delta.functional_changes


# --------------------------- emit + dedupe ----------------------------------


def test_record_understanding_delta_emits_both_audiences(tmp_path: Path) -> None:
    store = AgentLearningStore(tmp_path / "learning.json")
    new = _draft("p", commit="abcdef00")
    ceo, cto = record_understanding_delta(prior=None, new=new, learning_store=store)
    assert ceo is not None
    assert cto is not None
    assert ceo.agent_id == CEO_AGENT_ID
    assert ceo.role == "ceo"
    assert ceo.kind == "product"
    assert ceo.source_type == SOURCE_TYPE_UNDERSTANDING
    assert cto.agent_id == CTO_AGENT_ID
    assert cto.role == "cto"
    assert cto.kind == "technical"
    assert "abcdef00" in ceo.fact or "first dossier" in ceo.fact.lower()


def test_record_understanding_delta_dedupes(tmp_path: Path) -> None:
    store = AgentLearningStore(tmp_path / "learning.json")
    new = _draft("p", commit="cafe1234")
    record_understanding_delta(prior=None, new=new, learning_store=store)
    record_understanding_delta(prior=None, new=new, learning_store=store)
    rows = store.list_all()
    assert len(rows) == 2  # ceo + cto, not 4


def test_no_change_refresh_still_emits_with_clear_marker(tmp_path: Path) -> None:
    store = AgentLearningStore(tmp_path / "learning.json")
    same = _md_full()
    prior = _draft("p", commit="old", markdown=same)
    new = _draft("p", commit="new", markdown=same)
    ceo, cto = record_understanding_delta(
        prior=prior,
        new=new,
        learning_store=store,
    )
    assert ceo is not None
    assert cto is not None
    assert "no user-facing sections" in ceo.fact
    assert "no architecture" in cto.fact


# --------------------------- backlog event ----------------------------------


def _backlog_decision_with(proposal: BacklogProposal) -> Decision:
    return Decision(
        project=proposal.project,
        type=DecisionType.OTHER,
        risk="medium",
        summary="backlog",
        rationale="x",
        proposer_role="product_owner",
        proposer_agent_id=f"backlog_proposer@{proposal.project}",
        status=DecisionStatus.APPROVED,
    )


def test_record_backlog_proposed_emits_cto_event(tmp_path: Path) -> None:
    store = AgentLearningStore(tmp_path / "learning.json")
    proposal = BacklogProposal(
        project="p",
        dossier_commit_sha="cafe1234",
        candidates=[
            BacklogCandidate(
                title="t1",
                body="b1",
                kind=BacklogKind.FEATURE,
                source_section="tech_debt",
            ),
            BacklogCandidate(
                title="t2",
                body="b2",
                kind=BacklogKind.BUG,
                source_section="hot_spots",
            ),
        ],
    )
    decision = _backlog_decision_with(proposal)
    event = record_backlog_proposed(
        decision=decision,
        proposal=proposal,
        learning_store=store,
        created_count=2,
    )
    assert event is not None
    assert event.agent_id == CTO_AGENT_ID
    assert event.source_type == SOURCE_TYPE_BACKLOG
    assert "2/2 created" in event.fact
    assert "feature" in event.fact
    assert "bug" in event.fact


def test_record_backlog_proposed_dedupes(tmp_path: Path) -> None:
    store = AgentLearningStore(tmp_path / "learning.json")
    proposal = BacklogProposal(
        project="p",
        dossier_commit_sha="cafe",
        candidates=[
            BacklogCandidate(
                title="t",
                body="b",
                kind=BacklogKind.FEATURE,
                source_section="x",
            )
        ],
    )
    decision = _backlog_decision_with(proposal)
    record_backlog_proposed(
        decision=decision,
        proposal=proposal,
        learning_store=store,
    )
    second = record_backlog_proposed(
        decision=decision,
        proposal=proposal,
        learning_store=store,
    )
    assert second is None
    assert len(store.list_all()) == 1


# --------------------------- sync integration -------------------------------


def _decision_for(draft: DossierDraft) -> Decision:
    d = Decision(
        project=draft.project,
        type=DecisionType.DOSSIER_REFRESH,
        risk="low",
        summary="x",
        rationale="x",
        proposer_role="cloud_devops",
        proposer_agent_id=f"discoverer@{draft.project}",
        status=DecisionStatus.EXECUTED,
    )
    d.__pydantic_extra__ = {
        DRAFT_ID_KEY: str(draft.id),
        TARGET_PATH_KEY: "PROJECT_DOSSIER.md",
        COMMIT_SHA_KEY: draft.commit_sha,
    }
    return d


def test_sync_merge_emits_executive_events(tmp_path: Path) -> None:
    drafts = DossierDraftStore(tmp_path / "dr.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")
    learning = AgentLearningStore(tmp_path / "learn.json")

    # Seed a prior merged dossier so we exercise the *diff* path (not first).
    prior = _draft(
        "p",
        commit="old0000",
        status=DossierStatus.MERGED,
        generated_at=datetime.now(UTC) - timedelta(days=10),
    )
    drafts.save(prior)

    draft = _draft(
        "p",
        commit="new0000",
        status=DossierStatus.PR_OPEN,
        markdown=_md_full(tech_debt="new debt `b.py:50`"),
    )
    drafts.save(draft)
    decision = _decision_for(draft)
    decisions.save(decision)
    runs.update(
        EngineerRunRecord(
            decision_id=str(decision.id),
            project="p",
            completed_at=datetime.now(UTC),
            pr_url="https://x/p/1",
            pr_state="merged",
        )
    )

    report = sync_dossier_drafts(
        dossier_store=drafts,
        decision_store=decisions,
        engineer_runs_store=runs,
        learning_store=learning,
    )

    assert report.merged == 1
    events = learning.list_all()
    # One ceo + one cto.
    assert {e.agent_id for e in events} == {CEO_AGENT_ID, CTO_AGENT_ID}
    assert all(e.source_type == SOURCE_TYPE_UNDERSTANDING for e in events)
    # Diff carried the prior commit through to the fact body.
    assert any("old0000" in e.fact and "new0000" in e.fact for e in events)


def test_sync_without_learning_store_is_silent(tmp_path: Path) -> None:
    drafts = DossierDraftStore(tmp_path / "dr.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    draft = _draft("p", commit="abcde000", status=DossierStatus.PR_OPEN)
    drafts.save(draft)
    decision = _decision_for(draft)
    decisions.save(decision)
    runs.update(
        EngineerRunRecord(
            decision_id=str(decision.id),
            project="p",
            completed_at=datetime.now(UTC),
            pr_url="https://x/p/1",
            pr_state="merged",
        )
    )
    # No learning_store passed — should not raise, sync still works.
    report = sync_dossier_drafts(
        dossier_store=drafts,
        decision_store=decisions,
        engineer_runs_store=runs,
    )
    assert report.merged == 1
