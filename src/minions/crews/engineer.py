"""Engineer crew — picks up an approved Decision and opens a draft PR.

Flow:
  1. Engineer (Haiku in v0) reads the Decision's plan + a small context
     window of the repo (README, package config, top-level docs).
  2. Engineer produces a structured EngineerOutput with PR title, body, and
     up to MAX_FILES_PER_PR file patches.
  3. Orchestrator filters forbidden paths (CI / secrets / .github), creates
     a feature branch from origin/<default_branch>, and commits each file
     via the Contents API.
  4. Orchestrator opens a draft PR.
  5. TTL (Haiku) reads the diff summary and posts a review comment on the PR.
  6. Decision is mutated to status=EXECUTED with pr_url set.

Safety layers (in addition to the safety preamble in every system prompt):
  - GitHubClient refuses to push to main/master and has no merge method.
  - Forbidden-path filter rejects writes to .github/, .env*, secrets/, etc.
  - Hard cap MAX_FILES_PER_PR = 5.
  - Branch-exists check prevents accidental overwrites.
  - PR is always opened in DRAFT mode.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import textwrap
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from minions.activity import crew_run
from minions.agents.base import MinionAgent
from minions.agents.roster import build_named_agent
from minions.budget import assert_can_run_engineer
from minions.budget import evaluate as evaluate_budget
from minions.cost import clear_attribution, set_attribution
from minions.github.client import GitHubClient, GitHubError
from minions.models.decision import Decision, DecisionStatus
from minions.models.roles import Role
from minions.observability import add_metadata, observe_crew

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from minions.models.manifest import Manifest
    from minions.models.task import Task as SprintTask


MAX_FILES_PER_PR = 5
MAX_CONTEXT_FILES = 6
MAX_CONTEXT_CHARS_PER_FILE = 3000

CONTEXT_PRIORITY_PATHS: tuple[str, ...] = (
    "README.md",
    "README.rst",
    "README",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "tsconfig.json",
    "requirements.txt",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "DESIGN.md",
)

# Path prefixes the engineer is NEVER allowed to write to. Filtered at the
# orchestrator level even if the LLM tries.
FORBIDDEN_PATH_PREFIXES: tuple[str, ...] = (
    ".github/",
    ".env",
    "secrets/",
    ".aws/",
    ".ssh/",
)
FORBIDDEN_PATH_SUBSTRINGS: tuple[str, ...] = ("credentials",)
FORBIDDEN_PATH_SUFFIXES: tuple[str, ...] = (
    ".pem",
    ".key",
)
# Files that LOOK forbidden by the `.env` prefix but are documentation templates
# (no real secrets). Explicit allowlist — keep narrow.
FORBIDDEN_PATH_ALLOWLIST: tuple[str, ...] = (
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
)


class FilePatch(BaseModel):
    path: str = Field(..., description="Repo-relative path, e.g. 'README.md'")
    content: str = Field(..., description="Full file contents AFTER the change. Not a diff.")
    operation: Literal["create", "update"] = Field(
        "create", description="create for new files, update for existing"
    )


class EngineerOutput(BaseModel):
    pr_title: str = Field(..., description="Conventional Commit-style title")
    pr_body: str = Field(..., description="Markdown PR description")
    files: list[FilePatch] = Field(default_factory=list, max_length=MAX_FILES_PER_PR)


class EngineerResult(BaseModel):
    """Outcome of one engineer crew run."""

    decision_id: str
    task_id: str | None = None
    pr_url: str | None = None
    pr_number: int | None = None
    branch_name: str | None = None
    files_changed: list[str] = Field(default_factory=list)
    files_rejected: list[str] = Field(default_factory=list)
    review_comment: str | None = None
    operator_comment_posted: bool = False  # §3.4 PR notifier comment
    skipped: bool = False
    skip_reason: str | None = None
    dry_run: bool = False
    # Preflight surface (Phase 4 of engineer-preflight-execution). Populated
    # when preflight ran; None for dry-runs, in-place fixes, and projects
    # with ``preflight.enabled=false``.
    preflight_attempted: bool = False
    preflight_ok: bool | None = None
    preflight_failure_step: str | None = None
    preflight_failure_tail: str | None = None
    preflight_retries: int = 0
    # Sticky owner agent id (e.g. ``engineer@Demo``) — the seat that opened
    # the PR is accountable for it for the PR's lifetime. The owner sweep
    # re-dispatches THIS exact agent on retry.
    owner_agent_id: str | None = None


def slugify(text: str, max_len: int = 40) -> str:
    """Branch-friendly slug — alphanumerics + dashes, max 40 chars."""
    s = re.sub(r"[^a-zA-Z0-9_]+", "-", text.strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len] or "change"


def branch_name_for_decision(decision: Decision) -> str:
    """Stable branch name that stays unique across repeated sprint summaries."""
    return f"minions/eng/{slugify(decision.summary)}-{str(decision.id)[:8]}"


def branch_name_for_task(decision: Decision, task: SprintTask) -> str:
    """Stable branch name for one refined Task under a sprint Decision."""
    return f"minions/eng/{slugify(task.title)}-{str(task.id)[:8]}"


_MINIONS_RUN_ID_TRAILER = re.compile(r"^Minions-Run-Id:\s*\S+\s*$", re.MULTILINE)


def _branch_has_operator_commits(github: GitHubClient, branch: str) -> bool:
    """True iff any commit on ``branch`` lacks our ``Minions-Run-Id`` trailer.

    The engineer crew + branch sweeper share this rule: a single
    operator-authored commit (no trailer) makes the branch off-limits
    forever. Prevents the in-place fix path from clobbering manual
    operator edits.
    """
    try:
        commits = github.list_branch_commits(branch=branch, limit=100)
    except Exception:  # noqa: BLE001 — refuse to act on uncertain state
        return True
    for c in commits:
        message = ((c.get("commit") or {}).get("message")) or ""
        if not _MINIONS_RUN_ID_TRAILER.search(message):
            return True
    return False


def is_forbidden_path(path: str) -> bool:
    # Normalize without stripping the leading dot of dotfiles like '.env'.
    p = path.lower()
    if p.startswith("./"):
        p = p[2:]
    elif p.startswith("/"):
        p = p[1:]
    # Allowlist wins over the `.env` prefix rule — these files are templates,
    # not real secret stores.
    if p in FORBIDDEN_PATH_ALLOWLIST:
        return False
    if any(p.startswith(prefix) for prefix in FORBIDDEN_PATH_PREFIXES):
        return True
    if any(sub in p for sub in FORBIDDEN_PATH_SUBSTRINGS):
        return True
    return any(p.endswith(suffix) for suffix in FORBIDDEN_PATH_SUFFIXES)


def _run_preflight_gate(
    *,
    decision: Decision,
    manifest: Manifest,
    github: GitHubClient,
    api_key: str | None,
    eng_min: MinionAgent,
    allowed_files: list[FilePatch],
    task: SprintTask | None,
    retry_attempt: int,
) -> tuple[list[FilePatch], dict[str, object], str | None]:
    """Run preflight against ``allowed_files``; on failure, retry once.

    Returns ``(files_to_use, state, skip_reason)``. ``skip_reason`` is None
    on success; otherwise the engineer caller surfaces it as a skipped
    EngineerResult with no PR opened.

    The engineer's retry pass receives the failure output via the
    ``preflight_failure`` kwarg on ``_run_engineer_llm`` so the model can
    actually understand what to fix. Second failure → skip.
    """
    from pathlib import Path

    from minions.preflight.runner import run_preflight
    from minions.working_tree import resolve_working_tree

    state: dict[str, object] = {
        "attempted": True,
        "ok": None,
        "step": None,
        "tail": None,
        "retries": 0,
    }
    try:
        cache_dir = Path(__file__).resolve().parents[3] / "data" / "local" / "clones"
        repo_clone = resolve_working_tree(manifest, cache_dir=cache_dir)
    except Exception as e:  # noqa: BLE001 — clone failure is the operator's signal
        logger.warning("preflight: could not resolve working tree (%s); skipping", e)
        state["ok"] = None
        state["attempted"] = False
        return allowed_files, state, None

    report = run_preflight(
        patches=allowed_files,
        manifest=manifest,
        repo_clone=repo_clone,
    )
    if report.ok:
        state["ok"] = True
        return allowed_files, state, None

    failed = report.failed_step
    state["step"] = failed.step if failed else "unknown"
    state["tail"] = (failed.stderr_tail or failed.stdout_tail)[-2000:] if failed else None

    # If we have no api_key (e.g. an output_override flow), can't retry.
    if api_key is None:
        state["ok"] = False
        return (
            allowed_files,
            state,
            (f"preflight failed on step '{state['step']}' and no api_key for retry"),
        )

    # ONE retry — feed the failure back into the engineer.
    retry_output = _run_engineer_llm(
        decision,
        manifest,
        eng_min,
        github,
        github.get_repo().default_branch,
        api_key,
        task=task,
        retry_attempt=retry_attempt + 1,
        is_conflict_resolution=False,
        existing_pr_number=None,
        preflight_failure=report,
    )
    state["retries"] = 1
    retry_allowed, _ = filter_files(retry_output.files)
    if not retry_allowed:
        state["ok"] = False
        return allowed_files, state, "preflight retry produced no allowed files"

    report2 = run_preflight(
        patches=retry_allowed,
        manifest=manifest,
        repo_clone=repo_clone,
    )
    if report2.ok:
        state["ok"] = True
        return retry_allowed, state, None

    failed2 = report2.failed_step
    state["step"] = failed2.step if failed2 else state["step"]
    state["tail"] = (
        (failed2.stderr_tail or failed2.stdout_tail)[-2000:] if failed2 else state["tail"]
    )
    state["ok"] = False
    return (
        retry_allowed,
        state,
        (f"preflight failed twice on step '{state['step']}' — operator action required"),
    )


def filter_files(files: list[FilePatch]) -> tuple[list[FilePatch], list[str]]:
    allowed: list[FilePatch] = []
    rejected: list[str] = []
    for f in files[:MAX_FILES_PER_PR]:
        if is_forbidden_path(f.path):
            rejected.append(f.path)
        else:
            allowed.append(f)
    return allowed, rejected


@observe_crew("engineer_crew")
def run_engineer_crew(
    decision: Decision,
    manifest: Manifest,
    *,
    github: GitHubClient,
    dry_run: bool = True,
    api_key: str | None = None,
    output_override: EngineerOutput | None = None,
    cost_log_path: Path | None = None,
    task: SprintTask | None = None,
    target_branch: str | None = None,
    existing_pr_number: int | None = None,
    retry_attempt: int = 0,
    is_conflict_resolution: bool = False,
    is_review_response: bool = False,
) -> EngineerResult:
    """Run the engineer crew for an approved Decision. Opens a draft PR.

    ``output_override`` short-circuits the LLM and uses the provided output
    directly — for tests and for replaying past decisions.
    """
    add_metadata(
        crew="engineer",
        project=manifest.name,
        decision_id=str(decision.id),
        decision_type=decision.type.value,
        decision_risk=decision.risk,
        dry_run=dry_run,
        repo=manifest.source.repo,
        task_id=str(task.id) if task else None,
    )
    set_attribution(project=manifest.name, decision_id=str(decision.id), role="engineer")

    # §6.5 budget guard — refuse engineer runs once a project has breached its
    # monthly cap. Dry runs are free, so they're always permitted (useful for
    # diagnosing what *would* have happened post-cap).
    if not dry_run:
        bstate = evaluate_budget(manifest, cost_log_path=cost_log_path)
        assert_can_run_engineer(bstate)

    # APPROVED is required to OPEN a new PR. In-place mode (owner-sweep
    # retries, conflict resolution, CI fix) re-uses an existing branch +
    # PR, so the Decision is already EXECUTED — accept it. The PR's
    # existence is itself evidence of prior operator approval.
    allowed_statuses = (
        {DecisionStatus.APPROVED, DecisionStatus.EXECUTED}
        if target_branch is not None
        else {DecisionStatus.APPROVED}
    )
    if decision.status not in allowed_statuses:
        return EngineerResult(
            decision_id=str(decision.id),
            skipped=True,
            skip_reason=f"decision status is {decision.status.value}, must be APPROVED",
            dry_run=dry_run,
        )

    repo = github.get_repo()
    base_branch = repo.default_branch
    base_ref = github.get_branch_ref(base_branch)
    base_sha = base_ref.sha

    eng_min = build_named_agent(
        Role.ENGINEER,
        project=manifest.name,
        manifest=manifest,
        cadence=manifest.cadence_profile,
    )

    if output_override is not None:
        output = output_override
    elif dry_run:
        output = _dry_run_output(decision, task=task)
    else:
        if api_key is None:
            raise ValueError("api_key is required when dry_run=False")
        output = _run_engineer_llm(
            decision,
            manifest,
            eng_min,
            github,
            base_branch,
            api_key,
            task=task,
            retry_attempt=retry_attempt,
            is_conflict_resolution=is_conflict_resolution,
            is_review_response=is_review_response,
            existing_pr_number=existing_pr_number,
        )

    allowed_files, rejected = filter_files(output.files)
    if not allowed_files:
        return EngineerResult(
            decision_id=str(decision.id),
            task_id=str(task.id) if task else None,
            files_rejected=rejected,
            skipped=True,
            skip_reason=(
                "engineer produced no allowed file changes"
                + (f" ({len(rejected)} forbidden paths rejected)" if rejected else "")
            ),
            dry_run=dry_run,
        )

    # Preflight gate (Phase 4 of engineer-preflight-execution). Runs the
    # project's own build/test commands against a scratch checkout with the
    # engineer's patches applied. Skipped for dry-runs, in-place fixes
    # (those re-use an existing branch's CI), and projects with
    # ``preflight.enabled=false``. On failure, ONE retry is attempted with
    # the failure pasted into the engineer's prompt; on second failure the
    # PR is NOT opened.
    preflight_state: dict[str, object] = {
        "attempted": False,
        "ok": None,
        "step": None,
        "tail": None,
        "retries": 0,
    }
    if (
        not dry_run
        and target_branch is None
        and getattr(manifest, "preflight", None) is not None
        and manifest.preflight.enabled
    ):
        allowed_files, preflight_state, preflight_skip = _run_preflight_gate(
            decision=decision,
            manifest=manifest,
            github=github,
            api_key=api_key,
            eng_min=eng_min,
            allowed_files=allowed_files,
            task=task,
            retry_attempt=retry_attempt,
        )
        if preflight_skip is not None:
            return EngineerResult(
                decision_id=str(decision.id),
                task_id=str(task.id) if task else None,
                files_rejected=rejected,
                skipped=True,
                skip_reason=preflight_skip,
                dry_run=False,
                preflight_attempted=True,
                preflight_ok=False,
                preflight_failure_step=preflight_state["step"],  # type: ignore[arg-type]
                preflight_failure_tail=preflight_state["tail"],  # type: ignore[arg-type]
                preflight_retries=int(preflight_state["retries"] or 0),
            )

    # In-place mode: caller supplied an existing branch (typically from
    # pr_followup / pr_review_loop's fix Decision). Engineer commits onto
    # that branch instead of creating a fresh one, and we do NOT open a
    # new PR — the existing PR's CI re-runs on push.
    in_place = target_branch is not None
    branch_name = (
        target_branch
        if in_place
        else (branch_name_for_task(decision, task) if task else branch_name_for_decision(decision))
    )

    if in_place:
        # In-place path: verify the branch + (if present) PR still exist
        # before we waste an LLM call. Skip cleanly if the parent state is
        # gone — operator may have closed/merged the PR while the fix
        # Decision was queued.
        try:
            github.get_branch_ref(branch_name)
        except GitHubError as e:
            if e.status_code == 404:
                return EngineerResult(
                    decision_id=str(decision.id),
                    task_id=str(task.id) if task else None,
                    branch_name=branch_name,
                    skipped=True,
                    skip_reason=f"in-place target branch {branch_name!r} no longer exists",
                    dry_run=dry_run,
                )
            raise
        # Guard against operator-touched branches (any commit without our
        # Minions-Run-Id trailer). Same rule the branch sweeper enforces.
        if _branch_has_operator_commits(github, branch_name):
            return EngineerResult(
                decision_id=str(decision.id),
                task_id=str(task.id) if task else None,
                branch_name=branch_name,
                skipped=True,
                skip_reason=(
                    f"branch {branch_name!r} has operator-authored commits; "
                    "in-place fix declines to overwrite"
                ),
                dry_run=dry_run,
            )
    else:
        # Fresh-PR path — refuse to overwrite a stranded branch.
        try:
            github.get_branch_ref(branch_name)
            return EngineerResult(
                decision_id=str(decision.id),
                task_id=str(task.id) if task else None,
                branch_name=branch_name,
                files_rejected=rejected,
                skipped=True,
                skip_reason=f"branch {branch_name!r} already exists; resolve manually before retry",
                dry_run=dry_run,
            )
        except GitHubError as e:
            if e.status_code != 404:
                raise

    if dry_run:
        dry_summary = (
            f"[DRY RUN] would append commit to existing PR #{existing_pr_number} "
            f"on branch {branch_name} ({len(allowed_files)} files, retry {retry_attempt})"
            if in_place
            else f"[DRY RUN] would open draft PR for branch {branch_name} "
            f"({len(allowed_files)} files)"
        )
        return EngineerResult(
            decision_id=str(decision.id),
            task_id=str(task.id) if task else None,
            pr_url=dry_summary,
            pr_number=existing_pr_number,
            branch_name=branch_name,
            files_changed=[f.path for f in allowed_files],
            files_rejected=rejected,
            review_comment="[DRY RUN] no review run",
            dry_run=True,
        )

    # Live path — bracket the whole branch+commit+PR+review block as a
    # single "engineer is running" activity event so the dashboard's
    # running-now badge stays lit until the PR is open.
    with crew_run(
        crew="engineer",
        project=manifest.name,
        agents=["engineer", "tech_team_lead"] if api_key is not None else ["engineer"],
        decision_id=str(decision.id),
    ) as run_id:
        return _run_engineer_live_path(
            decision=decision,
            manifest=manifest,
            github=github,
            api_key=api_key,
            branch_name=branch_name,
            base_branch=base_branch,
            base_sha=base_sha,
            allowed_files=allowed_files,
            rejected=rejected,
            output=output,
            run_id=run_id,
            task=task,
            in_place=in_place,
            existing_pr_number=existing_pr_number,
            retry_attempt=retry_attempt,
            preflight_state=preflight_state,
            owner_agent_id=eng_min.name,
        )


def _run_engineer_live_path(
    *,
    decision: Decision,
    manifest: Manifest,
    github: GitHubClient,
    api_key: str | None,
    branch_name: str,
    base_branch: str,
    base_sha: str,
    allowed_files: list[FilePatch],
    rejected: list[str],
    output: EngineerOutput,
    run_id: str,
    task: SprintTask | None = None,
    in_place: bool = False,
    existing_pr_number: int | None = None,
    retry_attempt: int = 0,
    preflight_state: dict[str, object] | None = None,
    owner_agent_id: str | None = None,
) -> EngineerResult:
    """Live-path body — extracted so the activity bracket above is one block.

    Two modes:
      * Fresh-PR (default): create_branch + commit files + open_pull_request.
        On failure, delete the branch we just created (in-process rollback).
      * In-place (``in_place=True``): commit additional files onto an
        existing branch and skip open_pull_request. The existing PR
        re-runs CI on push. Rollback does NOT delete the branch — the
        operator's branch survives the failure intact.

    Every commit carries ``Minions-Run-Id`` + ``Minions-Decision-Id``
    trailers so the branch sweeper can prove ownership before deleting.
    """
    title_for_commit = (
        f"{output.pr_title} (retry {retry_attempt})"
        if in_place and retry_attempt > 0
        else output.pr_title
    )
    commit_message = (
        f"{title_for_commit}\n\n"
        f"(role: engineer; decision: {decision.id})\n\n"
        f"Minions-Run-Id: {run_id}\n"
        f"Minions-Decision-Id: {decision.id}\n"
    )

    if not in_place:
        github.create_branch(name=branch_name, base_sha=base_sha)

    try:
        for f in allowed_files:
            existing_sha = github.get_file_sha(path=f.path, branch=branch_name)
            github.update_file(
                branch=branch_name,
                path=f.path,
                content=f.content,
                message=commit_message,
                sha=existing_sha,
            )

        if in_place:
            # Skip open_pull_request — CI re-runs on the existing PR
            # automatically when GitHub sees the push on the branch.
            pr_url = f"https://github.com/{github.repo}/pull/{existing_pr_number}"
            pr_number = existing_pr_number
        else:
            body = (
                output.pr_body
                + "\n\n---\n"
                + f"**Source Decision:** `{decision.id}`\n"
                + (f"**Source Task:** `{task.id}` — {task.title}\n" if task else "")
                + f"**Proposer:** {decision.proposer_display_name or decision.proposer_agent_id}"
            )
            pr = github.open_pull_request(
                title=output.pr_title,
                body=body,
                head=branch_name,
                base=base_branch,
                draft=True,
            )
            pr_url = pr.html_url
            pr_number = pr.number
    except Exception:
        # Rollback policy differs by mode:
        #   * Fresh-PR: delete the branch we just created (stranded branch
        #     would otherwise block the next sweep with "branch already
        #     exists"). Branch sweeper catches what we miss.
        #   * In-place: do NOT delete — the branch existed before us and
        #     belongs to the original PR. Failed commit is a no-op as long
        #     as the file update was atomic (it is, per GitHub Contents API).
        if not in_place:
            with suppress(Exception):
                github.delete_branch(name=branch_name)
        raise

    # §3.4 — operator-review notifier comment. Distinct from the TTL code review
    # below: this is a one-paragraph briefing for the human, posted whether or
    # not the LLM-based TTL review runs. In-place retries skip the operator
    # briefing — the original PR already has it and a retry note would be
    # noise.
    operator_comment_posted = False
    if not in_place and pr_number is not None:
        try:
            github.comment_on_pull_request(
                number=pr_number,
                body=_build_operator_review_comment(
                    decision,
                    manifest,
                    output,
                    allowed_files,
                    task=task,
                ),
            )
            operator_comment_posted = True
        except GitHubError:
            operator_comment_posted = False

    review_comment = None
    if api_key is not None and pr_number is not None and pr_url is not None:
        review_comment = _run_ttl_review(decision, manifest, output, allowed_files, pr_url, api_key)
        try:
            github.comment_on_pull_request(number=pr_number, body=review_comment)
        except GitHubError:
            # Don't fail the whole flow if the review comment can't be posted.
            review_comment = (review_comment or "") + "\n\n[note: comment post failed]"

    # Phase 3 of crew-transcripts: persist the engineer's narrative
    # (pr_body) + TTL review as transcript messages. Best-effort.
    if run_id:
        from minions.transcripts.capture import record_task_default

        eng_narrative = (output.pr_body or "").strip() or output.pr_title
        record_task_default(
            run_id=run_id,
            project=manifest.name,
            crew="engineer",
            agent_role="engineer",
            agent_display_name=owner_agent_id,  # already namespaced (e.g. "engineer@Demo#1")
            sequence=0,
            role_in_conversation="task_output",
            task_output=eng_narrative,
            decision_id=str(decision.id),
        )
        if review_comment:
            record_task_default(
                run_id=run_id,
                project=manifest.name,
                crew="engineer",
                agent_role="tech_team_lead",
                agent_display_name=None,
                sequence=1,
                role_in_conversation="review",
                task_output=review_comment,
                decision_id=str(decision.id),
            )

    return EngineerResult(
        decision_id=str(decision.id),
        task_id=str(task.id) if task else None,
        pr_url=pr_url,
        pr_number=pr_number,
        branch_name=branch_name,
        files_changed=[f.path for f in allowed_files],
        files_rejected=rejected,
        review_comment=review_comment,
        operator_comment_posted=operator_comment_posted,
        dry_run=False,
        preflight_attempted=bool((preflight_state or {}).get("attempted")),
        preflight_ok=(preflight_state or {}).get("ok"),  # type: ignore[arg-type]
        preflight_failure_step=(preflight_state or {}).get("step"),  # type: ignore[arg-type]
        preflight_failure_tail=(preflight_state or {}).get("tail"),  # type: ignore[arg-type]
        preflight_retries=int((preflight_state or {}).get("retries") or 0),
        owner_agent_id=owner_agent_id,
    )


# =============================================================================
# Operator review comment builder (§3.4)
# =============================================================================


def _build_operator_review_comment(
    decision: Decision,
    manifest: Manifest,
    output: EngineerOutput,
    allowed_files: list[FilePatch],
    task: SprintTask | None = None,
) -> str:
    """One-paragraph operator briefing posted as a PR comment.

    Distinct from the LLM TTL review — this fires whether or not Claude is
    available, and is aimed at the human rather than the codebase. Includes
    the source Decision id (so the operator can trace back to the email),
    the risk level, and a clear "Review and merge" call to action.
    """
    proposer = decision.proposer_display_name or decision.proposer_agent_id
    files_block = "\n".join(f"  - `{f.path}`" for f in allowed_files) or "  - _(none)_"
    task_row = f"| **Task** | `{task.id}` · {task.title} |\n" if task else ""
    if task and task.sprint_number is not None:
        sprint = task.sprint_number
    else:
        sprint = decision.sprint_number
    sprint_row = f"| **Sprint** | {sprint} |\n" if sprint is not None else ""
    return (
        "## 🤖 Operator review requested\n\n"
        f"This draft PR was opened by the **engineer crew** for project "
        f"`{manifest.name}` based on an approved Decision Record.\n\n"
        f"| | |\n"
        f"|---|---|\n"
        f"| **Decision** | `{decision.id}` |\n"
        f"{task_row}"
        f"{sprint_row}"
        f"| **Type** | {decision.type.value} |\n"
        f"| **Risk** | {decision.risk} |\n"
        f"| **Proposer** | {proposer} ({decision.proposer_role}) |\n"
        f"| **Files** | {len(allowed_files)} |\n\n"
        "### Files changed\n"
        f"{files_block}\n\n"
        "### Next step — operator action\n"
        "1. Review the diff. The agent has **no merge capability** by design.\n"
        "2. If it looks good, mark the PR ready-for-review and merge.\n"
        "3. If not, close the PR and reject the underlying Decision with "
        '`minions decisions reject <id> -r "…"` so the rationale is captured '
        "in the audit log.\n"
    )


# =============================================================================
# Internals — LLM invocations, parsing, dry-run stubs
# =============================================================================


def _dry_run_output(decision: Decision, *, task: SprintTask | None = None) -> EngineerOutput:
    """Synthetic engineer output for --dry-run; lets the orchestrator demonstrate the
    full flow (file paths, branch name) without invoking Claude or mutating the repo."""
    title = task.title if task else decision.summary
    if task:
        plan = textwrap.dedent(
            f"""\
            Task: {task.title}

            Description:
            {task.description}

            Acceptance criteria:
            {task.acceptance_criteria or "(none)"}
            """
        ).strip()
    else:
        plan = decision.diff_or_plan or "(none)"
    return EngineerOutput(
        pr_title=f"[DRY RUN] {title[:60]}",
        pr_body=textwrap.dedent(
            f"""\
            DRY RUN — no LLM was invoked, no files would change.

            **Source Decision:** `{decision.id}`

            ## Plan
            {plan}
            """
        ).strip(),
        files=[
            FilePatch(
                path="DRYRUN.md",
                content=(
                    f"# Dry Run\n\nDecision: {decision.id}\n"
                    f"Project: {decision.project}\n"
                    f"This file would not actually be committed.\n"
                ),
                operation="create",
            )
        ],
    )


_CONFLICT_MARKER_RE = re.compile(r"^<{4,}\s|^={4,}\s|^>{4,}\s", re.MULTILINE)


def _gather_conflict_files(
    github: GitHubClient, pr_number: int, base_branch: str
) -> list[tuple[str, str]]:
    """Return up to MAX_CONTEXT_FILES (path, content) tuples where ``content``
    contains git-style conflict markers.

    Reads the file list from the PR, then fetches each file from the PR's
    head branch and scans for conflict markers. Truncates per-file content
    the same way ``_gather_context_files`` does so we stay within token
    budget.
    """
    out: list[tuple[str, str]] = []
    try:
        files = github.list_pull_request_files(number=pr_number)
    except (GitHubError, AttributeError):
        return out
    try:
        pr = github.get_pull_request(pr_number)
        head_ref = pr.head
    except GitHubError:
        head_ref = base_branch
    for f in files:
        if len(out) >= MAX_CONTEXT_FILES:
            break
        path = getattr(f, "path", None) or (f.get("filename") if isinstance(f, dict) else None)
        if not path:
            continue
        try:
            content = github.get_text_file(path=path, branch=head_ref)
        except GitHubError:
            continue
        if not content or not _CONFLICT_MARKER_RE.search(content):
            continue
        if len(content) > MAX_CONTEXT_CHARS_PER_FILE:
            content = content[:MAX_CONTEXT_CHARS_PER_FILE] + "\n\n[... truncated]\n"
        out.append((path, content))
    return out


def _gather_ci_failure_excerpt(github: GitHubClient, pr_number: int) -> str | None:
    """Pull a short excerpt of the failing CI check for the engineer's retry
    context. Best-effort; returns None on any error so the engineer can
    proceed using just the Acceptance criteria.
    """
    try:
        conclusion, details_url = github.get_pr_check_status(pr_number)
    except GitHubError:
        return None
    if conclusion != "failure":
        return None
    if not details_url:
        return None
    return (
        f"CI failed (latest run: {details_url}). The orchestrator can not "
        "fetch the run logs over the API without extra scopes — open the URL "
        "above to see the full trace. Diagnose the failure from the failing "
        "PR's diff + acceptance criteria and produce a targeted fix."
    )


def _gather_review_comments(github: GitHubClient, pr_number: int) -> list[dict[str, str]]:
    """Best-effort fetch of PR review comments for the in-place review-response
    path. Returns an empty list on any error so the engineer can proceed using
    just the acceptance criteria.
    """
    try:
        return github.list_issue_comments(number=pr_number)  # type: ignore[no-any-return]
    except Exception:  # noqa: BLE001
        return []


def _gather_context_files(github: GitHubClient, base_branch: str) -> list[tuple[str, str]]:
    """Best-effort fetch of likely-relevant context files. Skips on error."""
    out: list[tuple[str, str]] = []
    for path in CONTEXT_PRIORITY_PATHS:
        if len(out) >= MAX_CONTEXT_FILES:
            break
        try:
            sha = github.get_file_sha(path=path, branch=base_branch)
            if sha is None:
                continue
            r = github._request(
                "GET",
                f"/repos/{github.repo}/contents/{path}",
                params={"ref": base_branch},
            )
            body = r.json()
            content = base64.b64decode(body["content"]).decode("utf-8", errors="replace")
            if len(content) > MAX_CONTEXT_CHARS_PER_FILE:
                content = content[:MAX_CONTEXT_CHARS_PER_FILE] + "\n\n[... truncated]\n"
            out.append((path, content))
        except (GitHubError, ValueError, KeyError):
            continue
    return out


def _run_engineer_llm(
    decision: Decision,
    manifest: Manifest,
    eng_min: MinionAgent,
    github: GitHubClient,
    base_branch: str,
    api_key: str,
    task: SprintTask | None = None,
    retry_attempt: int = 0,
    is_conflict_resolution: bool = False,
    is_review_response: bool = False,
    existing_pr_number: int | None = None,
    preflight_failure: object | None = None,
) -> EngineerOutput:
    from crewai import Crew, Process, Task

    from minions.crews.factory import make_crewai_agent

    # Engineer output is whole-file JSON blobs (up to MAX_FILES_PER_PR files)
    # plus the PR title/body. The default 4096 max_tokens truncates the
    # structured payload mid-string for sprints with several files. Bump the
    # cap so Anthropic returns the whole EngineerOutput.
    eng = make_crewai_agent(eng_min, api_key=api_key, max_tokens=16384)

    context_files = _gather_context_files(github, base_branch)
    context_block = (
        "\n\n".join(f"### `{path}`\n```\n{content}\n```" for path, content in context_files)
        or "(no priority context files found)"
    )

    task_block = (
        textwrap.dedent(
            f"""\
            ## Refined Task
            Task id: {task.id}
            Sprint: {task.sprint_number}
            Category: {task.category}
            Owner: {task.owner_display_name or task.owner_agent_id} ({task.owner_role})
            Title: {task.title}
            Description:
            {task.description}

            Acceptance criteria:
            {task.acceptance_criteria or "(none)"}
            """
        )
        if task
        else ""
    )
    implementation_target = "the Refined Task above" if task else "ONE concrete item from the plan"

    # Retry / conflict context — if we're here because a prior attempt failed
    # CI or a merge conflict opened, pull the actual signal the engineer
    # needs to fix it: CI failure log + (for conflicts) the file list with
    # `<<<<<<< / =======` markers from the current branch tip.
    mode_block = ""
    if is_conflict_resolution and existing_pr_number is not None:
        conflict_files = _gather_conflict_files(github, existing_pr_number, base_branch)
        marker_block = (
            "\n\n".join(
                f"### `{path}` (has merge conflict markers)\n```\n{content}\n```"
                for path, content in conflict_files
            )
            or "(could not fetch conflict markers — proceed using the description)"
        )
        mode_block = textwrap.dedent(
            f"""\
            ## MODE: merge conflict resolution

            PR #{existing_pr_number} cannot merge: its branch has fallen behind
            `{base_branch}` and one or more files contain conflict markers
            (`<<<<<<<`, `=======`, `>>>>>>>`). Your job is to resolve those
            markers IN PLACE — keep the meaningful changes from both sides
            where possible, drop the markers, and produce the merged file
            contents.

            **DO NOT** open a new PR. DO NOT introduce unrelated changes.
            Touch ONLY the files with conflicts.

            ## Files with conflicts
            {marker_block}
            """
        )
    elif preflight_failure is not None:
        failed_step = getattr(getattr(preflight_failure, "failed_step", None), "step", "build")
        stderr_tail = getattr(getattr(preflight_failure, "failed_step", None), "stderr_tail", "")
        stdout_tail = getattr(getattr(preflight_failure, "failed_step", None), "stdout_tail", "")
        mode_block = textwrap.dedent(
            f"""\
            ## MODE: preflight retry (attempt #{retry_attempt})

            Your previous file set FAILED the project's own preflight on the
            **{failed_step}** step. The PR has NOT been opened. Read the
            failure output below, fix the root cause (often a missing
            dependency, an import that doesn't exist, or a type error), and
            produce a new file set that will pass.

            Common patterns:
            * If the error is "Cannot find module 'X'", add X to
              ``package.json`` (or `pyproject.toml`) AND include the lockfile
              update in your patch set.
            * If the error is a TypeScript type mismatch, fix the offending
              type — do NOT suppress with `any` or `@ts-ignore`.
            * If the error is from a test, fix the production code, not the
              assertion, unless the assertion is wrong.

            ## Preflight failure ({failed_step})
            stderr:
            ```
            {(stderr_tail or "(empty)")[-2000:]}
            ```
            stdout:
            ```
            {(stdout_tail or "(empty)")[-1000:]}
            ```
            """
        )
    elif is_review_response and existing_pr_number is not None:
        review_comments = _gather_review_comments(github, existing_pr_number)
        comments_block = (
            "\n\n".join(
                f"### {c.get('user', '?')} @ {c.get('created_at', '?')}\n{c.get('body', '')}"
                for c in review_comments[-5:]
            )
            or "(no review comments available — diagnose from acceptance criteria)"
        )
        mode_block = textwrap.dedent(
            f"""\
            ## MODE: review response (round #{retry_attempt})

            Crew reviewers requested changes on PR #{existing_pr_number}. Read
            their feedback below and produce the smallest possible commit on
            this same branch that addresses every legitimate concern.

            **DO NOT** open a new PR. DO NOT rewrite unrelated code. If a
            reviewer comment is out of scope for this PR, ignore it here — it
            will be re-classified separately.

            ## Reviewer feedback to address (last 5 comments)
            {comments_block}
            """
        )
    elif retry_attempt > 0 and existing_pr_number is not None:
        ci_log_excerpt = _gather_ci_failure_excerpt(github, existing_pr_number)
        mode_block = textwrap.dedent(
            f"""\
            ## MODE: CI fix retry (attempt #{retry_attempt})

            A previous engineer attempt landed on this branch but CI failed.
            The PR is #{existing_pr_number}. Read the failing CI excerpt below
            and produce the smallest possible commit that turns CI green.

            **DO NOT** rewrite unrelated code. DO NOT open a new PR. Push
            ONLY the files that need to change to fix the failing checks.

            ## Failing CI excerpt
            ```
            {ci_log_excerpt or "(CI log not available — diagnose from the Acceptance criteria)"}
            ```
            """
        )

    # Category-aware guidance — drives the engineer to actually implement
    # features/bugs in real source code instead of defaulting to docs.
    category = task.category if task else None
    category_hint = ""
    example_title = "docs: clarify setup steps"
    if category == "feature":
        category_hint = (
            "This is a FEATURE. Modify real source code (controllers, models, "
            "services, components, tests). Add the new capability described in "
            "the acceptance criteria. A docs-only PR for a feature is a FAILURE."
        )
        example_title = "feat: <one-line description of the new capability>"
    elif category == "bug":
        category_hint = (
            "This is a BUG. Find the root cause in the existing code and patch "
            "it. Add or update a test that proves the fix. A docs-only PR for a "
            "bug is a FAILURE."
        )
        example_title = "fix: <one-line description of what was broken>"
    elif category == "tech_debt":
        category_hint = (
            "This is TECH DEBT. Refactor / clean up existing code; do not change "
            "behaviour. Tests must still pass. README/CONTRIBUTING-only changes "
            "are not enough — touch the real code."
        )
        example_title = "refactor: <what you cleaned up>"
    elif category == "ops":
        category_hint = (
            "This is OPS work. Touch infra / deploy / configuration. CI config "
            "is still off-limits (see forbidden paths) — focus on app-level "
            "configuration."
        )
        example_title = "chore: <ops change>"
    elif category == "docs":
        category_hint = "This is a DOCS task. README, CONTRIBUTING, ADRs, runbooks are appropriate."
        example_title = "docs: <what was clarified>"

    description = textwrap.dedent(
        f"""\
        You are an ENGINEER implementing an APPROVED change in project '{manifest.name}'.
        Your code becomes a real PR that ships. Doc-only output for a feature
        or bug task is a failed implementation — the operator will reject it.

        {mode_block}

        ## Approved Decision
        Title: {decision.summary}
        Plan / rationale:
        {decision.diff_or_plan or decision.rationale}

        {task_block}

        ## Category guidance
        {category_hint or "Match your change to the type of work the plan asks for."}

        ## Repo context (read-only, for grounding)
        Default branch: {base_branch}
        {context_block}

        ## Your task
        Implement {implementation_target}. The PR must satisfy the acceptance
        criteria above. Code changes go to real source files (e.g. `src/...`,
        `app/...`, `lib/...`, `backend/...`, `frontend/...`), not README.

        Hard rules:
          - Do NOT modify CI config (`.github/`, `.gitlab/`, `ci/`).
          - Do NOT delete files.
          - Do NOT touch `.env*` (the forbidden-path filter rejects them anyway).
          - Stay within {MAX_FILES_PER_PR} files.
          - Each file is the COMPLETE post-change content (not a diff).
          - If you cannot ground the change in the repo context above, prefer
            a minimal, well-tested first cut over a sprawling rewrite.

        ## Output schema (EngineerOutput)
        {{
          "pr_title": "<conventional commit, e.g. '{example_title}'>",
          "pr_body": "<markdown body: context, change, files touched, testing approach>",
          "files": [
            {{"path": "...", "content": "...", "operation": "create"|"update"}}
          ]
        }}
        """
    )

    task = Task(
        description=description,
        agent=eng,
        expected_output=(
            f"An EngineerOutput JSON with pr_title, pr_body, and up to {MAX_FILES_PER_PR} files. "
            "Each file's content must be the complete file, not a diff."
        ),
        output_pydantic=EngineerOutput,
    )
    crew = Crew(agents=[eng], tasks=[task], process=Process.sequential, verbose=False)
    try:
        result = crew.kickoff()
    finally:
        clear_attribution()

    pydantic_out = getattr(result, "pydantic", None)
    if isinstance(pydantic_out, EngineerOutput):
        return pydantic_out
    return _parse_output_loose(str(result))


def _parse_output_loose(text: str) -> EngineerOutput:
    """Best-effort JSON parse from a text blob (CrewAI may not always return strict structured)."""
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    body = m.group(1) if m else text
    try:
        data = json.loads(body)
        return EngineerOutput.model_validate(data)
    except (ValueError, json.JSONDecodeError):
        return EngineerOutput(
            pr_title="(unparseable engineer output)",
            pr_body=(
                "Engineer output could not be parsed as EngineerOutput JSON.\n\n"
                "Raw output (truncated):\n```\n" + text[:1500] + "\n```"
            ),
            files=[],
        )


def _run_ttl_review(
    decision: Decision,
    manifest: Manifest,
    output: EngineerOutput,
    allowed_files: list[FilePatch],
    pr_url: str,
    api_key: str,
) -> str:
    from crewai import Crew, Process, Task

    from minions.crews.factory import make_crewai_agent

    ttl_min = build_named_agent(
        Role.TTL,
        project=manifest.name,
        manifest=manifest,
        cadence=manifest.cadence_profile,
    )
    ttl = make_crewai_agent(ttl_min, api_key=api_key)

    files_summary = "\n".join(
        f"- {f.operation} `{f.path}` ({len(f.content)} chars)" for f in allowed_files
    )

    description = textwrap.dedent(
        f"""\
        You are reviewing a draft PR opened by an Engineer in project '{manifest.name}'.

        ## PR
        URL: {pr_url}
        Title: {output.pr_title}

        Body:
        {output.pr_body}

        ## Files changed ({len(allowed_files)})
        {files_summary}

        ## Source Decision
        {decision.summary}
        Plan: {decision.diff_or_plan or decision.rationale}

        ## Your task
        Write a short PR review comment in Markdown. Cover:
          - Does the change address the approved Decision faithfully?
          - Any concerns: scope creep, missing tests, risky paths, breaking changes?
          - Verdict: **APPROVE** or **REQUEST_CHANGES** (only flag issues that would block merge).

        End with: "— {ttl_min.label} (Tech Team Lead)"
        """
    )

    task = Task(
        description=description,
        agent=ttl,
        expected_output="Short Markdown PR review with a verdict and signature.",
    )
    crew = Crew(agents=[ttl], tasks=[task], process=Process.sequential, verbose=False)
    return str(crew.kickoff())
