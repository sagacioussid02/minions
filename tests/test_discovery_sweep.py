"""Tests for src/minions/scheduled/discovery.py.

The orchestrator should:

* Skip projects whose latest merged dossier is freshness=``ok`` (idempotency).
* Honor ``force=True`` to re-run regardless of freshness.
* Surface verifier failures as ``verifier_failed`` without persisting the draft.
* Throttle when month-to-date cost is in budget breach (not dry-run only).
* Persist successful drafts at status ``drafted``.

We patch ``resolve_working_tree`` + ``run_discoverer`` so the test runs without
cloning, without an LLM, and without depending on a real Anthropic key.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from minions.crews.discoverer import DossierVerificationError
from minions.dossiers.store import DossierDraftStore
from minions.models.dossier import (
    DossierDraft,
    DossierSection,
    DossierStatus,
)
from minions.scheduled.discovery import run_discovery_sweep


def _init_tiny_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    (root / "x.py").write_text("a\nb\n")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "x"], check=True)
    return root


def _projects_dir(tmp_path: Path, project: str, source_path: Path) -> Path:
    """Write a minimal manifest YAML the loader will accept."""
    pd = tmp_path / "projects"
    pd.mkdir()
    (pd / f"{project}.yaml").write_text(
        f"""name: {project}
description: test
source:
  kind: local
  path: {source_path}
weekly_budget_usd: 1.0
monthly_budget_usd: 4.0
owner: test
"""
    )
    return pd


def _good_draft(project: str, sha: str = "abc123") -> DossierDraft:
    return DossierDraft(
        project=project,
        commit_sha=sha,
        status=DossierStatus.DRAFTED,
        markdown="# Architecture\nSee `x.py:1`.\n",
        sections_present=[DossierSection.ARCHITECTURE],
    )


def test_skips_when_freshness_ok(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_tiny_repo(tmp_path)
    pd = _projects_dir(tmp_path, "tiny", root)
    store = DossierDraftStore(tmp_path / "drafts.json")
    # Seed a recent merged dossier so freshness == ok.
    store.save(
        DossierDraft(
            project="tiny",
            commit_sha="seed",
            status=DossierStatus.MERGED,
            markdown="x",
            generated_at=datetime.now(UTC) - timedelta(days=1),
        )
    )

    monkeypatch.setattr(
        "minions.scheduled.discovery.run_discoverer",
        lambda *a, **kw: pytest.fail("discoverer should not be called when fresh"),
    )

    report = run_discovery_sweep(projects_dir=pd, dossier_store=store, dry_run=False, force=False)
    assert len(report.outcomes) == 1
    assert report.outcomes[0].status == "skipped_fresh"


def test_force_runs_even_when_fresh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_tiny_repo(tmp_path)
    pd = _projects_dir(tmp_path, "tiny", root)
    store = DossierDraftStore(tmp_path / "drafts.json")
    store.save(
        DossierDraft(
            project="tiny",
            commit_sha="seed",
            status=DossierStatus.MERGED,
            markdown="x",
            generated_at=datetime.now(UTC),
        )
    )

    called: dict[str, Any] = {}

    def _fake(manifest: Any, **kw: Any) -> DossierDraft:
        called["yes"] = True
        return _good_draft(manifest.name)

    monkeypatch.setattr("minions.scheduled.discovery.run_discoverer", _fake)

    report = run_discovery_sweep(
        projects_dir=pd,
        dossier_store=store,
        api_key="k",
        dry_run=False,
        force=True,
    )
    assert called.get("yes")
    assert report.outcomes[0].status == "submitted"
    assert store.list_for_project("tiny", status=DossierStatus.DRAFTED)


def test_verifier_failure_surfaces_and_does_not_persist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_tiny_repo(tmp_path)
    pd = _projects_dir(tmp_path, "tiny", root)
    store = DossierDraftStore(tmp_path / "drafts.json")

    bad = _good_draft("tiny")

    def _raise(manifest: Any, **kw: Any) -> DossierDraft:
        raise DossierVerificationError("bad citation", draft=bad)

    monkeypatch.setattr("minions.scheduled.discovery.run_discoverer", _raise)

    report = run_discovery_sweep(projects_dir=pd, dossier_store=store, api_key="k", dry_run=False)
    assert report.outcomes[0].status == "verifier_failed"
    # Nothing persisted.
    assert store.list_for_project("tiny") == []


def test_budget_breach_throttles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_tiny_repo(tmp_path)
    pd = _projects_dir(tmp_path, "tiny", root)
    store = DossierDraftStore(tmp_path / "drafts.json")

    from minions.budget import BudgetState

    monkeypatch.setattr(
        "minions.scheduled.discovery.evaluate_budget",
        lambda manifest, **kw: BudgetState(
            project=manifest.name,
            monthly_cap_usd=4.0,
            month_to_date_usd=4.0,
            fraction=1.0,
            state="breach",
        ),
    )
    monkeypatch.setattr(
        "minions.scheduled.discovery.run_discoverer",
        lambda *a, **kw: pytest.fail("discoverer should not be called when budget breached"),
    )

    report = run_discovery_sweep(
        projects_dir=pd,
        dossier_store=store,
        api_key="k",
        dry_run=False,
        force=True,
    )
    assert report.outcomes[0].status == "throttled"
    assert "budget" in (report.outcomes[0].reason or "")


def test_dry_run_records_skipped_fresh_with_dry_run_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _init_tiny_repo(tmp_path)
    pd = _projects_dir(tmp_path, "tiny", root)
    store = DossierDraftStore(tmp_path / "drafts.json")

    # Dry-run path inside run_discoverer returns None; orchestrator should
    # report skipped_fresh with the "dry-run" reason and NOT persist.
    monkeypatch.setattr(
        "minions.scheduled.discovery.run_discoverer",
        lambda *a, **kw: None,
    )

    report = run_discovery_sweep(projects_dir=pd, dossier_store=store, dry_run=True, force=True)
    out = report.outcomes[0]
    assert out.status == "skipped_fresh"
    assert "dry-run" in (out.reason or "")
    assert store.list_for_project("tiny") == []


def test_successful_run_persists_drafted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _init_tiny_repo(tmp_path)
    pd = _projects_dir(tmp_path, "tiny", root)
    store = DossierDraftStore(tmp_path / "drafts.json")

    monkeypatch.setattr(
        "minions.scheduled.discovery.run_discoverer",
        lambda manifest, **kw: _good_draft(manifest.name),
    )

    report = run_discovery_sweep(
        projects_dir=pd,
        dossier_store=store,
        api_key="k",
        dry_run=False,
        force=True,
    )
    assert report.outcomes[0].status == "submitted"
    saved = store.list_for_project("tiny", status=DossierStatus.DRAFTED)
    assert len(saved) == 1
    assert saved[0].commit_sha == "abc123"


def test_target_missing_skip(tmp_path: Path) -> None:
    """If the working tree can't be resolved, surface skipped_target_missing."""
    pd = tmp_path / "projects"
    pd.mkdir()
    (pd / "ghost.yaml").write_text(
        """name: ghost
description: nope
source:
  kind: local
  path: /nonexistent/path/that/does/not/exist
weekly_budget_usd: 1.0
monthly_budget_usd: 4.0
owner: test
"""
    )
    store = DossierDraftStore(tmp_path / "drafts.json")
    report = run_discovery_sweep(projects_dir=pd, dossier_store=store, dry_run=True)
    assert report.outcomes[0].status == "skipped_target_missing"
