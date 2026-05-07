"""Audit runner — bridges PR sync outcomes to the Code Auditor crew.

Called by the daily monitor cron after ``sync_pr_status``: for each PR that
just transitioned to merged AND passes the deterministic sample gate AND
doesn't already have a finding for this PR, runs the Code Auditor and
writes the finding to the store.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from minions.audit.store import AuditFindingStore
from minions.crews.code_auditor import CodeAuditOutput, audit_pr, should_audit
from minions.crews.engineer_runs_store import EngineerRunStore

if TYPE_CHECKING:
    from minions.approval.store import DecisionStore
    from minions.config.portfolio import PortfolioConfig
    from minions.github.client import GitHubClient
    from minions.models.manifest import Manifest
    from minions.sync import SyncOutcome

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditRunOutcome:
    decision_id: str
    project: str
    sampled: bool  # did the sample gate let it through?
    audited: bool  # did we actually run the auditor?
    finding_id: str | None
    severity: str | None
    skipped_reason: str | None = None


@dataclass(frozen=True)
class AuditRunReport:
    outcomes: list[AuditRunOutcome]

    @property
    def audited(self) -> int:
        return sum(1 for o in self.outcomes if o.audited)

    @property
    def findings_by_severity(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for o in self.outcomes:
            if o.severity:
                out[o.severity] = out.get(o.severity, 0) + 1
        return out


def audit_after_sync(
    *,
    sync_outcomes: list[SyncOutcome],
    runs_store: EngineerRunStore,
    decision_store: DecisionStore,
    findings_store: AuditFindingStore,
    open_github_client: Callable[[Manifest], GitHubClient | None],
    manifests: dict[str, Manifest],
    api_key: str | None = None,
    portfolio: PortfolioConfig | None = None,
    output_override: CodeAuditOutput | None = None,  # test injection point
) -> AuditRunReport:
    """For each newly-merged PR in ``sync_outcomes``, maybe run the Code Auditor.

    No-ops when ``api_key`` is None and no override (dry-run / unconfigured).
    Per-PR failures are isolated — one bad audit doesn't kill the rest.
    """
    outcomes: list[AuditRunOutcome] = []
    clients: dict[str, GitHubClient | None] = {}

    for sync_o in sync_outcomes:
        # Only audit PRs that JUST transitioned to merged in this sync.
        if sync_o.after != "merged":
            continue
        if sync_o.before == "merged":
            continue  # was already merged; nothing newly merged
        decision_id = sync_o.decision_id

        record = runs_store.get(decision_id)
        if record is None or record.pr_url is None:
            outcomes.append(_skipped(decision_id, sync_o.project, "no run record"))
            continue
        if findings_store.has_finding_for_pr(record.pr_url):
            outcomes.append(_skipped(decision_id, sync_o.project, "already audited"))
            continue

        # Resolve the source decision so we know the risk level.
        try:
            decision = decision_store.get(decision_id)
        except Exception:  # noqa: BLE001
            decision = None
        if decision is None:
            outcomes.append(_skipped(decision_id, sync_o.project, "decision not found"))
            continue

        if not should_audit(decision_id, decision.risk):
            outcomes.append(
                AuditRunOutcome(
                    decision_id=decision_id,
                    project=sync_o.project,
                    sampled=False,
                    audited=False,
                    finding_id=None,
                    severity=None,
                    skipped_reason="not sampled",
                )
            )
            continue

        # Open a github client for the project if we don't already have one.
        if sync_o.project not in clients:
            manifest = manifests.get(sync_o.project)
            if manifest is None:
                outcomes.append(_skipped(decision_id, sync_o.project, "manifest not found"))
                continue
            try:
                clients[sync_o.project] = open_github_client(manifest)
            except Exception as e:  # noqa: BLE001
                clients[sync_o.project] = None
                logger.debug("audit: client open failed for %s: %s", sync_o.project, e)
        gh = clients[sync_o.project]
        if gh is None:
            outcomes.append(_skipped(decision_id, sync_o.project, "no GitHub client"))
            continue

        try:
            finding = audit_pr(
                record,
                decision,
                github=gh,
                api_key=api_key,
                portfolio=portfolio,
                output_override=output_override,
            )
        except Exception as e:  # noqa: BLE001 — audits must not crash the cron
            logger.warning("audit failed for %s: %s", decision_id, e)
            outcomes.append(_skipped(decision_id, sync_o.project, f"audit error: {e}"))
            continue

        if finding is None:
            outcomes.append(
                AuditRunOutcome(
                    decision_id=decision_id,
                    project=sync_o.project,
                    sampled=True,
                    audited=False,
                    finding_id=None,
                    severity=None,
                    skipped_reason="auditor returned None",
                )
            )
            continue

        findings_store.save(finding)
        outcomes.append(
            AuditRunOutcome(
                decision_id=decision_id,
                project=sync_o.project,
                sampled=True,
                audited=True,
                finding_id=str(finding.id),
                severity=finding.severity,
            )
        )

    # Best-effort close any clients we opened.
    for c in clients.values():
        if c is not None:
            try:
                c.close()
            except Exception:  # noqa: BLE001
                pass

    return AuditRunReport(outcomes=outcomes)


def _skipped(decision_id: str, project: str, reason: str) -> AuditRunOutcome:
    return AuditRunOutcome(
        decision_id=decision_id,
        project=project,
        sampled=False,
        audited=False,
        finding_id=None,
        severity=None,
        skipped_reason=reason,
    )
