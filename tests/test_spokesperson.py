from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from minions.agile.store import AgileStore
from minions.approval.store import DecisionStore
from minions.crews.engineer_runs_store import EngineerRunRecord, EngineerRunStore
from minions.learning.store import AgentLearningStore
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.interview import (
    ConsultationRecord,
    InterviewMessageRecord,
    InterviewThreadRecord,
)
from minions.models.manifest import Manifest
from minions.spokesperson.code_scan import scan_codebase
from minions.spokesperson.redaction import redact_secrets
from minions.spokesperson.routing import classify_question, route_roles
from minions.spokesperson.service import ask_spokesperson
from minions.spokesperson.store import InterviewStore


def _manifest(tmp_path: Path, *, local: bool = True) -> Manifest:
    project = tmp_path / "alpha"
    if local:
        project.mkdir()
        (project / "README.md").write_text(
            "# Alpha\nDeployed on Fly using FLY_API_TOKEN=supersecretvalue.\n"
        )
        (project / "fly.toml").write_text('app = "alpha"\n')
    return Manifest.model_validate(
        {
            "name": "alpha",
            "description": "Alpha manages profile data.",
            "source": {
                "kind": "local" if local else "github",
                "path": str(project) if local else None,
                "repo": "org/alpha",
                "default_branch": "main",
            },
            "weekly_budget_usd": 1.0,
            "monthly_budget_usd": 4.0,
            "owner": "owner@example.com",
        }
    )


def test_json_store_round_trips_interview_records(tmp_path: Path) -> None:
    store = InterviewStore(tmp_path / "interviews.json")
    thread = store.save_thread(
        InterviewThreadRecord(
            scope="project",
            project="alpha",
            spokesperson_role="cto",
            title="Where is this deployed?",
        )
    )
    message = store.save_message(
        InterviewMessageRecord(
            thread_id=thread.id,
            role="operator",
            agent_role="operator",
            content="Where is this deployed?",
        )
    )
    consultation = store.save_consultation(
        ConsultationRecord(
            thread_id=thread.id,
            message_id=message.id,
            project="alpha",
            consulted_role="cloud_devops",
            status="queued",
        )
    )

    consultation.status = "gathering_memory"
    store.save_consultation(consultation)
    consultation.status = "scanning_code"
    store.save_consultation(consultation)
    consultation.status = "answered"
    consultation.note = "Fly config was inspected."
    store.save_consultation(consultation)

    assert store.get_thread(thread.id) == thread
    assert store.list_messages(thread.id)[0].content == "Where is this deployed?"
    assert [c.status for c in store.list_consultations(thread.id)] == ["answered"]


def test_routing_matches_deployment_and_functional_questions() -> None:
    assert classify_question("Where is this deployed?") == "deployment"
    assert route_roles("deployment", spokesperson_role="cto") == [
        "cto",
        "cloud_devops",
        "principal_engineer",
    ]
    assert route_roles("functional", spokesperson_role="product_manager") == [
        "product_manager",
        "manager",
    ]


def test_ask_spokesperson_uses_role_memory_scans_code_and_redacts(tmp_path: Path) -> None:
    decisions = DecisionStore(tmp_path / "decisions.json")
    decisions.save(
        Decision(
            project="alpha",
            type=DecisionType.INFRA,
            summary="Document Fly deployment",
            rationale="Operators need deployment evidence.",
            proposer_role="cloud_devops",
            proposer_agent_id="cloud_devops@alpha",
            status=DecisionStatus.APPROVED,
        )
    )
    runs = EngineerRunStore(tmp_path / "runs.json")
    runs.update(
        EngineerRunRecord(
            decision_id="d1",
            project="alpha",
            completed_at=datetime.now(UTC),
            pr_url="https://github.com/org/alpha/pull/1",
            files_changed=["fly.toml"],
        )
    )
    store = InterviewStore(tmp_path / "interviews.json")
    result = ask_spokesperson(
        spokesperson_role="cto",
        question="Where is this deployed and what token rotates it?",
        project="alpha",
        interview_store=store,
        decision_store=decisions,
        engineer_runs_store=runs,
        agile_store=AgileStore(tmp_path / "agile.json"),
        manifests={"alpha": _manifest(tmp_path)},
        activity_log_path=tmp_path / "activity.jsonl",
    )

    assert result.answer_message.consulted_roles == ["cto", "cloud_devops", "principal_engineer"]
    assert result.answer_message.confidence == "medium"
    assert "supersecretvalue" not in result.answer_message.content
    assert any(c.files_inspected for c in result.consultations)
    cloud = next(c for c in result.consultations if c.consulted_role == "cloud_devops")
    assert "Document Fly deployment" in (cloud.memory_summary or "")
    learnings = AgentLearningStore(tmp_path / "agent_learning.json").list_all()
    assert any(learning.source_type == "spokesperson_interview" for learning in learnings)
    assert any(learning.kind == "ops" for learning in learnings)


def test_low_confidence_answer_creates_pending_task(tmp_path: Path) -> None:
    result = ask_spokesperson(
        spokesperson_role="cto",
        question="Where is this deployed?",
        project="alpha",
        interview_store=InterviewStore(tmp_path / "interviews.json"),
        decision_store=DecisionStore(tmp_path / "decisions.json"),
        engineer_runs_store=EngineerRunStore(tmp_path / "runs.json"),
        agile_store=None,
        manifests={"alpha": _manifest(tmp_path, local=False)},
        activity_log_path=tmp_path / "activity.jsonl",
    )

    assert result.answer_message.confidence == "low"
    assert result.task is not None
    assert result.task.owner_role == "cloud_devops"
    assert result.task.status == "pending"


def test_github_fallback_scan_redacts_secret_values(tmp_path: Path) -> None:
    class FakeContentsClient:
        def list_files(self, *, branch: str) -> list[str]:
            return ["README.md", ".env", "deploy/render.yaml"]

        def get_text_file(self, *, path: str, branch: str) -> str | None:
            return {
                "README.md": "Deploys on Render.",
                "deploy/render.yaml": "services:\n  env: API_KEY=rendersecretvalue",
            }.get(path)

    scan = scan_codebase(
        manifest=_manifest(tmp_path, local=False),
        question="Where is deployment configured?",
        github_client=FakeContentsClient(),
    )

    assert scan.confidence == "medium"
    assert ".env" not in scan.files_inspected
    assert "rendersecretvalue" not in scan.summary
    assert any(c.reference == "github:org/alpha:deploy/render.yaml" for c in scan.citations)


def test_redaction_handles_common_secret_shapes() -> None:
    text = "token=abc1234567890 password: letmein12345 sk-ant-abcdefghijklmnopqrstuvwxyz"
    redacted = redact_secrets(text)
    assert "abc1234567890" not in redacted
    assert "letmein12345" not in redacted
    assert "sk-ant-" not in redacted
