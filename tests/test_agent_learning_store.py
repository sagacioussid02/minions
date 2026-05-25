"""Tests for durable agent learning storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from minions.agile.store import AgileStore
from minions.crews.engineer import EngineerResult
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.learning.store import AgentLearningStore
from minions.models.agile import AgileRitualRecord, PMAnswerRecord
from minions.models.learning import AgentLearningRecord


def _record(**kwargs) -> AgentLearningRecord:
    base = {
        "agent_id": "principal_engineer@demo_four",
        "role": "principal_engineer",
        "project": "demo_four",
        "kind": "technical",
        "fact": "demo_four UI is deployed through Vercel.",
        "source_type": "investigation",
        "source_id": "run-1",
        "confidence": "high",
    }
    base.update(kwargs)
    return AgentLearningRecord.model_validate(base)


def test_save_get_and_list_by_agent_round_trip(tmp_path: Path) -> None:
    store = AgentLearningStore(tmp_path / "learning.json")
    record = store.save(_record())

    fetched = store.get(record.id)
    assert fetched is not None
    assert fetched.fact == "demo_four UI is deployed through Vercel."
    assert store.list_by_agent("principal_engineer@demo_four")[0].id == record.id


def test_list_relevant_filters_project_role_kind_and_includes_global(
    tmp_path: Path,
) -> None:
    store = AgentLearningStore(tmp_path / "learning.json")
    project_record = store.save(_record(fact="demo_four uses Next.js."))
    global_record = store.save(
        _record(
            agent_id="cto@org",
            role="principal_engineer",
            project=None,
            kind="process",
            fact="Deployment answers need cited evidence.",
        )
    )
    store.save(
        _record(
            agent_id="principal_engineer@Demo",
            project="Demo",
            fact="Demo uses a separate UI.",
        )
    )

    relevant = store.list_relevant(role="principal_engineer", project="demo_four", limit=5)

    assert [record.id for record in relevant] == [global_record.id, project_record.id]
    assert store.list_relevant(project="demo_four", kind="process") == [global_record]
    assert store.list_relevant(project="demo_four", include_global=False) == [project_record]


def test_superseded_and_expired_records_are_inactive(tmp_path: Path) -> None:
    store = AgentLearningStore(tmp_path / "learning.json")
    old = store.save(_record(fact="Old deployment guess."))
    new = store.save(_record(fact="Verified deployment target."))
    expired = store.save(
        _record(
            fact="Temporary release freeze.",
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )

    store.supersede(old.id, new.id)

    assert store.list_all() == [new]
    inactive = {record.id for record in store.list_all(include_inactive=True)}
    assert inactive == {old.id, new.id, expired.id}


def test_mark_used_updates_last_used_at(tmp_path: Path) -> None:
    store = AgentLearningStore(tmp_path / "learning.json")
    record = store.save(_record())

    updated = store.mark_used(record.id)

    assert updated is not None
    assert updated.last_used_at is not None
    assert store.get(record.id) == updated


def test_corrupt_file_returns_empty_and_recovers(tmp_path: Path) -> None:
    path = tmp_path / "learning.json"
    path.write_text("{not json")
    store = AgentLearningStore(path)

    assert store.list_all() == []
    record = store.save(_record())
    assert store.get(record.id) == record


def test_pm_answers_emit_candidate_learning(tmp_path: Path) -> None:
    agile = AgileStore(tmp_path / "agile.json")
    answer = PMAnswerRecord(
        project="demo_four",
        question="Where is the UI deployed?",
        answer="No verified deployment evidence yet.",
        citations=["project manifest:demo_four"],
    )

    agile.save_pm_answer(answer)

    learning = AgentLearningStore(tmp_path / "agent_learning.json").list_all()
    assert len(learning) == 1
    assert learning[0].source_type == "pm_answer"
    assert learning[0].project == "demo_four"
    assert learning[0].role == "product_manager"
    assert "Where is the UI deployed?" in learning[0].fact


def test_rituals_emit_candidate_learning(tmp_path: Path) -> None:
    agile = AgileStore(tmp_path / "agile.json")
    ritual = AgileRitualRecord(
        project="demo_four",
        ritual="scrum",
        period_start=datetime.now(UTC) - timedelta(days=1),
        period_end=datetime.now(UTC),
        summary="Reviewed deployment evidence.",
        blockers=["Need access to hosting provider"],
        next_actions=["PE verifies repo config"],
    )

    agile.save_ritual(ritual)

    learning = AgentLearningStore(tmp_path / "agent_learning.json").list_all()
    assert len(learning) == 1
    assert learning[0].source_type == "agile_ritual"
    assert learning[0].kind == "risk"
    assert "Need access to hosting provider" in learning[0].fact


def test_engineer_runs_emit_candidate_learning_without_duplicates(tmp_path: Path) -> None:
    runs = EngineerRunStore(tmp_path / "runs.json")
    result = EngineerResult(
        decision_id="dec-1",
        pr_url="https://github.com/o/r/pull/1",
        pr_number=1,
        branch_name="minions/eng/x",
        files_changed=["web/app/page.tsx", "web/lib/deploy.ts"],
        files_rejected=[],
        operator_comment_posted=True,
        dry_run=False,
    )

    record = runs.save(result, project="demo_four")
    runs.update(record)

    learning = AgentLearningStore(tmp_path / "agent_learning.json").list_all()
    assert len(learning) == 1
    assert learning[0].source_type == "engineer_run"
    assert learning[0].kind == "technical"
    assert "web/app/page.tsx" in learning[0].fact
