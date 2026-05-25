"""Capture compact candidate learnings from durable operating records."""

from __future__ import annotations

import os
from collections.abc import Iterable

from minions.crews.engineer_runs_store import EngineerRunRecord
from minions.learning.store_factory import AgentLearningStoreLike
from minions.models.agile import AgileRitualRecord, PMAnswerRecord
from minions.models.deployment import DeploymentRecord
from minions.models.interview import InterviewMessageRecord
from minions.models.learning import AgentLearningRecord, LearningConfidence, LearningKind

MAX_FACT_CHARS = 420


def capture_enabled() -> bool:
    return os.environ.get("MINIONS_AGENT_LEARNING_CAPTURE", "1").lower() not in {
        "0",
        "false",
        "no",
    }


def capture_pm_answer(
    record: PMAnswerRecord,
    store: AgentLearningStoreLike,
) -> list[AgentLearningRecord]:
    if not capture_enabled():
        return []
    kind: LearningKind = "risk" if record.escalated_to else "product"
    fact = _compact(
        f"{record.project} answered owner question '{record.question}': {record.answer}"
    )
    learning = AgentLearningRecord(
        agent_id=f"product_manager@{record.project}",
        role="product_manager",
        project=record.project,
        kind=kind,
        fact=fact,
        source_type="pm_answer",
        source_id=str(record.id),
        confidence="medium",
    )
    return _save_unique(store, [learning])


def capture_ritual(
    record: AgileRitualRecord,
    store: AgentLearningStoreLike,
) -> list[AgentLearningRecord]:
    if not capture_enabled():
        return []
    kind: LearningKind = "risk" if record.blockers else "process"
    fact = _compact(
        " ".join(
            [
                f"{record.project} {record.ritual} recorded: {record.summary}",
                _join_prefix("Blockers", record.blockers),
                _join_prefix("Next", record.next_actions),
            ]
        )
    )
    learning = AgentLearningRecord(
        agent_id=f"manager@{record.project}",
        role="manager",
        project=record.project,
        kind=kind,
        fact=fact,
        source_type="agile_ritual",
        source_id=str(record.id),
        confidence="medium",
    )
    return _save_unique(store, [learning])


def capture_engineer_run(
    record: EngineerRunRecord,
    store: AgentLearningStoreLike,
) -> list[AgentLearningRecord]:
    if not capture_enabled() or record.dry_run or record.skipped:
        return []
    facts: list[AgentLearningRecord] = []
    if record.files_changed:
        facts.append(
            AgentLearningRecord(
                agent_id=f"engineer@{record.project}",
                role="engineer",
                project=record.project,
                kind="technical",
                fact=_compact(
                    f"{record.project} engineering changed "
                    f"{', '.join(record.files_changed[:8])}"
                    f"{_pr_suffix(record)}."
                ),
                source_type="engineer_run",
                source_id=record.decision_id,
                confidence=_confidence(record),
            )
        )
    if record.review_status not in {"not_started", "assigned", "reviewing"}:
        facts.append(
            AgentLearningRecord(
                agent_id=f"principal_engineer@{record.project}",
                role="principal_engineer",
                project=record.project,
                kind="process",
                fact=_compact(
                    f"{record.project} PR review for decision {record.decision_id} "
                    f"is {record.review_status}{_review_suffix(record)}."
                ),
                source_type="pr_review",
                source_id=record.decision_id,
                confidence=_confidence(record),
            )
        )
    if record.merge_blocked_reason:
        facts.append(
            AgentLearningRecord(
                agent_id=f"principal_engineer@{record.project}",
                role="principal_engineer",
                project=record.project,
                kind="risk",
                fact=_compact(
                    f"{record.project} merge was blocked for decision "
                    f"{record.decision_id}: {record.merge_blocked_reason}"
                ),
                source_type="pr_review",
                source_id=record.decision_id,
                confidence="high",
            )
        )
    return _save_unique(store, facts)


def capture_interview_answer(
    *,
    question: str,
    answer: InterviewMessageRecord,
    project: str | None,
    spokesperson_role: str,
    store: AgentLearningStoreLike,
) -> list[AgentLearningRecord]:
    if not capture_enabled():
        return []
    kind = _kind_from_question(question)
    scope = project or "organization"
    learning = AgentLearningRecord(
        agent_id=f"{spokesperson_role}@{scope}",
        role=spokesperson_role,
        project=project,
        kind=kind,
        fact=_compact(
            f"{spokesperson_role} answered owner question '{question}' "
            f"for {scope}: {answer.content}"
        ),
        source_type="spokesperson_interview",
        source_id=str(answer.id),
        confidence=_message_confidence(answer),
    )
    return _save_unique(store, [learning])


def capture_deploy_outcome(
    *,
    record: DeploymentRecord,
    healthy: bool,
    store: AgentLearningStoreLike,
) -> list[AgentLearningRecord]:
    """Tag deploy outcomes for the executive layer.

    Failures are CTO learnings (kind=risk, high confidence): the dossier
    should reflect that this sha broke prod so future planning avoids
    similar patterns. Successes are CEO learnings (kind=ops, medium): a
    rolling signal of portfolio stability.
    """
    if not capture_enabled():
        return []
    project = record.project
    sha = record.merge_sha[:12]
    if healthy:
        learning = AgentLearningRecord(
            agent_id=f"ceo@{project}",
            role="ceo",
            project=project,
            kind="ops",
            fact=_compact(
                f"{project} deploy {sha} verified HEALTHY across "
                f"{len(record.health_check_results)} probes."
            ),
            source_type="deploy_outcome",
            source_id=str(record.id),
            confidence="medium",
        )
    else:
        failed_urls = [r.url for r in record.health_check_results if not r.ok][:4]
        learning = AgentLearningRecord(
            agent_id=f"cto@{project}",
            role="cto",
            project=project,
            kind="risk",
            fact=_compact(
                f"{project} deploy {sha} UNHEALTHY ({record.failed_count} of "
                f"{len(record.health_check_results)} probes failed); "
                f"failing URLs: {', '.join(failed_urls) or 'n/a'}. "
                f"Notes: {record.findings_md or 'see deployment record'}."
            ),
            source_type="deploy_outcome",
            source_id=str(record.id),
            confidence="high",
        )
    return _save_unique(store, [learning])


def _save_unique(
    store: AgentLearningStoreLike,
    records: Iterable[AgentLearningRecord],
) -> list[AgentLearningRecord]:
    existing = {
        (
            record.source_type,
            record.source_id,
            record.role,
            record.kind,
            record.fact,
        )
        for record in store.list_all(include_inactive=True)
    }
    saved: list[AgentLearningRecord] = []
    for record in records:
        key = (
            record.source_type,
            record.source_id,
            record.role,
            record.kind,
            record.fact,
        )
        if key in existing:
            continue
        saved.append(store.save(record))
        existing.add(key)
    return saved


def _compact(text: str) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= MAX_FACT_CHARS:
        return normalized
    return normalized[: MAX_FACT_CHARS - 1].rstrip() + "..."


def _join_prefix(prefix: str, values: list[str]) -> str:
    return f"{prefix}: {'; '.join(values[:4])}." if values else ""


def _pr_suffix(record: EngineerRunRecord) -> str:
    if record.pr_url:
        return f" in {record.pr_url}"
    if record.pr_number is not None:
        return f" in PR #{record.pr_number}"
    return ""


def _review_suffix(record: EngineerRunRecord) -> str:
    if record.superseded_by_pr_url:
        return f"; superseded by {record.superseded_by_pr_url}"
    if record.merge_blocked_reason:
        return f"; blocked because {record.merge_blocked_reason}"
    summaries = [r.summary for r in record.reviewers if r.summary]
    if summaries:
        return f"; review notes: {'; '.join(summaries[:2])}"
    return ""


def _confidence(record: EngineerRunRecord) -> LearningConfidence:
    if record.pr_state == "merged" or record.review_status in {"crew_approved", "merged"}:
        return "high"
    if record.review_status in {"changes_requested", "merge_blocked", "conflict_queued"}:
        return "medium"
    return "low"


def _message_confidence(answer: InterviewMessageRecord) -> LearningConfidence:
    if answer.confidence == "unknown":
        return "low"
    return answer.confidence


def _kind_from_question(question: str) -> LearningKind:
    q = question.lower()
    if any(token in q for token in ["deploy", "hosting", "runtime", "infra", "ci", "cd"]):
        return "ops"
    if any(token in q for token in ["architecture", "code", "repo", "technical", "stack"]):
        return "technical"
    if any(token in q for token in ["risk", "security", "secret", "token", "password"]):
        return "risk"
    if any(token in q for token in ["process", "sprint", "meeting", "scrum", "review"]):
        return "process"
    return "product"
