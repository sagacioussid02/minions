"""Auto-execute-approved sweep — runs the engineer crew on approved Decisions.

Schedule: every 6h (per `.github/workflows/execute_approved.yml`).
Outcome: each approved Decision that has not yet been executed gets an
engineer-crew run, opening a draft PR; Decision is mutated to EXECUTED and
the EngineerRunRecord is persisted.

Design rules mirror the other `scheduled/*.py` modules:
  * No CLI imports inside the entrypoint; CLI helpers (e.g. ``_open_github_client``)
    are injected.
  * Per-decision try/except — one bad project does not abort the sweep.
  * Hard cap on `max_runs` per invocation so cron never blasts the whole queue.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.approval.store import DecisionStore
from minions.budget import BudgetBreachError
from minions.crews.engineer import EngineerResult, run_engineer_crew
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.models.decision import Decision, DecisionStatus
from minions.models.manifest import Manifest, load_active_manifests

if TYPE_CHECKING:
    from minions.github.client import GitHubClient


EngineerRunner = Callable[..., EngineerResult]


class ExecuteOutcome(BaseModel):
    decision_id: str
    project: str
    status: Literal["executed", "skipped", "throttled", "error"]
    pr_url: str | None = None
    reason: str | None = None


class ExecuteApprovedReport(BaseModel):
    started_at: str
    finished_at: str
    outcomes: list[ExecuteOutcome] = Field(default_factory=list)
    capped: bool = False  # True when max_runs cut the sweep short

    @property
    def executed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "executed")

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "skipped")

    @property
    def throttled(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "throttled")

    @property
    def errored(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "error")


def _is_dry_run_decision(d: Decision) -> bool:
    """Decisions seeded by `--dry-run` carry no real plan; never execute them."""
    return "[DRY RUN]" in (d.summary or "")


def run_execute_approved(
    *,
    projects_dir: Path,
    store: DecisionStore,
    engineer_runs_store: EngineerRunStore,
    open_github_client: Callable[[Manifest], GitHubClient | None],
    api_key: str | None = None,
    dry_run: bool = True,
    cost_log_path: Path | None = None,
    max_runs: int = 5,
    runner: EngineerRunner | None = None,
) -> ExecuteApprovedReport:
    """Iterate approved Decisions and run the engineer crew on each.

    Filters applied (in order):
      1. Skip ``[DRY RUN]`` decisions (no real plan).
      2. Skip decisions that already have an ``EngineerRunRecord`` (already shipped).
      3. Skip non-GitHub-hosted projects (engineer crew is GitHub-only in v0).

    Stops after ``max_runs`` successful executions; remaining decisions stay
    APPROVED and will be picked up on the next sweep.

    ``runner`` defaults to ``run_engineer_crew`` and is injectable for tests.
    """
    from datetime import UTC, datetime

    runner = runner or run_engineer_crew
    started = datetime.now(tz=UTC).isoformat()

    manifests = load_active_manifests(projects_dir)
    approved = store.list_by_status(DecisionStatus.APPROVED)
    approved.sort(key=lambda d: d.created_at)  # FIFO

    outcomes: list[ExecuteOutcome] = []
    successful = 0
    capped = False

    for decision in approved:
        if successful >= max_runs:
            capped = True
            break

        if _is_dry_run_decision(decision):
            outcomes.append(
                ExecuteOutcome(
                    decision_id=str(decision.id),
                    project=decision.project,
                    status="skipped",
                    reason="dry-run decision (no real plan)",
                )
            )
            continue

        if engineer_runs_store.get(str(decision.id)) is not None:
            outcomes.append(
                ExecuteOutcome(
                    decision_id=str(decision.id),
                    project=decision.project,
                    status="skipped",
                    reason="engineer run already exists",
                )
            )
            continue

        manifest = manifests.get(decision.project)
        if manifest is None:
            outcomes.append(
                ExecuteOutcome(
                    decision_id=str(decision.id),
                    project=decision.project,
                    status="error",
                    reason=f"project {decision.project!r} not found in active manifests",
                )
            )
            continue

        if manifest.source.kind != "github" or not manifest.source.repo:
            outcomes.append(
                ExecuteOutcome(
                    decision_id=str(decision.id),
                    project=decision.project,
                    status="skipped",
                    reason=f"project source.kind={manifest.source.kind} not supported by engineer crew",
                )
            )
            continue

        github = open_github_client(manifest)
        if github is None:
            outcomes.append(
                ExecuteOutcome(
                    decision_id=str(decision.id),
                    project=decision.project,
                    status="error",
                    reason="failed to open GitHub client",
                )
            )
            continue

        try:
            with github:
                result = runner(
                    decision,
                    manifest,
                    github=github,
                    dry_run=dry_run,
                    api_key=api_key,
                    cost_log_path=cost_log_path,
                )
        except BudgetBreachError as e:
            outcomes.append(
                ExecuteOutcome(
                    decision_id=str(decision.id),
                    project=decision.project,
                    status="throttled",
                    reason=str(e),
                )
            )
            continue
        except Exception as e:  # noqa: BLE001 — surface every failure in the report
            outcomes.append(
                ExecuteOutcome(
                    decision_id=str(decision.id),
                    project=decision.project,
                    status="error",
                    reason=f"{type(e).__name__}: {e}",
                )
            )
            continue

        if result.skipped:
            outcomes.append(
                ExecuteOutcome(
                    decision_id=str(decision.id),
                    project=decision.project,
                    status="skipped",
                    reason=result.skip_reason or "engineer crew skipped",
                )
            )
            continue

        # Persist the engineer run + mark Decision EXECUTED. Mirror `minions
        # implement` so dashboard + sync paths see the same state.
        try:
            engineer_runs_store.save(result, project=manifest.name)
        except Exception:  # noqa: BLE001
            pass  # persistence failure must not block the status transition

        if not dry_run:
            persisted = store.get(decision.id)
            if persisted is not None:
                persisted.pr_url = result.pr_url
                persisted.status = DecisionStatus.EXECUTED
                store.save(persisted)

        outcomes.append(
            ExecuteOutcome(
                decision_id=str(decision.id),
                project=decision.project,
                status="executed",
                pr_url=result.pr_url,
            )
        )
        successful += 1

    return ExecuteApprovedReport(
        started_at=started,
        finished_at=datetime.now(tz=UTC).isoformat(),
        outcomes=outcomes,
        capped=capped,
    )
