from __future__ import annotations

from pathlib import Path

from minions.agile.pm import answer_pm_question
from minions.agile.store import AgileStore
from minions.approval.store import DecisionStore
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.models.manifest import Manifest


def _manifest(tmp_path: Path) -> Manifest:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text(
        "# Project\nUses SECRET_API_KEY for deployment but never prints it.\n"
    )
    return Manifest.model_validate({
        "name": "alpha",
        "description": "Alpha helps users manage profile data.",
        "source": {"kind": "local", "path": str(project), "default_branch": "main"},
        "weekly_budget_usd": 1.0,
        "monthly_budget_usd": 4.0,
        "owner": "t@t",
    })


def test_pm_answers_functionality_question(tmp_path: Path) -> None:
    agile = AgileStore(tmp_path / "agile.json")
    answer = answer_pm_question(
        manifest=_manifest(tmp_path),
        question="What does this project do?",
        decision_store=DecisionStore(tmp_path / "decisions.json"),
        engineer_runs_store=EngineerRunStore(tmp_path / "runs.json"),
        agile_store=agile,
        activity_log_path=tmp_path / "activity.jsonl",
    )

    assert "Alpha helps users manage profile data" in answer.answer
    assert answer.citations
    assert agile.list_pm_answers("alpha")[0].id == answer.id


def test_pm_redacts_secret_values_and_escalates_rotation(tmp_path: Path) -> None:
    answer = answer_pm_question(
        manifest=_manifest(tmp_path),
        question="How do we rotate SECRET_API_KEY?",
        decision_store=DecisionStore(tmp_path / "decisions.json"),
        engineer_runs_store=EngineerRunStore(tmp_path / "runs.json"),
        agile_store=AgileStore(tmp_path / "agile.json"),
        activity_log_path=tmp_path / "activity.jsonl",
    )

    assert "SECRET_API_KEY" in answer.answer
    assert "sk-" not in answer.answer
    assert answer.escalated_to == "security_champion"
