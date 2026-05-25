"""Evidence assembly for spokesperson interviews."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from minions.activity import read_log
from minions.agile.store_factory import AgileStoreLike
from minions.approval.store_factory import DecisionStoreLike
from minions.cost import read_log as read_cost_log
from minions.crews.engineer_runs_store_factory import EngineerRunStoreLike
from minions.models.interview import InterviewCitation
from minions.models.manifest import Manifest
from minions.spokesperson.redaction import redact_secrets


@dataclass
class EvidencePacket:
    summary_lines: list[str] = field(default_factory=list)
    citations: list[InterviewCitation] = field(default_factory=list)

    def add(self, source_type: str, label: str, excerpt: str, reference: str | None = None) -> None:
        if not excerpt:
            return
        self.summary_lines.append(f"{label}: {excerpt}")
        self.citations.append(
            InterviewCitation(
                source_type=source_type,  # type: ignore[arg-type]
                label=label,
                reference=reference,
                excerpt=excerpt[:500],
            )
        )


def build_project_evidence(
    *,
    manifest: Manifest,
    decision_store: DecisionStoreLike,
    engineer_runs_store: EngineerRunStoreLike,
    agile_store: AgileStoreLike | None,
    activity_log_path: Path | None = None,
    cost_log_path: Path | None = None,
) -> EvidencePacket:
    packet = EvidencePacket()
    packet.add(
        "manifest",
        f"manifest:{manifest.name}",
        f"{manifest.description} Source repo={manifest.source.repo or 'unknown'} path={manifest.source.path or 'unknown'}.",
    )
    for label, text in _read_local_docs(manifest)[:4]:
        packet.add("readme" if label.lower().endswith("readme.md") else "docs", label, text)

    decisions = [
        d
        for d in decision_store.list_all()
        if d.project == manifest.name and "[DRY RUN]" not in d.summary
    ]
    for d in sorted(decisions, key=lambda item: item.created_at, reverse=True)[:5]:
        packet.add(
            "decision",
            f"decision:{str(d.id)[:8]}",
            f"{d.status.value}: {d.summary}",
            str(d.id),
        )

    runs = engineer_runs_store.list_by_project(manifest.name)
    for run in sorted(runs, key=lambda item: item.completed_at, reverse=True)[:5]:
        detail = run.pr_url or run.branch_name or run.skip_reason or "engineer run recorded"
        packet.add(
            "pull_request",
            f"engineer_run:{run.decision_id[:8]}",
            f"PR state={run.pr_state or 'unknown'} review={run.review_status}; {detail}",
            run.pr_url,
        )

    if agile_store is not None:
        for ritual in agile_store.list_rituals(manifest.name)[:5]:
            packet.add(
                "agile_ritual",
                f"{ritual.ritual}:{str(ritual.id)[:8]}",
                ritual.summary,
                str(ritual.id),
            )

    for entry in read_log(activity_log_path)[-30:]:
        if entry.project != manifest.name:
            continue
        packet.add(
            "activity",
            f"activity:{entry.event}",
            f"{entry.crew} {entry.event} with {', '.join(entry.agents) or 'crew'}",
            entry.run_id,
        )
    for entry in read_cost_log(cost_log_path)[-30:]:
        if entry.project != manifest.name:
            continue
        packet.add(
            "cost",
            f"cost:{entry.role or 'unknown'}",
            f"{entry.model} cost ${entry.cost_usd:.4f} for {entry.input_tokens + entry.output_tokens} tokens",
            entry.decision_id,
        )
    return packet


def build_org_evidence(
    *,
    decision_store: DecisionStoreLike,
    engineer_runs_store: EngineerRunStoreLike,
    agile_store: AgileStoreLike | None,
    activity_log_path: Path | None = None,
    cost_log_path: Path | None = None,
) -> EvidencePacket:
    packet = EvidencePacket()
    for d in sorted(decision_store.list_all(), key=lambda item: item.created_at, reverse=True)[:8]:
        packet.add(
            "decision",
            f"decision:{str(d.id)[:8]}",
            f"{d.project} {d.status.value}: {d.summary}",
            str(d.id),
        )
    for run in sorted(engineer_runs_store.list_all(), key=lambda item: item.completed_at, reverse=True)[:8]:
        packet.add(
            "pull_request",
            f"engineer_run:{run.decision_id[:8]}",
            f"{run.project} PR state={run.pr_state or 'unknown'} review={run.review_status}",
            run.pr_url,
        )
    if agile_store is not None:
        for ritual in agile_store.list_rituals(None)[:8]:
            packet.add(
                "agile_ritual",
                f"{ritual.project}:{ritual.ritual}",
                ritual.summary,
                str(ritual.id),
            )
    for entry in read_log(activity_log_path)[-50:]:
        packet.add(
            "activity",
            f"activity:{entry.project or 'org'}:{entry.event}",
            f"{entry.crew} {entry.event} with {', '.join(entry.agents) or 'crew'}",
            entry.run_id,
        )
    by_project: dict[str, float] = {}
    for entry in read_cost_log(cost_log_path)[-200:]:
        by_project[entry.project or "org"] = by_project.get(entry.project or "org", 0.0) + entry.cost_usd
    for project, cost in sorted(by_project.items(), key=lambda item: item[1], reverse=True)[:8]:
        packet.add("cost", f"cost:{project}", f"Recent logged LLM spend ${cost:.4f}", project)
    if not packet.summary_lines:
        packet.add("activity", "org:evidence", "No organization-wide evidence records were found.")
    return packet


def build_role_memory(
    *,
    role: str,
    project: str | None,
    decision_store: DecisionStoreLike,
    engineer_runs_store: EngineerRunStoreLike,
    interview_store,
    agile_store: AgileStoreLike | None = None,
    activity_log_path: Path | None = None,
    cost_log_path: Path | None = None,
) -> EvidencePacket:
    packet = EvidencePacket()
    role_key = role.lower()

    for d in sorted(decision_store.list_all(), key=lambda item: item.created_at, reverse=True):
        if project is not None and d.project != project:
            continue
        payload_role = d.proposer_role.lower()
        if role_key in payload_role or payload_role in role_key:
            packet.add(
                "role_memory",
                f"{role}:decision:{str(d.id)[:8]}",
                f"Proposed {d.summary} ({d.status.value})",
                str(d.id),
            )
        if len(packet.citations) >= 4:
            break

    runs = engineer_runs_store.list_by_project(project) if project else engineer_runs_store.list_all()
    for run in sorted(runs, key=lambda item: item.completed_at, reverse=True):
        if role_key in {"principal_engineer", "engineer", "qa", "team_architect"}:
            packet.add(
                "role_memory",
                f"{role}:run:{run.decision_id[:8]}",
                f"Worked around PR {run.pr_url or run.branch_name or run.decision_id}; "
                f"review={run.review_status}; files={', '.join(run.files_changed[:4]) or 'unknown'}",
                run.pr_url,
            )
        for reviewer in run.reviewers:
            if role_key in reviewer.role.lower() or reviewer.role.lower() in role_key:
                packet.add(
                    "role_memory",
                    f"{role}:review:{run.decision_id[:8]}",
                    f"{reviewer.display_name} {reviewer.status}: {reviewer.summary or reviewer.verdict or 'review recorded'}",
                    run.pr_url,
                )
        if len(packet.citations) >= 7:
            break

    for thread in interview_store.list_threads(project):
        for consultation in interview_store.list_consultations(thread.id):
            if consultation.consulted_role == role and consultation.note:
                packet.add(
                    "role_memory",
                    f"{role}:consultation:{str(consultation.id)[:8]}",
                    consultation.note,
                    str(consultation.id),
                )
                break
        if len(packet.citations) >= 9:
            break

    if agile_store is not None:
        for ritual in agile_store.list_rituals(project):
            ritual_text = " ".join(
                [ritual.summary, *ritual.blockers, *ritual.next_actions]
            ).lower()
            if role_key in ritual_text or role.replace("_", " ") in ritual_text:
                packet.add(
                    "role_memory",
                    f"{role}:agile:{ritual.ritual}",
                    ritual.summary,
                    str(ritual.id),
                )
            if len(packet.citations) >= 10:
                break

    for entry in read_log(activity_log_path)[-50:]:
        if project is not None and entry.project != project:
            continue
        if role in entry.agents:
            packet.add(
                "role_memory",
                f"{role}:activity:{entry.event}",
                f"{entry.crew} {entry.event}",
                entry.run_id,
            )
        if len(packet.citations) >= 10:
            break

    if role_key in {"cost_auditor", "cto", "managing_director"}:
        for entry in read_cost_log(cost_log_path)[-50:]:
            if project is not None and entry.project != project:
                continue
            packet.add(
                "role_memory",
                f"{role}:cost:{entry.project or 'org'}",
                f"{entry.model} logged ${entry.cost_usd:.4f}",
                entry.decision_id,
            )
            if len(packet.citations) >= 10:
                break

    if not packet.summary_lines:
        packet.add(
            "role_memory",
            f"{role}:memory",
            "No prior role-specific memory was found for this scope.",
        )
    return packet


def _read_local_docs(manifest: Manifest) -> list[tuple[str, str]]:
    if not manifest.source.path:
        return []
    root = Path(manifest.source.path).expanduser()
    if not root.is_dir():
        return []
    candidates = [root / "README.md", *sorted((root / "docs").glob("**/*.md"))[:4]]
    out: list[tuple[str, str]] = []
    for path in candidates:
        try:
            if path.is_file() and path.stat().st_size <= 80_000:
                rel = str(path.relative_to(root))
                out.append((rel, redact_secrets(path.read_text(errors="ignore")[:800])))
        except OSError:
            continue
    return out
