"""Tests for backlog proposal models, dedupe, decision filer, and worker."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from minions.approval.store import DecisionStore
from minions.dossiers.backlog import (
    DOSSIER_SHA_KEY,
    FOOTER_TEMPLATE,
    KIND_KEY,
    KIND_VALUE,
    PROPOSAL_KEY,
    build_backlog_proposal,
    create_issues_for_decision,
    file_backlog_decision,
    is_backlog_proposal_decision,
    proposal_from_decision,
)
from minions.dossiers.dedupe import dedupe_candidates
from minions.github.models import Issue
from minions.models.backlog import BacklogCandidate, BacklogKind, BacklogProposal, label_for
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.dossier import DossierDraft, DossierStatus
from minions.models.manifest import load_manifest
from minions.notify.base import Notifier

REPO_ROOT = Path(__file__).resolve().parents[1]


class _CapturingNotifier(Notifier):
    def __init__(self) -> None:
        self.calls: list[Decision] = []

    def notify_approval_request(self, decision: Decision) -> None:
        self.calls.append(decision)

    def notify_resolution(self, decision: Decision) -> None:
        self.calls.append(decision)


def _manifest(tmp_path: Path, name: str, *, cap: int = 5):
    src = REPO_ROOT / "projects" / "Demo.yaml"
    data = yaml.safe_load(src.read_text())
    data["name"] = name
    data["dossier"] = {"max_new_issues_per_cycle": cap}
    out = tmp_path / f"{name}.yaml"
    out.write_text(yaml.safe_dump(data))
    return load_manifest(out)


def _cand(
    title: str,
    *,
    kind: BacklogKind = BacklogKind.TECH_DEBT,
    citations: list[str] | None = None,
    body: str = "Some description with `src/x.py:42`.",
    source: str = "tech_debt",
) -> BacklogCandidate:
    return BacklogCandidate(
        title=title,
        body=body,
        kind=kind,
        source_section=source,
        citations=citations or [],
    )


def _issue(number: int, title: str, body: str | None = None) -> Issue:
    return Issue(
        number=number,
        title=title,
        body=body or "",
        state="open",
        html_url=f"https://x/issues/{number}",
    )


def _draft(project: str) -> DossierDraft:
    return DossierDraft(
        project=project,
        commit_sha="cafef00d" * 5,
        markdown="# x\nSee `src/x.py:1`.",
        status=DossierStatus.MERGED,
    )


# ----------------------------- labels ---------------------------------------


def test_label_for_each_kind() -> None:
    assert label_for(BacklogKind.FEATURE) == "minions/feature"
    assert label_for(BacklogKind.BUG) == "minions/bug"
    assert label_for(BacklogKind.TECH_DEBT) == "minions/tech-debt"
    assert label_for(BacklogKind.SECURITY) == "minions/security"


# ----------------------------- dedupe ---------------------------------------


def test_dedupe_drops_title_similarity() -> None:
    out = dedupe_candidates(
        [_cand("Fix flaky checkout test")],
        existing_issues=[_issue(1, "fix flaky checkout test (again)")],
    )
    assert out.kept == []
    assert "title similarity" in out.dropped[0][1]


def test_dedupe_drops_shared_anchor() -> None:
    out = dedupe_candidates(
        [_cand("Refactor middleware", citations=["src/auth.ts:120"])],
        existing_issues=[
            _issue(7, "Unrelated", body="Touched `src/auth.ts:120` originally."),
        ],
    )
    assert out.kept == []
    assert "shared anchor" in out.dropped[0][1]


def test_dedupe_keeps_unrelated() -> None:
    out = dedupe_candidates(
        [_cand("Brand-new feature X", citations=["src/feature_x.py:1"])],
        existing_issues=[_issue(1, "Refactor logging")],
    )
    assert len(out.kept) == 1
    assert out.dropped == []


def test_dedupe_strips_bracket_prefix() -> None:
    out = dedupe_candidates(
        [_cand("Fix login bug")],
        existing_issues=[_issue(1, "[bug] Fix login bug")],
    )
    assert out.kept == []


# ----------------------------- build cap ------------------------------------


def test_build_applies_cap(tmp_path: Path) -> None:
    m = _manifest(tmp_path, "cap-test", cap=2)
    raw = BacklogProposal(
        project=m.name,
        dossier_commit_sha="abc",
        candidates=[_cand(f"item {i}", citations=[f"src/x.py:{i}"]) for i in range(5)],
    )
    out = build_backlog_proposal(raw=raw, manifest=m, existing_issues=[])
    assert len(out.proposal.candidates) == 2
    assert out.capped == 3


def test_build_zero_cap_yields_empty(tmp_path: Path) -> None:
    m = _manifest(tmp_path, "zero-cap", cap=0)
    raw = BacklogProposal(
        project=m.name,
        dossier_commit_sha="abc",
        candidates=[_cand("x")],
    )
    out = build_backlog_proposal(raw=raw, manifest=m, existing_issues=[])
    assert out.proposal.candidates == []


def test_build_runs_dedupe_then_cap(tmp_path: Path) -> None:
    m = _manifest(tmp_path, "ded-cap", cap=2)
    raw = BacklogProposal(
        project=m.name,
        dossier_commit_sha="abc",
        candidates=[
            _cand("Refactor logging"),  # dropped by dedupe
            _cand("New thing A", citations=["src/a.py:1"]),
            _cand("New thing B", citations=["src/b.py:1"]),
            _cand("New thing C", citations=["src/c.py:1"]),
        ],
    )
    out = build_backlog_proposal(
        raw=raw,
        manifest=m,
        existing_issues=[_issue(1, "Refactor logging")],
    )
    titles = [c.title for c in out.proposal.candidates]
    assert "Refactor logging" not in titles
    assert len(out.proposal.candidates) == 2
    assert out.capped == 1


# ----------------------------- decision filer -------------------------------


def test_file_decision_round_trips_payload(tmp_path: Path) -> None:
    m = _manifest(tmp_path, "file-test", cap=5)
    store = DecisionStore(tmp_path / "dec.json")
    notifier = _CapturingNotifier()
    raw = BacklogProposal(
        project=m.name,
        dossier_commit_sha="cafe",
        candidates=[_cand("Add health endpoint", kind=BacklogKind.FEATURE)],
    )
    build = build_backlog_proposal(raw=raw, manifest=m, existing_issues=[])
    decision = file_backlog_decision(
        build=build,
        manifest=m,
        dossier=_draft(m.name),
        decision_store=store,
        notifier=notifier,
    )
    assert decision is not None
    assert decision.risk == "medium"
    assert decision.type is DecisionType.OTHER
    assert decision.status is DecisionStatus.PENDING
    assert is_backlog_proposal_decision(decision)
    assert len(notifier.calls) == 1

    persisted = store.get(decision.id)
    assert persisted is not None
    extra = getattr(persisted, "model_extra", None) or {}
    assert extra.get(KIND_KEY) == KIND_VALUE
    assert extra.get(DOSSIER_SHA_KEY) == _draft(m.name).commit_sha
    payload = extra.get(PROPOSAL_KEY)
    assert isinstance(payload, dict)
    proposal = BacklogProposal.model_validate(payload)
    assert proposal.candidates[0].title == "Add health endpoint"


def test_file_decision_returns_none_when_empty(tmp_path: Path) -> None:
    m = _manifest(tmp_path, "empty", cap=5)
    store = DecisionStore(tmp_path / "dec.json")
    notifier = _CapturingNotifier()
    raw = BacklogProposal(project=m.name, dossier_commit_sha="x", candidates=[])
    build = build_backlog_proposal(raw=raw, manifest=m, existing_issues=[])
    assert (
        file_backlog_decision(
            build=build,
            manifest=m,
            dossier=_draft(m.name),
            decision_store=store,
            notifier=notifier,
        )
        is None
    )
    assert notifier.calls == []


def test_proposal_from_decision_handles_non_backlog() -> None:
    d = Decision(
        project="p",
        type=DecisionType.FEATURE,
        summary="x",
        rationale="x",
        proposer_role="manager",
        proposer_agent_id="manager@p",
    )
    assert not is_backlog_proposal_decision(d)
    assert proposal_from_decision(d) is None


# ----------------------------- worker / create -----------------------------


class _FakeGitHub:
    def __init__(self, existing: list[Issue] | None = None) -> None:
        self.existing = existing or []
        self.created: list[dict[str, Any]] = []

    def __enter__(self):  # context manager parity with real client
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def list_open_issues(self, *, per_page: int = 50) -> list[Issue]:
        return self.existing

    def create_issue(self, *, title: str, body: str, labels: list[str] | None = None) -> Issue:
        n = len(self.created) + 100
        self.created.append({"title": title, "body": body, "labels": labels or []})
        return Issue(
            number=n,
            title=title,
            body=body,
            state="open",
            labels=labels or [],
            html_url=f"https://x/issues/{n}",
        )


def _approved_decision(m, build) -> Decision:
    """Reuse the filer to construct a payload-accurate Decision, then APPROVE it."""
    store = DecisionStore(Path("/tmp/__throwaway_dec.json"))
    notifier = _CapturingNotifier()
    d = file_backlog_decision(
        build=build,
        manifest=m,
        dossier=_draft(m.name),
        decision_store=store,
        notifier=notifier,
    )
    assert d is not None
    d.status = DecisionStatus.APPROVED
    return d


def test_create_issues_labels_and_footer(tmp_path: Path) -> None:
    m = _manifest(tmp_path, "create-test", cap=5)
    raw = BacklogProposal(
        project=m.name,
        dossier_commit_sha="cafef00d",
        candidates=[
            _cand("Add health endpoint", kind=BacklogKind.FEATURE, citations=["src/health.ts:1"]),
            _cand(
                "Tech debt: rip out old auth",
                kind=BacklogKind.TECH_DEBT,
                citations=["src/auth.ts:99"],
            ),
        ],
    )
    build = build_backlog_proposal(raw=raw, manifest=m, existing_issues=[])
    decision = _approved_decision(m, build)
    gh = _FakeGitHub()

    outcome = create_issues_for_decision(decision=decision, manifest=m, github=gh)

    assert len(outcome.created) == 2
    assert outcome.capped == 0
    # Labels are minions/* and unique per kind.
    assert gh.created[0]["labels"] == ["minions/feature"]
    assert gh.created[1]["labels"] == ["minions/tech-debt"]
    # Footer is appended on every body.
    for issued in gh.created:
        assert "Filed by minions discoverer" in issued["body"]
        assert "cafef00d" in issued["body"]


def test_create_redoes_dedupe_against_fresh_state(tmp_path: Path) -> None:
    m = _manifest(tmp_path, "fresh-dedupe", cap=5)
    raw = BacklogProposal(
        project=m.name,
        dossier_commit_sha="x",
        candidates=[
            _cand("Add health endpoint", kind=BacklogKind.FEATURE, citations=["src/health.ts:1"]),
        ],
    )
    build = build_backlog_proposal(raw=raw, manifest=m, existing_issues=[])
    decision = _approved_decision(m, build)

    # During the approval window, someone filed exactly that issue.
    gh = _FakeGitHub(existing=[_issue(99, "Add health endpoint")])
    outcome = create_issues_for_decision(decision=decision, manifest=m, github=gh)
    assert outcome.created == []
    assert outcome.dropped
    assert "title similarity" in outcome.dropped[0][1]


def test_create_rejects_non_backlog_decision(tmp_path: Path) -> None:
    m = _manifest(tmp_path, "x", cap=5)
    d = Decision(
        project=m.name,
        type=DecisionType.FEATURE,
        summary="x",
        rationale="x",
        proposer_role="manager",
        proposer_agent_id="manager@x",
        status=DecisionStatus.APPROVED,
    )
    with pytest.raises(ValueError, match="not a backlog proposal"):
        create_issues_for_decision(decision=d, manifest=m, github=_FakeGitHub())


def test_footer_template_format() -> None:
    rendered = FOOTER_TEMPLATE.format(commit_sha="deadbeef")
    assert "deadbeef" in rendered
    assert "Filed by minions" in rendered
