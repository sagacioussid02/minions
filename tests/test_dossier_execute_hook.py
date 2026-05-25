"""Tests for the DOSSIER_REFRESH hook inside execute_approved.

Verifies that an approved DOSSIER_REFRESH decision:
* causes the runner to be called with an ``output_override`` built from the
  linked draft, and
* flips the linked draft to ``pr_open`` with the engineer PR url after the
  run succeeds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from minions.approval.store import DecisionStore
from minions.crews.engineer import EngineerOutput, EngineerResult
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.dossiers.refresh import (
    COMMIT_SHA_KEY,
    DRAFT_ID_KEY,
    TARGET_PATH_KEY,
)
from minions.dossiers.store import DossierDraftStore
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.dossier import DossierDraft, DossierSection, DossierStatus
from minions.scheduled.execute_approved import run_execute_approved

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECTS_DIR = REPO_ROOT / "projects"


def _fake_github(_manifest: Any) -> Any:
    class _C:
        def __enter__(self) -> _C:
            return self

        def __exit__(self, *_: Any) -> None:
            return None

    return _C()


def test_dossier_refresh_supplies_override_and_flips_draft(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "dec.json")
    drafts = DossierDraftStore(tmp_path / "dr.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    draft = DossierDraft(
        project="Demo",
        commit_sha="cafe1234",
        markdown="# Architecture\nSee `src/x.py:1`.\n",
        sections_present=[DossierSection.ARCHITECTURE],
        status=DossierStatus.DRAFTED,
    )
    drafts.save(draft)

    d = Decision(
        project="Demo",
        type=DecisionType.DOSSIER_REFRESH,
        risk="low",
        summary="refresh",
        rationale="x",
        proposer_role="cloud_devops",
        proposer_agent_id="discoverer@Demo",
        status=DecisionStatus.APPROVED,
    )
    d.__pydantic_extra__ = {
        DRAFT_ID_KEY: str(draft.id),
        TARGET_PATH_KEY: "PROJECT_DOSSIER.md",
        COMMIT_SHA_KEY: draft.commit_sha,
    }
    decisions.save(d)

    seen_override: dict[str, Any] = {}

    def runner(decision: Decision, manifest: Any, **kw: Any) -> EngineerResult:
        ov = kw.get("output_override")
        seen_override["was"] = ov
        assert isinstance(ov, EngineerOutput)
        assert len(ov.files) == 1
        assert ov.files[0].path == "PROJECT_DOSSIER.md"
        assert ov.files[0].content == draft.markdown
        return EngineerResult(
            decision_id=str(decision.id),
            pr_url="https://example/pr/55",
            pr_number=55,
            branch_name="minions/eng/dossier",
            files_changed=["PROJECT_DOSSIER.md"],
            dry_run=False,
        )

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=_fake_github,
        dry_run=False,
        runner=runner,
        dossier_store=drafts,
    )

    assert report.executed == 1
    assert seen_override.get("was") is not None

    refreshed = drafts.get(str(draft.id))
    assert refreshed is not None
    assert refreshed.status is DossierStatus.PR_OPEN
    assert refreshed.pr_url == "https://example/pr/55"
    assert refreshed.pr_number == 55


def test_dossier_refresh_without_store_errors(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "dec.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    d = Decision(
        project="Demo",
        type=DecisionType.DOSSIER_REFRESH,
        risk="low",
        summary="refresh",
        rationale="x",
        proposer_role="cloud_devops",
        proposer_agent_id="discoverer@Demo",
        status=DecisionStatus.APPROVED,
    )
    decisions.save(d)

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=_fake_github,
        dry_run=False,
        runner=lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[misc]
            AssertionError("runner must not be called")
        ),
        dossier_store=None,
    )

    assert report.executed == 0
    assert report.outcomes[0].status == "error"
    assert "dossier_store" in (report.outcomes[0].reason or "")


def test_dossier_refresh_missing_draft_errors(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "dec.json")
    drafts = DossierDraftStore(tmp_path / "dr.json")
    runs = EngineerRunStore(tmp_path / "runs.json")

    d = Decision(
        project="Demo",
        type=DecisionType.DOSSIER_REFRESH,
        risk="low",
        summary="refresh",
        rationale="x",
        proposer_role="cloud_devops",
        proposer_agent_id="discoverer@Demo",
        status=DecisionStatus.APPROVED,
    )
    d.__pydantic_extra__ = {DRAFT_ID_KEY: "00000000-0000-0000-0000-000000000000"}
    decisions.save(d)

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=decisions,
        engineer_runs_store=runs,
        open_github_client=_fake_github,
        dry_run=False,
        runner=lambda *a, **k: (_ for _ in ()).throw(  # type: ignore[misc]
            AssertionError("runner must not be called")
        ),
        dossier_store=drafts,
    )

    assert report.outcomes[0].status == "error"
    assert "not found" in (report.outcomes[0].reason or "")
