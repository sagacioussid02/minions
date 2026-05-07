"""Tests for src/minions/audit/store.py."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from minions.audit import AuditFindingStore
from minions.models.audit import AuditFinding, FindingCategory, FindingStatus


def _make(severity: str = "medium", pr_url: str = "https://github.com/o/r/pull/1") -> AuditFinding:
    return AuditFinding(
        source_project="p",
        source_decision_id=UUID("00000000-0000-0000-0000-000000000001"),
        source_pr_url=pr_url,
        category=FindingCategory.CODE,
        severity=severity,  # type: ignore[arg-type]
        summary="x",
        evidence="e",
        recommendation="r",
        auditor_role="code_auditor",
        auditor_agent_id="code_auditor@org",
    )


def test_save_and_get(tmp_path: Path) -> None:
    store = AuditFindingStore(tmp_path / "findings.json")
    f = _make()
    store.save(f)
    fetched = store.get(str(f.id))
    assert fetched is not None
    assert fetched.summary == "x"
    assert fetched.severity == "medium"


def test_list_open_filters_resolved(tmp_path: Path) -> None:
    store = AuditFindingStore(tmp_path / "findings.json")
    f1 = _make()
    f2 = _make()
    f2.status = FindingStatus.RESOLVED
    store.save(f1)
    store.save(f2)
    open_findings = store.list_open()
    assert len(open_findings) == 1
    assert open_findings[0].id == f1.id


def test_has_finding_for_pr(tmp_path: Path) -> None:
    store = AuditFindingStore(tmp_path / "findings.json")
    store.save(_make(pr_url="https://github.com/o/r/pull/1"))
    assert store.has_finding_for_pr("https://github.com/o/r/pull/1") is True
    assert store.has_finding_for_pr("https://github.com/o/r/pull/99") is False


def test_corrupt_file_returns_empty(tmp_path: Path) -> None:
    p = tmp_path / "findings.json"
    p.write_text("{ not json")
    store = AuditFindingStore(p)
    assert store.list_all() == []
    # Saves still work afterward
    store.save(_make())
    assert len(store.list_all()) == 1
