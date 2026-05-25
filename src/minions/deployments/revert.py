"""File a risk=high Decision proposing rollback of a broken merge.

Called by the post-deploy orchestrator when a verification round
returns ``status == UNHEALTHY`` or ``FAILED``. Operator approves; the
existing engineer crew handles the actual revert PR via the standard
approval gate.

No auto-revert — the Decision is the operator-visible escalation. The
engineer can produce the revert patch via output_override (the body
includes the merge sha to revert), or the operator can apply manually.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from minions.approval.service import submit_for_approval
from minions.approval.store import DecisionStore
from minions.models.decision import Decision, DecisionType
from minions.models.deployment import DeploymentRecord
from minions.notify.base import Notifier

if TYPE_CHECKING:
    pass

KIND = "deploy_revert"


def file_revert_decision(
    *,
    record: DeploymentRecord,
    decision_store: DecisionStore,
    notifier: Notifier,
) -> Decision:
    """Submit a ``risk=high`` revert Decision keyed on the deployment record."""
    failed = [r for r in record.health_check_results if not r.ok]
    summary = (
        f"deploy revert: {record.project} prod broken after "
        f"sha {record.merge_sha[:8]}"
    )
    rationale = (
        f"Post-deploy verification ran {len(record.health_check_results)} "
        f"probe(s) against the {record.deploy_target} deployment of "
        f"{record.project} at {record.production_url}; "
        f"{len(failed)} failed. Operator should review the findings and "
        "either revert the merge or hotfix forward. The engineer crew can "
        "produce a revert PR via the standard approval gate."
    )
    body_lines = [
        f"## Post-deploy verification failed — {record.project}",
        "",
        f"- merge sha: `{record.merge_sha}`",
        f"- production URL: `{record.production_url}`",
        f"- target: `{record.deploy_target}`",
        f"- verified at: {record.verified_at}",
        f"- probes failed: {len(failed)}/{len(record.health_check_results)}",
        "",
        "### Findings",
        "",
        record.findings_md or "(no findings recorded)",
        "",
        "### Suggested operator action",
        "",
        "1. Click the failing URLs above to confirm the user-facing breakage.",
        "2. Either:",
        "   - revert the merge commit "
        f"(`git revert {record.merge_sha[:12]}` + open a PR), or",
        "   - approve a hotfix Decision the engineer crew opens via the "
        "normal planning flow.",
        "3. Add an entry to LESSONS_LEARNED.md in this project's repo so "
        "the discoverer surfaces this foot-gun on future runs.",
    ]
    decision = Decision(
        project=record.project,
        type=DecisionType.BUG,
        risk="high",
        summary=summary,
        rationale=rationale,
        diff_or_plan="\n".join(body_lines),
        proposer_role="cloud_devops",
        proposer_agent_id=f"post_deploy_verify@{record.project}",
        requested_by_role="cloud_devops",
        priority="p1",
        expedited=True,
        pr_url=None,
    )
    decision.__pydantic_extra__ = {
        "kind": KIND,
        "deployment_record_id": str(record.id),
        "merge_sha": record.merge_sha,
        "production_url": record.production_url,
    }
    submit_for_approval(decision, store=decision_store, notifier=notifier)
    return decision


def find_open_revert_decision(
    *, project: str, merge_sha: str, decision_store: DecisionStore
) -> Decision | None:
    """Dedupe: return any pending/approved revert Decision for this sha."""
    from minions.models.decision import DecisionStatus

    open_statuses = (DecisionStatus.PENDING, DecisionStatus.APPROVED)
    for d in decision_store.list_all():
        if d.project != project or d.status not in open_statuses:
            continue
        extras = getattr(d, "model_extra", None) or {}
        if extras.get("kind") != KIND:
            continue
        if extras.get("merge_sha") == merge_sha:
            return d
    return None


__all__ = ["KIND", "file_revert_decision", "find_open_revert_decision"]


def _now() -> datetime:
    return datetime.now(UTC)
