"""Tests for src/minions/dossiers/refresh.py."""

from __future__ import annotations

from pathlib import Path

from minions.approval.store import DecisionStore
from minions.dossiers.refresh import (
    COMMIT_SHA_KEY,
    DRAFT_ID_KEY,
    TARGET_PATH_KEY,
    build_dossier_engineer_output,
    draft_id_from_decision,
    file_dossier_refresh_decision,
    is_dossier_refresh_decision,
    section_diff_summary,
    target_path_for,
)
from minions.dossiers.store import DossierDraftStore
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.dossier import DossierDraft, DossierSection, DossierStatus
from minions.notify.base import Notifier


class _CapturingNotifier(Notifier):
    """No-op notifier that records what it would have sent."""

    def __init__(self) -> None:
        self.calls: list[Decision] = []

    def notify_approval_request(self, decision: Decision) -> None:
        self.calls.append(decision)

    def notify_resolution(self, decision: Decision) -> None:
        self.calls.append(decision)


def _manifest(name: str, publish: bool = True):
    import yaml

    from minions.models.manifest import load_manifest

    # Build a tiny manifest by piggybacking on Demo.yaml; keeps schema in sync.
    repo_root = Path(__file__).resolve().parents[1]
    src = repo_root / "projects" / "Demo.yaml"
    data = yaml.safe_load(src.read_text())
    data["name"] = name
    data["dossier"] = {"publish": publish}
    tmp = repo_root / "data" / "local" / "_test" / f"{name}.yaml"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(yaml.safe_dump(data))
    try:
        return load_manifest(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _draft(project: str, sha: str = "abcdef12") -> DossierDraft:
    return DossierDraft(
        project=project,
        commit_sha=sha,
        status=DossierStatus.DRAFTED,
        markdown="# Architecture\nSee `src/x.py:1`.\n",
        sections_present=[DossierSection.ARCHITECTURE, DossierSection.DATA],
        verifier_log="all 2 citations resolved",
    )


def test_target_path_in_repo_when_publish_true() -> None:
    m = _manifest("p1", publish=True)
    assert target_path_for(m) == "PROJECT_DOSSIER.md"


def test_target_path_internal_when_publish_false() -> None:
    m = _manifest("p2", publish=False)
    assert target_path_for(m) == "data/dossiers/p2.md"


def test_build_engineer_output_is_single_file() -> None:
    m = _manifest("p3")
    out = build_dossier_engineer_output(_draft("p3"), m)
    assert len(out.files) == 1
    fp = out.files[0]
    assert fp.path == "PROJECT_DOSSIER.md"
    assert fp.content.startswith("# Architecture")
    assert "abcdef12" in out.pr_title
    assert "Commit basis" in out.pr_body


def test_section_diff_first_dossier_message() -> None:
    msg = section_diff_summary(_draft("p"), prior=None)
    assert "First dossier" in msg
    assert "architecture" in msg


def test_section_diff_added_removed() -> None:
    prior = _draft("p", sha="old")
    prior.sections_present = [DossierSection.ARCHITECTURE]
    new = _draft("p", sha="new")
    new.sections_present = [DossierSection.ARCHITECTURE, DossierSection.SECURITY]
    msg = section_diff_summary(new, prior)
    assert "security" in msg
    assert "old" in msg
    assert "new" in msg


def test_file_decision_persists_payload_keys(tmp_path: Path) -> None:
    store = DecisionStore(tmp_path / "dec.json")
    drafts = DossierDraftStore(tmp_path / "dr.json")
    notifier = _CapturingNotifier()
    m = _manifest("decision-test")
    draft = _draft(m.name)

    decision = file_dossier_refresh_decision(
        draft=draft,
        manifest=m,
        decision_store=store,
        dossier_store=drafts,
        notifier=notifier,
    )

    assert decision.type == DecisionType.DOSSIER_REFRESH
    assert decision.risk == "low"
    assert decision.status is DecisionStatus.PENDING
    assert is_dossier_refresh_decision(decision)
    assert draft_id_from_decision(decision) == str(draft.id)

    # Notifier was called.
    assert len(notifier.calls) == 1

    # Payload keys round-trip through the store.
    persisted = store.get(decision.id)
    assert persisted is not None
    extra = getattr(persisted, "model_extra", None) or {}
    assert extra.get(DRAFT_ID_KEY) == str(draft.id)
    assert extra.get(TARGET_PATH_KEY) == "PROJECT_DOSSIER.md"
    assert extra.get(COMMIT_SHA_KEY) == draft.commit_sha


def test_is_dossier_refresh_decision_negative() -> None:
    d = Decision(
        project="x",
        type=DecisionType.FEATURE,
        summary="x",
        rationale="x",
        proposer_role="manager",
        proposer_agent_id="manager@x",
    )
    assert not is_dossier_refresh_decision(d)
    assert draft_id_from_decision(d) is None


def test_file_decision_is_idempotent_per_project(tmp_path: Path) -> None:
    """Re-filing for the same project returns the existing pending decision,
    does not double-notify, does not create a duplicate row."""
    from minions.dossiers.refresh import find_open_refresh_decision

    store = DecisionStore(tmp_path / "dec.json")
    drafts = DossierDraftStore(tmp_path / "dr.json")
    notifier = _CapturingNotifier()
    m = _manifest("dedupe-test")
    draft1 = _draft(m.name, sha="aaaa1111")
    draft2 = _draft(m.name, sha="bbbb2222")  # newer draft, different sha

    first = file_dossier_refresh_decision(
        draft=draft1,
        manifest=m,
        decision_store=store,
        dossier_store=drafts,
        notifier=notifier,
    )
    second = file_dossier_refresh_decision(
        draft=draft2,
        manifest=m,
        decision_store=store,
        dossier_store=drafts,
        notifier=notifier,
    )

    # Same Decision id returned both times.
    assert first.id == second.id
    # Only ONE notifier call (the first one).
    assert len(notifier.calls) == 1
    # Only ONE row in the store.
    assert len(store.list_all()) == 1
    # find_open_refresh_decision sees it.
    assert (
        find_open_refresh_decision(
            project=m.name,
            decision_store=store,
        )
        is not None
    )


def test_file_decision_allows_new_after_executed(tmp_path: Path) -> None:
    """Once a prior refresh is EXECUTED (PR opened), a new run is allowed."""
    from minions.models.decision import DecisionStatus

    store = DecisionStore(tmp_path / "dec.json")
    drafts = DossierDraftStore(tmp_path / "dr.json")
    notifier = _CapturingNotifier()
    m = _manifest("post-executed")
    first = file_dossier_refresh_decision(
        draft=_draft(m.name, sha="aaaa1111"),
        manifest=m,
        decision_store=store,
        dossier_store=drafts,
        notifier=notifier,
    )
    # Simulate the engineer crew having opened the PR.
    first.status = DecisionStatus.EXECUTED
    store.save(first)

    second = file_dossier_refresh_decision(
        draft=_draft(m.name, sha="bbbb2222"),
        manifest=m,
        decision_store=store,
        dossier_store=drafts,
        notifier=notifier,
    )

    assert second.id != first.id
    assert len(store.list_all()) == 2
    assert len(notifier.calls) == 2
