"""Tests for src/minions/dossiers/sync.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from minions.approval.store import DecisionStore
from minions.crews.engineer_runs_store import EngineerRunRecord, EngineerRunStore
from minions.dossiers.refresh import (
    COMMIT_SHA_KEY,
    DRAFT_ID_KEY,
    TARGET_PATH_KEY,
)
from minions.dossiers.store import DossierDraftStore
from minions.dossiers.sync import sync_dossier_drafts
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.dossier import DossierDraft, DossierStatus


def _decision_linked_to(draft: DossierDraft, *, status: DecisionStatus) -> Decision:
    d = Decision(
        project=draft.project,
        type=DecisionType.DOSSIER_REFRESH,
        risk="low",
        summary="dossier refresh",
        rationale="x",
        proposer_role="cloud_devops",
        proposer_agent_id=f"discoverer@{draft.project}",
    )
    d.status = status
    d.__pydantic_extra__ = {
        DRAFT_ID_KEY: str(draft.id),
        TARGET_PATH_KEY: "PROJECT_DOSSIER.md",
        COMMIT_SHA_KEY: draft.commit_sha,
    }
    return d


def _draft(project: str, sha: str = "abc", **kw) -> DossierDraft:
    return DossierDraft(
        project=project,
        commit_sha=sha,
        markdown="# x",
        generated_at=kw.pop("generated_at", datetime.now(UTC)),
        status=kw.pop("status", DossierStatus.DRAFTED),
        **kw,
    )


def _run(
    decision: Decision,
    *,
    pr_state: str | None = None,
    pr_url: str = "https://x/p/1",
) -> EngineerRunRecord:
    return EngineerRunRecord(
        decision_id=str(decision.id),
        project=decision.project,
        completed_at=datetime.now(UTC),
        pr_url=pr_url,
        pr_state=pr_state,
    )


def test_merge_flips_draft_to_merged(tmp_path: Path) -> None:
    drafts = DossierDraftStore(tmp_path / "dr.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    draft = _draft("p1", status=DossierStatus.PR_OPEN)
    drafts.save(draft)
    decision = _decision_linked_to(draft, status=DecisionStatus.EXECUTED)
    decisions.save(decision)
    runs.update(_run(decision, pr_state="merged"))

    report = sync_dossier_drafts(
        dossier_store=drafts,
        decision_store=decisions,
        engineer_runs_store=runs,
    )

    assert report.merged == 1
    fetched = drafts.get(str(draft.id))
    assert fetched is not None
    assert fetched.status is DossierStatus.MERGED
    assert fetched.merged_at is not None


def test_rejected_decision_flips_draft(tmp_path: Path) -> None:
    drafts = DossierDraftStore(tmp_path / "dr.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    draft = _draft("p")
    drafts.save(draft)
    decision = _decision_linked_to(draft, status=DecisionStatus.REJECTED)
    decisions.save(decision)

    report = sync_dossier_drafts(
        dossier_store=drafts, decision_store=decisions, engineer_runs_store=runs
    )

    assert report.rejected == 1
    assert drafts.get(str(draft.id)).status is DossierStatus.REJECTED  # type: ignore[union-attr]


def test_pr_closed_without_merge_flips_to_rejected(tmp_path: Path) -> None:
    drafts = DossierDraftStore(tmp_path / "dr.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    draft = _draft("p")
    drafts.save(draft)
    decision = _decision_linked_to(draft, status=DecisionStatus.EXECUTED)
    decisions.save(decision)
    runs.update(_run(decision, pr_state="closed"))

    report = sync_dossier_drafts(
        dossier_store=drafts, decision_store=decisions, engineer_runs_store=runs
    )

    assert report.rejected == 1


def test_pending_decision_is_noop(tmp_path: Path) -> None:
    drafts = DossierDraftStore(tmp_path / "dr.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    draft = _draft("p")
    drafts.save(draft)
    decision = _decision_linked_to(draft, status=DecisionStatus.PENDING)
    decisions.save(decision)

    report = sync_dossier_drafts(
        dossier_store=drafts, decision_store=decisions, engineer_runs_store=runs
    )

    assert report.transitions == []
    assert drafts.get(str(draft.id)).status is DossierStatus.DRAFTED  # type: ignore[union-attr]


def test_executed_with_open_pr_flips_drafted_to_pr_open(tmp_path: Path) -> None:
    drafts = DossierDraftStore(tmp_path / "dr.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    draft = _draft("p")
    drafts.save(draft)
    decision = _decision_linked_to(draft, status=DecisionStatus.EXECUTED)
    decisions.save(decision)
    runs.update(_run(decision, pr_state="open"))

    report = sync_dossier_drafts(
        dossier_store=drafts, decision_store=decisions, engineer_runs_store=runs
    )

    assert len(report.transitions) == 1
    assert report.transitions[0].to_status is DossierStatus.PR_OPEN


def test_older_drafts_superseded_when_newer_in_flight(tmp_path: Path) -> None:
    drafts = DossierDraftStore(tmp_path / "dr.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    now = datetime.now(UTC)
    older = _draft("p", sha="old", generated_at=now - timedelta(days=2))
    newer = _draft("p", sha="new", generated_at=now)
    drafts.save(older)
    drafts.save(newer)

    report = sync_dossier_drafts(
        dossier_store=drafts, decision_store=decisions, engineer_runs_store=runs
    )

    assert report.superseded == 1
    fetched_older = drafts.get(str(older.id))
    fetched_newer = drafts.get(str(newer.id))
    assert fetched_older is not None
    assert fetched_older.status is DossierStatus.SUPERSEDED
    assert fetched_newer is not None
    assert fetched_newer.status is DossierStatus.DRAFTED


def test_terminal_drafts_skipped(tmp_path: Path) -> None:
    drafts = DossierDraftStore(tmp_path / "dr.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    draft = _draft("p", status=DossierStatus.MERGED)
    drafts.save(draft)

    report = sync_dossier_drafts(
        dossier_store=drafts, decision_store=decisions, engineer_runs_store=runs
    )
    assert report.transitions == []


def test_sync_is_idempotent(tmp_path: Path) -> None:
    drafts = DossierDraftStore(tmp_path / "dr.json")
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    draft = _draft("p")
    drafts.save(draft)
    decision = _decision_linked_to(draft, status=DecisionStatus.EXECUTED)
    decisions.save(decision)
    runs.update(_run(decision, pr_state="merged"))

    sync_dossier_drafts(dossier_store=drafts, decision_store=decisions, engineer_runs_store=runs)
    second = sync_dossier_drafts(
        dossier_store=drafts, decision_store=decisions, engineer_runs_store=runs
    )
    assert second.transitions == []
