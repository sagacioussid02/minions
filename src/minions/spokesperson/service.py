"""Spokesperson interview orchestration."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from minions.activity import ActivityEntry, append
from minions.agile.store_factory import AgileStoreLike
from minions.approval.store_factory import DecisionStoreLike
from minions.crews.engineer_runs_store_factory import EngineerRunStoreLike
from minions.models.interview import (
    Confidence,
    ConsultationRecord,
    InterviewMessageRecord,
    InterviewTaskProposal,
    InterviewThreadRecord,
)
from minions.models.manifest import Manifest
from minions.spokesperson.code_scan import scan_codebase
from minions.spokesperson.evidence import (
    build_org_evidence,
    build_project_evidence,
    build_role_memory,
)
from minions.spokesperson.redaction import redact_secrets
from minions.spokesperson.routing import classify_question, normalize_role, route_roles
from minions.spokesperson.store_factory import InterviewStoreLike


@dataclass
class SpokespersonAnswer:
    thread: InterviewThreadRecord
    operator_message: InterviewMessageRecord
    answer_message: InterviewMessageRecord
    consultations: list[ConsultationRecord]
    task: InterviewTaskProposal | None = None


def ask_spokesperson(
    *,
    spokesperson_role: str,
    question: str,
    interview_store: InterviewStoreLike,
    decision_store: DecisionStoreLike,
    engineer_runs_store: EngineerRunStoreLike,
    manifests: dict[str, Manifest],
    agile_store: AgileStoreLike | None = None,
    project: str | None = None,
    thread_id: str | None = None,
    activity_log_path: Path | None = None,
    cost_log_path: Path | None = None,
) -> SpokespersonAnswer:
    normalized_spokesperson = normalize_role(spokesperson_role)
    manifest = _resolve_manifest(project, manifests) if project else None
    scope = "project" if manifest else "organization"
    thread = _get_or_create_thread(
        interview_store=interview_store,
        thread_id=thread_id,
        project=manifest.name if manifest else None,
        scope=scope,
        spokesperson_role=normalized_spokesperson,
        question=question,
    )
    operator_message = interview_store.save_message(
        InterviewMessageRecord(
            thread_id=thread.id,
            role="operator",
            agent_role="operator",
            content=redact_secrets(question),
            confidence="high",
        )
    )

    kind = classify_question(question)
    consulted_roles = route_roles(kind, spokesperson_role=normalized_spokesperson)
    evidence = (
        build_project_evidence(
            manifest=manifest,
            decision_store=decision_store,
            engineer_runs_store=engineer_runs_store,
            agile_store=agile_store,
            activity_log_path=activity_log_path,
            cost_log_path=cost_log_path,
        )
        if manifest is not None
        else build_org_evidence(
            decision_store=decision_store,
            engineer_runs_store=engineer_runs_store,
            agile_store=agile_store,
            activity_log_path=activity_log_path,
            cost_log_path=cost_log_path,
        )
    )

    consultations: list[ConsultationRecord] = []
    for role in consulted_roles:
        consultation = ConsultationRecord(
            thread_id=thread.id,
            message_id=operator_message.id,
            project=manifest.name if manifest else None,
            consulted_role=role,
            status="gathering_memory",
        )
        consultation = interview_store.save_consultation(consultation)
        memory = build_role_memory(
            role=role,
            project=manifest.name if manifest else None,
            decision_store=decision_store,
            engineer_runs_store=engineer_runs_store,
            interview_store=interview_store,
            agile_store=agile_store,
            activity_log_path=activity_log_path,
            cost_log_path=cost_log_path,
        )
        consultation.memory_summary = "\n".join(memory.summary_lines[:6])
        consultation.citations.extend(memory.citations[:8])

        if manifest is not None and kind in {"technical", "deployment", "security"}:
            consultation.status = "scanning_code"
            consultation = interview_store.save_consultation(consultation)
            scan = scan_codebase(manifest=manifest, question=question)
            consultation.code_scan_summary = scan.summary
            consultation.files_inspected = scan.files_inspected
            consultation.citations.extend(scan.citations[:8])
            consultation.confidence = scan.confidence if scan.citations else "low"  # type: ignore[assignment]
        else:
            consultation.confidence = "medium" if memory.citations else "low"

        consultation.note = _consultation_note(
            role=role,
            question=question,
            memory_summary=consultation.memory_summary,
            code_scan_summary=consultation.code_scan_summary,
        )
        consultation.note = redact_secrets(consultation.note)
        consultation.status = "answered" if consultation.note else "blocked"
        consultation.updated_at = datetime.now(UTC)
        consultation = interview_store.save_consultation(consultation)
        consultations.append(consultation)

    answer_text, confidence, follow_ups = _synthesize_answer(
        spokesperson_role=normalized_spokesperson,
        question=question,
        project=manifest.name if manifest else None,
        kind=kind,
        project_summary="\n".join(evidence.summary_lines[:8]),
        consultations=consultations,
    )
    citations = []
    citations.extend(evidence.citations[:8])
    for consultation in consultations:
        citations.extend(consultation.citations[:4])

    task: InterviewTaskProposal | None = None
    if confidence in {"low", "unknown"} and follow_ups:
        task = interview_store.save_task(
            InterviewTaskProposal(
                thread_id=thread.id,
                message_id=operator_message.id,
                project=manifest.name if manifest else None,
                owner_role=_owner_for_kind(kind),
                title=follow_ups[0],
                rationale=f"Spokesperson could not answer confidently: {question}",
            )
        )

    answer = InterviewMessageRecord(
        thread_id=thread.id,
        role="spokesperson",
        agent_role=normalized_spokesperson,
        content=redact_secrets(answer_text),
        citations=citations[:16],
        consulted_roles=consulted_roles,
        confidence=cast(Confidence, confidence),
        follow_up_actions=follow_ups,
        task_proposal_id=task.id if task else None,
    )
    answer = interview_store.save_message(answer)
    with suppress(Exception):
        from minions.learning.capture import capture_interview_answer
        from minions.learning.store import AgentLearningStore
        from minions.learning.store_postgres import PostgresAgentLearningStore

        learning_store = (
            AgentLearningStore(interview_store.path.parent / "agent_learning.json")
            if hasattr(interview_store, "path")
            else PostgresAgentLearningStore()
        )
        capture_interview_answer(
            question=question,
            answer=answer,
            project=manifest.name if manifest else None,
            spokesperson_role=normalized_spokesperson,
            store=learning_store,
        )
    if task is not None:
        task.message_id = answer.id
        task = interview_store.save_task(task)

    thread.updated_at = datetime.now(UTC)
    interview_store.save_thread(thread)

    append(
        ActivityEntry(
            timestamp=datetime.now(UTC),
            event="spokesperson_answered",
            run_id=f"spokesperson-{thread.id}",
            crew="spokesperson",
            project=manifest.name if manifest else "",
            decision_id=str(answer.id),
            agents=tuple(consulted_roles),
        ),
        path=activity_log_path,
    )
    return SpokespersonAnswer(
        thread=thread,
        operator_message=operator_message,
        answer_message=answer,
        consultations=consultations,
        task=task,
    )


def _resolve_manifest(project: str | None, manifests: dict[str, Manifest]) -> Manifest:
    if project is None:
        raise KeyError("project is required")
    for name, manifest in manifests.items():
        if name.lower() == project.lower():
            return manifest
    raise KeyError(f"unknown project {project!r}")


def _get_or_create_thread(
    *,
    interview_store: InterviewStoreLike,
    thread_id: str | None,
    project: str | None,
    scope: str,
    spokesperson_role: str,
    question: str,
) -> InterviewThreadRecord:
    if thread_id:
        existing = interview_store.get_thread(thread_id)
        if existing is None:
            raise KeyError(f"unknown interview thread {thread_id!r}")
        return existing
    title = question.strip().replace("\n", " ")[:80] or "Spokesperson interview"
    return interview_store.save_thread(
        InterviewThreadRecord(
            scope=scope,  # type: ignore[arg-type]
            project=project,
            spokesperson_role=spokesperson_role,
            title=title,
        )
    )


def _consultation_note(
    *,
    role: str,
    question: str,
    memory_summary: str | None,
    code_scan_summary: str | None,
) -> str:
    pieces = [f"{role} reviewed the question: {question}."]
    if memory_summary:
        pieces.append(f"Role memory: {memory_summary}")
    if code_scan_summary:
        pieces.append(f"Code scan: {code_scan_summary}")
    if not memory_summary and not code_scan_summary:
        pieces.append("No evidence was available for this role.")
    return " ".join(pieces)


def _synthesize_answer(
    *,
    spokesperson_role: str,
    question: str,
    project: str | None,
    kind: str,
    project_summary: str,
    consultations: list[ConsultationRecord],
) -> tuple[str, str, list[str]]:
    has_strong_evidence = any(c.code_scan_summary or c.memory_summary for c in consultations)
    has_code_findings = any(c.files_inspected for c in consultations)
    confidence = "medium" if has_strong_evidence else "low"
    if kind in {"deployment", "security", "technical"} and not has_code_findings:
        confidence = "low"
    scope = f"for {project}" if project else "for the organization"

    lines = [
        f"{spokesperson_role} answer {scope}: I routed this as a {kind} question.",
        f"Question: {question}",
    ]
    if project_summary:
        lines.append(f"Project evidence: {project_summary}")
    for consultation in consultations:
        if consultation.note:
            lines.append(f"{consultation.consulted_role}: {consultation.note}")

    follow_ups: list[str] = []
    if confidence == "low":
        owner = _owner_for_kind(kind)
        target = project or "portfolio"
        follow_ups.append(f"Create discovery task for {owner}: answer '{question}' for {target}")
        lines.append(
            "I do not have enough verified evidence to call this complete. "
            f"I recommend a follow-up owned by {owner}."
        )
    else:
        lines.append("This answer is grounded in stored memory and inspected evidence above.")
    return redact_secrets("\n".join(lines)), confidence, follow_ups


def _owner_for_kind(kind: str) -> str:
    return {
        "deployment": "cloud_devops",
        "technical": "principal_engineer",
        "security": "security_champion",
        "cost": "cost_auditor",
        "portfolio": "cto",
        "functional": "manager",
    }.get(kind, "product_manager")
