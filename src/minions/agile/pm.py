"""Product Manager spokesperson answers grounded in project records."""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from minions.activity import ActivityEntry, append
from minions.agile.store_factory import AgileStoreLike
from minions.approval.store_factory import DecisionStoreLike
from minions.crews.engineer_runs_store_factory import EngineerRunStoreLike
from minions.models.agile import PMAnswerRecord
from minions.models.manifest import Manifest

SECRET_NAME_RE = re.compile(r"(SECRET|TOKEN|API[_-]?KEY|PASSWORD|PAT|PRIVATE)", re.I)


def answer_pm_question(
    *,
    manifest: Manifest,
    question: str,
    decision_store: DecisionStoreLike,
    engineer_runs_store: EngineerRunStoreLike,
    agile_store: AgileStoreLike,
    activity_log_path: Path | None = None,
) -> PMAnswerRecord:
    """Answer as the project's PM without exposing secret values."""
    project = manifest.name
    q_lower = question.lower()
    docs = _docs_summary(manifest)
    decisions = [
        d for d in decision_store.list_all()
        if d.project == project and d.created_at >= datetime.now(tz=UTC) - timedelta(days=45)
    ][:6]
    runs = engineer_runs_store.list_by_project(project)[:6]
    rituals = agile_store.list_rituals(project)[:5]
    secret_names = _safe_secret_names(manifest, question)

    citations: list[str] = [f"project manifest:{project}"]
    citations += [f"decision:{str(d.id)[:8]}" for d in decisions[:3]]
    citations += [f"pr:{r.pr_url}" for r in runs[:3] if r.pr_url]
    citations += [f"ritual:{str(r.id)[:8]}:{r.ritual}" for r in rituals[:3]]

    if any(word in q_lower for word in ["secret", "password", "api key", "token", "rotate"]):
        answer = _secrets_answer(project, secret_names, runs, rituals)
        escalated_to = "security_champion"
    elif any(word in q_lower for word in ["tech", "stack", "framework", "language"]):
        answer = _tech_answer(project, manifest, docs)
        escalated_to = None
    elif any(word in q_lower for word in ["status", "sprint", "changed", "progress", "blocker"]):
        answer = _status_answer(project, decisions, runs, rituals)
        escalated_to = None
    else:
        answer = _functional_answer(project, manifest, docs, decisions)
        escalated_to = None

    record = PMAnswerRecord(
        project=project,
        question=question,
        answer=answer,
        citations=_dedupe(citations),
        escalated_to=escalated_to,
    )
    agile_store.save_pm_answer(record)
    append(
        ActivityEntry(
            timestamp=datetime.now(tz=UTC),
            event="pm_answered",
            run_id=f"pm-{project}-{uuid.uuid4().hex}",
            crew="pm_spokesperson",
            project=project,
            decision_id=str(record.id),
            agents=("product_manager",),
        ),
        path=activity_log_path,
    )
    return record


def _functional_answer(project: str, manifest: Manifest, docs: str, decisions) -> str:
    recent = "; ".join(d.summary for d in decisions[:3]) or "no recent Decisions found"
    return (
        f"{project} is described as: {manifest.description}. "
        f"Docs signal: {docs or 'no README/docs summary was available'}. "
        f"Recent product work: {recent}."
    )


def _tech_answer(project: str, manifest: Manifest, docs: str) -> str:
    stack = _infer_stack(manifest)
    return (
        f"{project}'s visible tech stack is {', '.join(stack) if stack else 'not explicit yet'}. "
        f"I inferred this from repository files and docs, not from secrets. "
        f"Docs signal: {docs or 'no README/docs summary was available'}."
    )


def _status_answer(project: str, decisions, runs, rituals) -> str:
    pending = sum(1 for d in decisions if d.status.value == "pending")
    approved = sum(1 for d in decisions if d.status.value == "approved")
    open_prs = [r for r in runs if r.pr_state in {None, "open"}]
    merged = [r for r in runs if r.pr_state == "merged"]
    blocker_bits = [
        b
        for ritual in rituals
        for b in ritual.blockers
    ][:3]
    blocker_text = (
        ", ".join(blocker_bits)
        if blocker_bits
        else "none recorded in recent rituals"
    )
    return (
        f"{project} status: {pending} pending Decision(s), {approved} approved "
        f"Decision(s), {len(open_prs)} open PR(s), {len(merged)} recently merged PR(s). "
        f"Current blockers: {blocker_text}."
    )


def _secrets_answer(project: str, secret_names: list[str], runs, rituals) -> str:
    recent_ops = "; ".join(r.summary for r in rituals if "rotation" in r.summary.lower())[:240]
    names = ", ".join(secret_names) if secret_names else "no explicit secret names discovered"
    return (
        f"For {project}, I can discuss secret identifiers and rotation process, "
        "but I will not reveal values. "
        f"Known/suspected secret names: {names}. "
        "Rotation should be handled by Security Champion or DevSecOps: create a "
        "new value in the provider, update the repo/runtime secret store, "
        "deploy, verify health checks, then revoke the old value. "
        f"Recent rotation notes: {recent_ops or 'none recorded'}."
    )


def _docs_summary(manifest: Manifest) -> str:
    root = _project_path(manifest)
    if root is None:
        return ""
    for name in ["README.md", "docs/README.md", "package.json", "pyproject.toml"]:
        path = root / name
        if path.exists() and path.is_file():
            text = path.read_text(errors="ignore").strip().replace("\n", " ")
            if text:
                return _redact(text[:300])
    return ""


def _infer_stack(manifest: Manifest) -> list[str]:
    root = _project_path(manifest)
    if root is None:
        return []
    stack: list[str] = []
    markers = {
        "Next.js/Node": ["package.json", "next.config.ts", "next.config.js"],
        "Python": ["pyproject.toml", "requirements.txt"],
        "AWS Lambda": ["template.yaml", "serverless.yml"],
        "Docker": ["Dockerfile", "compose.yml", "docker-compose.yml"],
    }
    for label, files in markers.items():
        if any((root / f).exists() for f in files):
            stack.append(label)
    return stack


def _project_path(manifest: Manifest) -> Path | None:
    if manifest.source.kind == "local" and manifest.source.path:
        return Path(manifest.source.path).expanduser()
    return None


def _safe_secret_names(manifest: Manifest, question: str = "") -> list[str]:
    names: set[str] = {k for k in os.environ if SECRET_NAME_RE.search(k)}
    secret_pattern = (
        r"\b[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|API_KEY|PAT|PASSWORD)[A-Z0-9_]*\b"
    )
    for token in re.findall(secret_pattern, question):
        names.add(token)
    root = _project_path(manifest)
    if root is not None:
        env_path = root / ".env"
        if env_path.exists():
            for line in env_path.read_text(errors="ignore").splitlines():
                key = line.split("=", 1)[0].strip()
                if key and SECRET_NAME_RE.search(key):
                    names.add(key)
    return sorted(names)[:20]


def _redact(text: str) -> str:
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "[REDACTED_SECRET]", text)
    return re.sub(
        r"(?i)(token|secret|password|api[_-]?key)\\s*=\\s*\\S+",
        r"\1=[REDACTED]",
        text,
    )


def _dedupe(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
