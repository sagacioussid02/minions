"""Backend selection tests for the Decision Store factory.

The Postgres round-trip test runs only when ``MINIONS_DATABASE_URL`` (or
``DATABASE_URL``) is set, so the suite stays green on machines without
Neon configured.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from minions.approval.store import DecisionStore
from minions.approval.store_factory import make_decision_store
from minions.models.decision import Decision, DecisionStatus, DecisionType


def _has_db_url() -> bool:
    return bool(os.environ.get("MINIONS_DATABASE_URL") or os.environ.get("DATABASE_URL"))


def test_factory_forces_json_when_env_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from minions.audit.store import AuditFindingStore
    from minions.audit.store_factory import make_audit_findings_store
    from minions.crews.engineer_runs_store import EngineerRunStore
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.dossiers.store import DossierDraftStore
    from minions.dossiers.store_factory import make_dossier_store

    monkeypatch.setenv("MINIONS_STORE_BACKEND", "json")
    # Even if a database URL is set, json wins.
    monkeypatch.setenv("MINIONS_DATABASE_URL", "postgres://x/y")

    assert isinstance(make_decision_store(tmp_path / "d.json"), DecisionStore)
    assert isinstance(make_audit_findings_store(tmp_path / "a.json"), AuditFindingStore)
    assert isinstance(make_engineer_runs_store(tmp_path / "e.json"), EngineerRunStore)
    assert isinstance(make_dossier_store(tmp_path / "do.json"), DossierDraftStore)


def test_factory_falls_back_to_json_when_no_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MINIONS_STORE_BACKEND", raising=False)
    monkeypatch.delenv("MINIONS_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Force has_database_url() False by mocking get_secret to raise.
    from minions import secrets as secrets_module

    def _raise(name: str) -> str:
        raise secrets_module.SecretNotFound(name)

    monkeypatch.setattr(secrets_module, "get_secret", _raise)
    # Also patch the import binding inside connection.py.
    from minions.db import connection as conn_mod

    monkeypatch.setattr(conn_mod, "get_secret", _raise)

    store = make_decision_store(tmp_path / "decisions.json")
    assert isinstance(store, DecisionStore)


@pytest.mark.skipif(not _has_db_url(), reason="No database URL configured")
def test_postgres_cost_log_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime

    from minions import cost as cost_module
    from minions.db.migrate import apply_migrations

    apply_migrations()
    monkeypatch.setenv("MINIONS_LOGS_BACKEND", "postgres")
    # Reset the test-mode override that prior tests may have set.
    cost_module._force_jsonl.set(False)

    project = f"pg-cost-rt-{datetime.now(tz=UTC).timestamp()}"
    e = cost_module.CostEntry(
        timestamp=datetime.now(tz=UTC),
        project=project,
        decision_id="dec-rt",
        role="manager",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.000525,
    )
    try:
        cost_module.append_entry(e)
        rows = [x for x in cost_module.read_log() if x.project == project]
        assert len(rows) == 1
        assert rows[0].input_tokens == 100
    finally:
        # Prod-Neon cleanup — same pattern as the other roundtrip tests.
        # Without this, every pytest run accumulates a 'pg-cost-rt-*' row
        # which surfaces as a phantom project in the operator console.
        from minions.db.connection import connect as _conn

        with _conn() as _c, _c.cursor() as _cur:
            _cur.execute("DELETE FROM cost_log WHERE project = %s", (project,))
            _c.commit()


@pytest.mark.skipif(not _has_db_url(), reason="No database URL configured")
def test_postgres_activity_log_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    from minions import activity as activity_module
    from minions.db.migrate import apply_migrations

    apply_migrations()
    monkeypatch.setenv("MINIONS_LOGS_BACKEND", "postgres")
    activity_module._force_jsonl = False

    run_id = uuid4().hex
    started = activity_module.ActivityEntry(
        timestamp=datetime.now(tz=UTC),
        event="crew_started",
        run_id=run_id,
        crew="planning",
        project="pg-act-rt",
        decision_id="dec-rt",
        agents=("manager", "principal"),
    )
    try:
        activity_module.append(started)
        matches = [e for e in activity_module.read_log() if e.run_id == run_id]
        assert len(matches) == 1
        assert matches[0].agents == ("manager", "principal")
    finally:
        # CRITICAL: tests against prod Neon must clean up after themselves.
        # Without this finally, every pytest run pollutes activity_log with
        # a "pg-act-rt" project row that shows up in the Stage / Sprint Board
        # as a phantom agent + tasks.
        from minions.db.connection import connect as _conn

        with _conn() as _c, _c.cursor() as _cur:
            _cur.execute("DELETE FROM activity_log WHERE run_id = %s", (run_id,))
            _c.commit()


@pytest.mark.skipif(not _has_db_url(), reason="No database URL configured")
def test_postgres_audit_findings_round_trip() -> None:
    from minions.audit.store_postgres import PostgresAuditFindingStore
    from minions.db.migrate import apply_migrations
    from minions.models.audit import AuditFinding, FindingCategory

    apply_migrations()
    store = PostgresAuditFindingStore()

    f = AuditFinding(
        source_project="pg-test",
        source_pr_url="https://github.com/test/repo/pull/9999",
        category=FindingCategory.CODE,
        severity="advisory",
        auditor_role="code_auditor",
        auditor_agent_id="code_auditor@shared",
        summary="postgres round-trip",
        evidence="testing",
        recommendation="none",
    )
    try:
        store.save(f)
        assert store.has_finding_for_pr(f.source_pr_url)
        assert any(x.id == f.id for x in store.list_open())
    finally:
        from minions.db.connection import connect as _conn

        with _conn() as _c, _c.cursor() as _cur:
            _cur.execute("DELETE FROM audit_findings WHERE id = %s", (str(f.id),))
            _c.commit()


@pytest.mark.skipif(not _has_db_url(), reason="No database URL configured")
def test_postgres_engineer_runs_round_trip() -> None:
    from datetime import UTC, datetime

    from minions.crews.engineer_runs_store import EngineerRunRecord
    from minions.crews.engineer_runs_store_postgres import PostgresEngineerRunStore
    from minions.db.migrate import apply_migrations

    apply_migrations()
    store = PostgresEngineerRunStore()
    rec = EngineerRunRecord(
        decision_id="dec-pg-rt-1",
        project="pg-test",
        completed_at=datetime.now(tz=UTC),
        pr_url="https://example/pr/1",
        pr_state="open",
    )
    try:
        store.update(rec)
        fetched = store.get(rec.decision_id)
        assert fetched is not None and fetched.pr_state == "open"
    finally:
        from minions.db.connection import connect as _conn

        with _conn() as _c, _c.cursor() as _cur:
            _cur.execute("DELETE FROM engineer_runs WHERE decision_id = %s", (rec.decision_id,))
            _c.commit()


@pytest.mark.skipif(not _has_db_url(), reason="No database URL configured")
def test_postgres_dossier_drafts_round_trip() -> None:
    from minions.db.migrate import apply_migrations
    from minions.dossiers.store_postgres import PostgresDossierDraftStore
    from minions.models.dossier import DossierDraft, DossierSection, DossierStatus

    apply_migrations()
    store = PostgresDossierDraftStore()

    d = DossierDraft(
        project="pg-dossier-rt",
        commit_sha="deadbeef" * 5,
        status=DossierStatus.DRAFTED,
        markdown="# Architecture\nSee `x.py:1`.\n",
        sections_present=[DossierSection.ARCHITECTURE],
    )
    try:
        store.save(d)
        fetched = store.get(str(d.id))
        assert fetched is not None
        assert fetched.commit_sha == d.commit_sha
        assert fetched.status is DossierStatus.DRAFTED

        d.status = DossierStatus.MERGED
        d.pr_url = "https://github.com/o/r/pull/77"
        d.pr_number = 77
        store.save(d)

        latest = store.latest_merged("pg-dossier-rt")
        assert latest is not None
        assert latest.pr_number == 77
    finally:
        from minions.db.connection import connect as _conn

        with _conn() as _c, _c.cursor() as _cur:
            _cur.execute("DELETE FROM dossier_drafts WHERE id = %s::uuid", (str(d.id),))
            _c.commit()


@pytest.mark.skipif(not _has_db_url(), reason="No database URL configured")
def test_postgres_decision_store_round_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    from minions.approval.store_postgres import PostgresDecisionStore
    from minions.db.migrate import apply_migrations

    apply_migrations()
    monkeypatch.setenv("MINIONS_STORE_BACKEND", "postgres")
    store = PostgresDecisionStore()

    d = Decision(
        project="test-project-pg-roundtrip",
        type=DecisionType.FEATURE,
        summary="round-trip test",
        rationale="just checking",
        diff_or_plan="(none)",
        proposer_role="manager",
        proposer_agent_id="manager@test-project-pg-roundtrip",
    )
    try:
        store.save(d)
        fetched = store.get(d.id)
        assert fetched is not None
        assert fetched.id == d.id
        assert fetched.summary == "round-trip test"

        store.update_status(d.id, DecisionStatus.APPROVED, reason="test pass")
        again = store.get(d.id)
        assert again is not None
        assert again.status == DecisionStatus.APPROVED
        assert again.resolved_reason == "test pass"

        pending = [x for x in store.list_by_status(DecisionStatus.APPROVED) if x.id == d.id]
        assert len(pending) == 1
    finally:
        # Prod-Neon cleanup — see the activity-log test for the same pattern.
        from minions.db.connection import connect as _conn

        with _conn() as _c, _c.cursor() as _cur:
            _cur.execute("DELETE FROM decisions WHERE id = %s::uuid", (str(d.id),))
            _c.commit()
