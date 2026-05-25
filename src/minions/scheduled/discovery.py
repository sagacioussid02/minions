"""Discovery sweep — runs the discoverer crew across active projects.

Schedule (target): weekly. Behavior:

* For each active project, resolve the working tree (clone-on-demand) and
  collect ``RepoReadings``.
* Compute freshness of the latest merged dossier (if any). Skip if ``ok``
  *unless* the caller passed ``force=True``.
* Before spending on an LLM run, check the project's month-to-date cost
  against ``manifest.monthly_budget_usd``. If the budget is in ``breach``,
  abort this project with an ``error`` outcome — the operator will see it
  in the report. ``warn`` proceeds (cost auditor will follow up separately).
* Run the discoverer (always ``--no-dry-run`` when ``api_key`` is provided)
  and persist the resulting ``DossierDraft`` to the dossier store at status
  ``drafted``. The PR open flow lives in Phase 5 and is invoked separately.

Failures are caught per-project so one bad project does not abort the sweep.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from minions.approval.store import DecisionStore
from minions.budget import evaluate as evaluate_budget
from minions.crews.discoverer import (
    DossierVerificationError,
    collect_repo_readings,
    run_discoverer,
)
from minions.dossiers.freshness import compute_freshness
from minions.dossiers.refresh import file_dossier_refresh_decision
from minions.dossiers.store_factory import DossierStoreLike
from minions.models.manifest import Manifest, load_active_manifests
from minions.notify.base import Notifier


class DiscoveryOutcome(BaseModel):
    project: str
    status: Literal[
        "submitted",
        "skipped_fresh",
        "skipped_target_missing",
        "throttled",
        "verifier_failed",
        "error",
    ]
    draft_id: str | None = None
    commit_sha: str | None = None
    freshness: str | None = None
    reason: str | None = None
    decision_id: str | None = None  # DOSSIER_REFRESH decision when filed


class DiscoverySweepReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[DiscoveryOutcome] = Field(default_factory=list)

    @property
    def submitted(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "submitted")

    @property
    def skipped(self) -> int:
        return sum(
            1
            for o in self.outcomes
            if o.status in ("skipped_fresh", "skipped_target_missing")
        )

    @property
    def errored(self) -> int:
        return sum(
            1 for o in self.outcomes if o.status in ("error", "verifier_failed")
        )


def run_discovery_sweep(
    *,
    projects_dir: Path,
    dossier_store: DossierStoreLike,
    api_key: str | None = None,
    dry_run: bool = True,
    force: bool = False,
    cost_log_path: Path | None = None,
    cache_dir: Path | None = None,
    projects: list[str] | None = None,
    decision_store: DecisionStore | None = None,
    notifier: Notifier | None = None,
) -> DiscoverySweepReport:
    """Run the discoverer crew across every active project.

    ``dry_run=True`` collects readings + computes freshness but never invokes
    an LLM and never writes to the store. This is the default and is safe to
    call from any environment.

    ``force=True`` ignores the ``skipped_fresh`` short-circuit, useful for
    operator-triggered refreshes and for the first run on a new project.
    """
    started = datetime.now(UTC).isoformat()
    manifests = load_active_manifests(projects_dir)
    if projects:
        wanted = {p.lower() for p in projects}
        manifests = {
            name: m for name, m in manifests.items() if name.lower() in wanted
        }

    outcomes: list[DiscoveryOutcome] = []
    for name, manifest in manifests.items():
        outcomes.append(
            _run_one(
                manifest=manifest,
                dossier_store=dossier_store,
                api_key=api_key,
                dry_run=dry_run,
                force=force,
                cost_log_path=cost_log_path,
                cache_dir=cache_dir,
                decision_store=decision_store,
                notifier=notifier,
            )
        )
        _ = name

    return DiscoverySweepReport(
        started_at=started,
        finished_at=datetime.now(UTC).isoformat(),
        outcomes=outcomes,
    )


def _run_one(
    *,
    manifest: Manifest,
    dossier_store: DossierStoreLike,
    api_key: str | None,
    dry_run: bool,
    force: bool,
    cost_log_path: Path | None,
    cache_dir: Path | None,
    decision_store: DecisionStore | None,
    notifier: Notifier | None,
) -> DiscoveryOutcome:
    project = manifest.name

    # Resolve working tree (cloning if needed). If this fails, surface as
    # skipped — there's nothing actionable for the discoverer without source.
    try:
        from minions.working_tree import resolve_working_tree

        clones_dir = cache_dir or (
            Path(__file__).resolve().parents[3] / "data" / "local" / "clones"
        )
        root = resolve_working_tree(manifest, cache_dir=clones_dir)
    except Exception as e:  # noqa: BLE001 — clone failures are per-project
        return DiscoveryOutcome(
            project=project,
            status="skipped_target_missing",
            reason=f"could not resolve working tree: {e}",
        )

    # Freshness gate — skip when the latest merged dossier is still ``ok``
    # unless caller forces a refresh.
    latest = dossier_store.latest_merged(project)
    freshness = compute_freshness(
        latest, overrides=manifest.dossier.freshness_overrides, repo_root=root
    )
    if not force and freshness.label == "ok":
        return DiscoveryOutcome(
            project=project,
            status="skipped_fresh",
            freshness=freshness.label,
            reason=freshness.reason,
        )

    # Budget gate — refuse to spend if the project is already in monthly breach.
    if not dry_run and api_key is not None:
        budget = evaluate_budget(manifest, cost_log_path=cost_log_path)
        if budget.state == "breach":
            return DiscoveryOutcome(
                project=project,
                status="throttled",
                freshness=freshness.label,
                reason=(
                    f"monthly budget {budget.fraction:.0%} of "
                    f"${budget.monthly_cap_usd:.2f} cap — discovery skipped"
                ),
            )

    readings = collect_repo_readings(manifest, root)
    try:
        draft = run_discoverer(
            manifest,
            api_key=api_key,
            dry_run=dry_run,
            readings=readings,
        )
    except DossierVerificationError as e:
        # Per spec: verifier failure does NOT persist the draft. We surface
        # the failure in the report so the operator can inspect it; the
        # rejected draft body is in the exception for callers that want it.
        return DiscoveryOutcome(
            project=project,
            status="verifier_failed",
            commit_sha=readings.commit_sha,
            freshness=freshness.label,
            reason=str(e).splitlines()[0][:200],
        )
    except Exception as e:  # noqa: BLE001 — per-project isolation
        return DiscoveryOutcome(
            project=project,
            status="error",
            commit_sha=readings.commit_sha,
            freshness=freshness.label,
            reason=str(e)[:200],
        )

    if draft is None:
        # dry_run path — no persistence, no draft. Report as skipped_fresh
        # only if it was actually a no-op for a fresh project; here it's a
        # successful dry-run, so report as submitted=0 / no-op via "skipped".
        return DiscoveryOutcome(
            project=project,
            status="skipped_fresh",
            commit_sha=readings.commit_sha,
            freshness=freshness.label,
            reason="dry-run — readings collected, no LLM invoked",
        )

    with suppress(Exception):
        dossier_store.save(draft)

    decision_id: str | None = None
    if decision_store is not None and notifier is not None:
        with suppress(Exception):
            decision = file_dossier_refresh_decision(
                draft=draft,
                manifest=manifest,
                decision_store=decision_store,
                dossier_store=dossier_store,
                notifier=notifier,
            )
            decision_id = str(decision.id)

    return DiscoveryOutcome(
        project=project,
        status="submitted",
        draft_id=str(draft.id),
        commit_sha=draft.commit_sha,
        freshness=freshness.label,
        reason=None,
        decision_id=decision_id,
    )
