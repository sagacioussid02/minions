"""Tests for §9.4 — Code Auditor sampling + audit_pr() flow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import httpx

from minions.audit import AuditFindingStore, audit_after_sync
from minions.crews.code_auditor import (
    _SAMPLE_RATES,
    CodeAuditOutput,
    audit_pr,
    should_audit,
)
from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.github.client import GitHubClient
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.manifest import Manifest


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> GitHubClient:
    return GitHubClient(token="x", repo="o/r", transport=httpx.MockTransport(handler))


def _decision(risk: str, status: DecisionStatus = DecisionStatus.EXECUTED) -> Decision:
    return Decision(
        project="p",
        type=DecisionType.FEATURE,
        summary="add feature",
        rationale="r",
        diff_or_plan="p",
        risk=risk,
        proposer_role="manager",
        proposer_agent_id="m@p",
        status=status,
        pr_url="https://github.com/o/r/pull/1",
    )


def _record(decision_id: str = "dec-1") -> EngineerResult:
    return EngineerResult(
        decision_id=decision_id,
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        branch_name="minions/eng/x",
        files_changed=["a.py"],
        files_rejected=[],
        operator_comment_posted=True,
        dry_run=False,
    )


def _saved_record(store, decision_id: str = "dec-1", project: str = "p"):
    """Save an EngineerResult through the store and return the persisted record."""
    return store.save(_record(decision_id), project=project)


def _override() -> CodeAuditOutput:
    return CodeAuditOutput(
        severity="medium",
        summary="missing test coverage on new branch",
        evidence="a.py:42 — handler swallows ValueError silently, no test exercises it",
        recommendation="add a unit test for the empty-input case before next release",
    )


# ---- Sampling gate ---------------------------------------------------------


def test_sample_rates_documented() -> None:
    assert _SAMPLE_RATES == {"high": 100, "medium": 50, "low": 25}


def test_high_risk_always_audited() -> None:
    # Try a bunch of decision ids — high risk must always be True.
    for i in range(20):
        assert should_audit(f"dec-{i}", "high") is True


def test_low_risk_sampled_around_25_percent() -> None:
    sampled = sum(1 for i in range(1000) if should_audit(f"dec-{i}", "low"))
    # 25% nominal, allow generous slack so the test isn't flaky.
    assert 200 <= sampled <= 320


def test_medium_risk_sampled_around_50_percent() -> None:
    sampled = sum(1 for i in range(1000) if should_audit(f"dec-{i}", "medium"))
    assert 420 <= sampled <= 580


def test_sampling_is_deterministic() -> None:
    """Same decision id must yield the same sample answer every call."""
    answers = {should_audit("stable-id", "low") for _ in range(5)}
    assert len(answers) == 1


# ---- audit_pr() -----------------------------------------------------------


def test_audit_pr_with_override_skips_llm(tmp_path: Path) -> None:
    routes: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        routes.append(req.url.path)
        if req.url.path == "/repos/o/r/pulls/1/files":
            return httpx.Response(
                200,
                json=[
                    {
                        "filename": "a.py",
                        "status": "modified",
                        "additions": 5,
                        "deletions": 1,
                        "patch": "@@ -1 +1 @@\n-pass\n+x = 1",
                    }
                ],
            )
        return httpx.Response(404)

    runs = EngineerRunStore(tmp_path / "runs.json")
    rec = _saved_record(runs)
    finding = audit_pr(
        rec,
        _decision("medium"),
        github=_client(handler),
        api_key=None,
        output_override=_override(),
    )
    assert finding is not None
    assert finding.severity == "medium"
    assert finding.summary.startswith("missing test")
    assert finding.source_pr_url == "https://github.com/o/r/pull/1"
    assert finding.auditor_role == "code_auditor"
    # Should hit the files endpoint exactly once even though override is set
    # (we still want the LLM-free path to fetch the diff for evidence).
    assert "/repos/o/r/pulls/1/files" in routes


def test_audit_pr_no_files_returns_none(tmp_path: Path) -> None:
    """A PR with zero changed files (binary-only / weird) returns None."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/repos/o/r/pulls/1/files":
            return httpx.Response(200, json=[])
        return httpx.Response(404)

    runs = EngineerRunStore(tmp_path / "runs.json")
    rec = _saved_record(runs)
    finding = audit_pr(
        rec,
        _decision("medium"),
        github=_client(handler),
        api_key=None,
        output_override=None,  # need real audit, but no files → None
    )
    assert finding is None


def test_audit_pr_normalizes_invalid_severity(tmp_path: Path) -> None:
    """LLM returning 'critical' or anything off-list collapses to advisory."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "filename": "a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "patch": "+x",
                }
            ],
        )

    bad = CodeAuditOutput(
        severity="CRITICAL",  # not in {advisory, medium, high}
        summary="x",
        evidence="x",
        recommendation="x",
    )
    runs = EngineerRunStore(tmp_path / "runs.json")
    rec = _saved_record(runs)
    finding = audit_pr(
        rec,
        _decision("high"),
        github=_client(handler),
        output_override=bad,
    )
    assert finding is not None
    assert finding.severity == "advisory"


def test_audit_pr_no_pr_number_returns_none(tmp_path: Path) -> None:
    runs = EngineerRunStore(tmp_path / "runs.json")
    rec = runs.save(
        EngineerResult(decision_id="dec-1", pr_url=None, pr_number=None, dry_run=False),
        project="p",
    )
    finding = audit_pr(
        rec,
        _decision("high"),
        github=_client(lambda r: httpx.Response(500)),
        output_override=_override(),
    )
    assert finding is None


# ---- audit_after_sync runner ----------------------------------------------


def _make_manifest() -> Manifest:
    return Manifest.model_validate(
        {
            "name": "p",
            "description": "test",
            "source": {"kind": "github", "path": "/tmp", "repo": "o/r", "default_branch": "main"},
            "weekly_budget_usd": 1.0,
            "monthly_budget_usd": 4.0,
            "owner": "o@o",
        }
    )


def test_audit_after_sync_only_audits_newly_merged(tmp_path: Path) -> None:
    """A 'open → open' transition should not trigger an audit."""
    from minions.sync import SyncOutcome

    runs = EngineerRunStore(tmp_path / "runs.json")
    runs.save(_record(), project="p")
    findings = AuditFindingStore(tmp_path / "findings.json")

    decision_store = _DecisionStoreStub({_record().decision_id: _decision("high")})

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "filename": "a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "patch": "+x",
                }
            ],
        )

    # 'open → open' — should be skipped
    outcome_unchanged = SyncOutcome(
        decision_id=_record().decision_id,
        project="p",
        before="open",
        after="open",
    )
    report = audit_after_sync(
        sync_outcomes=[outcome_unchanged],
        runs_store=runs,
        decision_store=decision_store,  # type: ignore[arg-type]
        findings_store=findings,
        open_github_client=lambda m: _client(handler),
        manifests={"p": _make_manifest()},
        output_override=_override(),
    )
    assert report.audited == 0
    assert findings.list_all() == []


def test_audit_after_sync_audits_high_risk_merge(tmp_path: Path) -> None:
    from minions.sync import SyncOutcome

    runs = EngineerRunStore(tmp_path / "runs.json")
    runs.save(_record(), project="p")
    findings = AuditFindingStore(tmp_path / "findings.json")
    d = _decision("high")
    decision_store = _DecisionStoreStub({_record().decision_id: d})

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {
                    "filename": "a.py",
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "patch": "+x",
                }
            ],
        )

    outcome_merged = SyncOutcome(
        decision_id=_record().decision_id,
        project="p",
        before="open",
        after="merged",
    )
    report = audit_after_sync(
        sync_outcomes=[outcome_merged],
        runs_store=runs,
        decision_store=decision_store,  # type: ignore[arg-type]
        findings_store=findings,
        open_github_client=lambda m: _client(handler),
        manifests={"p": _make_manifest()},
        output_override=_override(),
    )
    assert report.audited == 1
    assert len(findings.list_all()) == 1


def test_audit_after_sync_skips_low_risk_when_not_sampled(tmp_path: Path) -> None:
    """Pick a decision id known to fall outside the 25% low-risk window."""
    # Find an id that does NOT pass the low-risk gate.
    from minions.crews.code_auditor import should_audit
    from minions.sync import SyncOutcome

    skipped_id = next(f"never-{i}" for i in range(1000) if not should_audit(f"never-{i}", "low"))
    runs = EngineerRunStore(tmp_path / "runs.json")
    runs.save(
        EngineerResult(decision_id=skipped_id, pr_url="u", pr_number=1, dry_run=False), project="p"
    )
    findings = AuditFindingStore(tmp_path / "findings.json")
    decision_store = _DecisionStoreStub({skipped_id: _decision("low")})

    outcome_merged = SyncOutcome(decision_id=skipped_id, project="p", before="open", after="merged")
    report = audit_after_sync(
        sync_outcomes=[outcome_merged],
        runs_store=runs,
        decision_store=decision_store,  # type: ignore[arg-type]
        findings_store=findings,
        open_github_client=lambda m: _client(lambda r: httpx.Response(500)),
        manifests={"p": _make_manifest()},
        output_override=_override(),
    )
    assert report.audited == 0
    assert any(o.skipped_reason == "not sampled" for o in report.outcomes)


def test_audit_after_sync_skips_already_audited(tmp_path: Path) -> None:
    """Re-running the cron must not double-audit the same PR."""
    from minions.sync import SyncOutcome

    runs = EngineerRunStore(tmp_path / "runs.json")
    runs.save(_record(), project="p")
    findings = AuditFindingStore(tmp_path / "findings.json")
    findings.save(
        # Pre-existing finding for this PR.
        _override_to_finding(_record().pr_url, _record().decision_id, "p")
    )
    d = _decision("high")
    decision_store = _DecisionStoreStub({_record().decision_id: d})

    outcome_merged = SyncOutcome(
        decision_id=_record().decision_id, project="p", before="open", after="merged"
    )
    report = audit_after_sync(
        sync_outcomes=[outcome_merged],
        runs_store=runs,
        decision_store=decision_store,  # type: ignore[arg-type]
        findings_store=findings,
        open_github_client=lambda m: _client(lambda r: httpx.Response(500)),
        manifests={"p": _make_manifest()},
        output_override=_override(),
    )
    assert report.audited == 0
    # Original finding still the only one
    assert len(findings.list_all()) == 1


# ---- Helpers ---------------------------------------------------------------


class _DecisionStoreStub:
    """Tiny stub matching the .get(id) → Decision|None signature."""

    def __init__(self, decisions: dict) -> None:
        self._d = decisions

    def get(self, decision_id):  # type: ignore[no-untyped-def]
        return self._d.get(str(decision_id))


def _override_to_finding(pr_url: str, decision_id: str, project: str):
    from minions.models.audit import AuditFinding, FindingCategory

    return AuditFinding(
        source_project=project,
        source_decision_id=UUID("00000000-0000-0000-0000-000000000001"),
        source_pr_url=pr_url,
        category=FindingCategory.CODE,
        severity="advisory",
        summary="pre-existing",
        evidence="x",
        recommendation="none",
        auditor_role="code_auditor",
        auditor_agent_id="code_auditor@org",
    )
