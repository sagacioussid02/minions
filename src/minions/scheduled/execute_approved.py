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
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.approval.store import DecisionStore
from minions.budget import BudgetBreachError
from minions.crews.engineer import EngineerOutput, EngineerResult, run_engineer_crew
from minions.crews.engineer_runs_store import EngineerRunStore
from minions.crews.flow_control import distinct_open_pr_count
from minions.dossiers.refresh import (
    build_dossier_engineer_output,
    draft_id_from_decision,
    is_dossier_refresh_decision,
)
from minions.dossiers.store_factory import DossierStoreLike
from minions.models.decision import Decision, DecisionStatus
from minions.models.manifest import Manifest, load_active_manifests

if TYPE_CHECKING:
    from minions.agents.memory_store_factory import AgentMemoryStoreLike
    from minions.github.client import GitHubClient
    from minions.models.task import Task
    from minions.tasks.store_factory import TaskStoreLike


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


def _is_dossier_refresh_decision_safe(d: Decision) -> bool:
    # Local import — keeps module-import-time deps shallow.
    try:
        from minions.dossiers.refresh import is_dossier_refresh_decision

        return is_dossier_refresh_decision(d)
    except Exception:  # noqa: BLE001
        return False


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
    only_expedited: bool = False,
    task_store: TaskStoreLike | None = None,
    memory_store: AgentMemoryStoreLike | None = None,
    dossier_store: DossierStoreLike | None = None,
    projects: list[str] | None = None,
) -> ExecuteApprovedReport:
    """Iterate approved Decisions and run the engineer crew on each.

    Filters applied (in order):
      1. Skip ``[DRY RUN]`` decisions (no real plan).
      2. Skip decisions that already have an ``EngineerRunRecord`` (already shipped).
      3. Skip non-GitHub-hosted projects (engineer crew is GitHub-only in v0).

    Stops after ``max_runs`` successful executions; remaining decisions stay
    APPROVED and will be picked up on the next sweep.

    ``only_expedited`` restricts the sweep to ``expedited`` Decisions (the
    fast-lane the leadership room uses to jump CTO investigations ahead of
    the 6-hour cron cadence). Off by default; enable via ``--only-expedited``
    on ``cron-execute-approved`` or from an out-of-band ``workflow_dispatch``.

    ``projects`` filters to specific ``load_active_manifests`` dict keys
    (e.g. tenant-scoped ``"<tenant_id>:<project>"`` compound keys) — used by
    the tenant-scoped dispatch so a visitor's approval isn't stuck waiting
    for the founder's own shared cron.

    ``runner`` defaults to ``run_engineer_crew`` and is injectable for tests.
    """
    from datetime import UTC, datetime

    runner = runner or run_engineer_crew
    started = datetime.now(tz=UTC).isoformat()

    manifests = load_active_manifests(projects_dir)
    if projects:
        wanted = {p.lower() for p in projects}
        manifests = {
            name: manifest for name, manifest in manifests.items() if name.lower() in wanted
        }
    # Decision.project stores manifest.name (the plain display name), not
    # this dict's key — which is a compound "tenant_id:project" for tenant
    # manifests (see load_tenant_manifests). Resolve by (tenant_id, name),
    # not name alone — two different tenants can independently pick the same
    # project display name (e.g. both call it "demo"), and a name-only
    # lookup would let one tenant's approved decision execute against the
    # other tenant's manifest/repo.
    manifests_by_key = {(m.tenant_id, m.name): m for m in manifests.values()}
    approved = store.list_by_status(DecisionStatus.APPROVED)
    if only_expedited:
        approved = [d for d in approved if d.expedited]
    if projects:
        # Scoped dispatch (e.g. tenant-triggered) — only touch decisions
        # whose project resolved into the (already-filtered) manifests above,
        # rather than iterating every other tenant's pending decisions too.
        approved = [d for d in approved if (d.tenant_id, d.project) in manifests_by_key]
    approved.sort(key=_approved_sort_key)

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

        task: Task | None = None
        has_tasks = False
        if task_store is not None:
            tasks = task_store.list_by_decision(decision.id)
            has_tasks = len(tasks) > 0
            queued = [t for t in tasks if t.status == "queued"]
            if queued:
                task = sorted(queued, key=lambda t: t.created_at)[0]
            elif has_tasks:
                outcomes.append(
                    ExecuteOutcome(
                        decision_id=str(decision.id),
                        project=decision.project,
                        status="skipped",
                        reason="refined tasks already claimed or complete",
                    )
                )
                continue

        if not has_tasks and engineer_runs_store.get(str(decision.id)) is not None:
            outcomes.append(
                ExecuteOutcome(
                    decision_id=str(decision.id),
                    project=decision.project,
                    status="skipped",
                    reason="engineer run already exists",
                )
            )
            continue

        manifest = manifests_by_key.get((decision.tenant_id, decision.project))
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

        # Per-project flow-control: refuse to spend tokens on a fresh
        # engineer run when the project is already at its open-PR cap.
        # Dossier refresh + in-place fix decisions are exempt — they
        # don't open NEW PRs and blocking them would deadlock the queue.
        is_fresh_pr_decision = not (
            (getattr(decision, "model_extra", None) or {}).get("existing_pr_branch")
        )
        if is_fresh_pr_decision and not _is_dossier_refresh_decision_safe(decision):
            open_prs = distinct_open_pr_count(
                project=decision.project,
                engineer_runs_store=engineer_runs_store,
                tenant_id=decision.tenant_id,
            )
            cap = manifests_by_key.get((decision.tenant_id, decision.project))
            cap_value = cap.flow_control.max_open_prs if cap is not None else 5
            if open_prs >= cap_value:
                outcomes.append(
                    ExecuteOutcome(
                        decision_id=str(decision.id),
                        project=decision.project,
                        status="throttled",
                        reason=(
                            f"open_pr_cap={cap_value} reached "
                            f"({open_prs} open) — merge or close existing PRs first"
                        ),
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

        # In-place fix mode: pr_followup + pr_review_loop stamp these
        # fields on fix Decisions so the engineer commits onto the
        # original PR's branch instead of opening a new PR. Pydantic
        # extra='allow' preserves them; access via model_extra.
        extra = getattr(decision, "model_extra", None) or {}
        target_branch = extra.get("existing_pr_branch")
        existing_pr_number = extra.get("existing_pr_number")
        retry_attempt = int(extra.get("retry_attempt") or 0)
        is_conflict_resolution = bool(extra.get("conflict_resolution"))

        # Dossier refreshes ride through the same engineer-crew machinery,
        # but they bypass the LLM entirely — the markdown body was already
        # produced + verified by the discoverer crew. Build the override here.
        output_override: EngineerOutput | None = None
        if is_dossier_refresh_decision(decision):
            if dossier_store is None:
                outcomes.append(
                    ExecuteOutcome(
                        decision_id=str(decision.id),
                        project=decision.project,
                        status="error",
                        reason="dossier refresh approved but no dossier_store wired in",
                    )
                )
                continue
            draft_id = draft_id_from_decision(decision)
            draft = dossier_store.get(draft_id) if draft_id else None
            if draft is None:
                outcomes.append(
                    ExecuteOutcome(
                        decision_id=str(decision.id),
                        project=decision.project,
                        status="error",
                        reason=f"linked dossier draft {draft_id!r} not found",
                    )
                )
                continue
            output_override = build_dossier_engineer_output(draft, manifest)

        try:
            if task is not None and not dry_run:
                task_store.update_status(task.id, "in_progress")  # type: ignore[union-attr]
            with github:
                result = runner(
                    decision,
                    manifest,
                    github=github,
                    dry_run=dry_run,
                    api_key=api_key,
                    cost_log_path=cost_log_path,
                    task=task,
                    target_branch=target_branch,
                    existing_pr_number=existing_pr_number,
                    retry_attempt=retry_attempt,
                    is_conflict_resolution=is_conflict_resolution,
                    output_override=output_override,
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
            if task is not None and not dry_run:
                with suppress(Exception):
                    task_store.update_status(task.id, "blocked")  # type: ignore[union-attr]
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
        # implement` so dashboard + sync paths see the same state. Dry-run
        # diagnostics must stay read-only; otherwise a diagnostic sweep can
        # create an engineer_runs row that prevents the real sweep from
        # picking the Decision up later.
        if not dry_run:
            with suppress(Exception):
                engineer_runs_store.save(
                    result, project=manifest.name, tenant_id=manifest.tenant_id
                )

            if task is not None:
                with suppress(Exception):
                    task_store.update_status(  # type: ignore[union-attr]
                        task.id,
                        "review",
                        pr_url=result.pr_url,
                        pr_number=result.pr_number,
                    )
                if memory_store is not None:
                    with suppress(Exception):
                        _record_task_memory(memory_store, task, decision, result)

            persisted = store.get(decision.id)
            if persisted is not None:
                persisted.pr_url = result.pr_url
                if not has_tasks or _all_tasks_claimed(task_store, decision.id):  # type: ignore[arg-type]
                    persisted.status = DecisionStatus.EXECUTED
                store.save(persisted)

            # Dossier-specific: flip the linked draft to pr_open so
            # ``minions dossier show`` reflects the in-flight PR.
            if is_dossier_refresh_decision(decision) and dossier_store is not None:
                draft_id = draft_id_from_decision(decision)
                if draft_id:
                    linked = dossier_store.get(draft_id)
                    if linked is not None:
                        from minions.models.dossier import DossierStatus

                        linked.status = DossierStatus.PR_OPEN
                        linked.pr_url = result.pr_url
                        linked.pr_number = result.pr_number
                        with suppress(Exception):
                            dossier_store.save(linked)

            # Best-effort relay back into the spokesperson thread. Only fires
            # for Decisions whose raw payload has spike_source +
            # thread_id (set by the TS-side createSpikeDecision). Never
            # raises — relay failure must not block the EXECUTED transition.
            with suppress(Exception):
                from minions.spokesperson.interview_relay import relay_spike_answer

                relay_spike_answer(
                    decision_id=str(decision.id),
                    project=manifest.name,
                    engineer_result=result,
                )

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


def _approved_sort_key(decision: Decision) -> tuple[int, int, object]:
    priority_rank = {"p1": 0, "p2": 1, "p3": 2}
    return (
        priority_rank.get(decision.priority, priority_rank["p3"]),
        0 if decision.expedited else 1,
        decision.created_at,
    )


def _all_tasks_claimed(task_store: TaskStoreLike, decision_id: object) -> bool:
    return all(t.status != "queued" for t in task_store.list_by_decision(decision_id))


def _record_task_memory(
    memory_store: AgentMemoryStoreLike,
    task: Task,
    decision: Decision,
    result: EngineerResult,
) -> None:
    memory_store.record(
        agent_id=task.owner_agent_id,
        sprint_number=task.sprint_number,
        decision_id=decision.id,
        task_id=task.id,
        pr_url=result.pr_url,
        event="pr_opened",
        summary=f"Opened PR for task '{task.title}' in {task.project}.",
        details=task.acceptance_criteria or task.description,
    )
    memory_store.record(
        agent_id=task.owner_agent_id,
        sprint_number=task.sprint_number,
        decision_id=decision.id,
        task_id=task.id,
        pr_url=result.pr_url,
        event="task_done",
        summary=f"Completed implementation pass for '{task.title}'.",
        details=", ".join(result.files_changed) if result.files_changed else None,
    )
