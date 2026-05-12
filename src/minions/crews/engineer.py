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
import re
import textwrap
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

if TYPE_CHECKING:
    from minions.models.manifest import Manifest


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


def slugify(text: str, max_len: int = 40) -> str:
    """Branch-friendly slug — alphanumerics + dashes, max 40 chars."""
    s = re.sub(r"[^a-zA-Z0-9_]+", "-", text.strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:max_len] or "change"


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
    if any(p.endswith(suffix) for suffix in FORBIDDEN_PATH_SUFFIXES):
        return True
    return False


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
    )
    set_attribution(project=manifest.name, decision_id=str(decision.id), role="engineer")

    # §6.5 budget guard — refuse engineer runs once a project has breached its
    # monthly cap. Dry runs are free, so they're always permitted (useful for
    # diagnosing what *would* have happened post-cap).
    if not dry_run:
        bstate = evaluate_budget(manifest, cost_log_path=cost_log_path)
        assert_can_run_engineer(bstate)

    if decision.status is not DecisionStatus.APPROVED:
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
        output = _dry_run_output(decision)
    else:
        if api_key is None:
            raise ValueError("api_key is required when dry_run=False")
        output = _run_engineer_llm(decision, manifest, eng_min, github, base_branch, api_key)

    allowed_files, rejected = filter_files(output.files)
    if not allowed_files:
        return EngineerResult(
            decision_id=str(decision.id),
            files_rejected=rejected,
            skipped=True,
            skip_reason=(
                "engineer produced no allowed file changes"
                + (f" ({len(rejected)} forbidden paths rejected)" if rejected else "")
            ),
            dry_run=dry_run,
        )

    summary_slug = slugify(decision.summary)
    branch_name = f"minions/eng/{summary_slug}"

    # Branch-exists check — refuse rather than overwrite.
    try:
        github.get_branch_ref(branch_name)
        return EngineerResult(
            decision_id=str(decision.id),
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
        return EngineerResult(
            decision_id=str(decision.id),
            pr_url=f"[DRY RUN] would open draft PR for branch {branch_name} ({len(allowed_files)} files)",
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
    ):
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
) -> EngineerResult:
    """Live-path body — extracted so the activity bracket above is one block."""
    github.create_branch(name=branch_name, base_sha=base_sha)
    for f in allowed_files:
        existing_sha = github.get_file_sha(path=f.path, branch=branch_name)
        github.update_file(
            branch=branch_name,
            path=f.path,
            content=f.content,
            message=(f"{output.pr_title}\n\n(role: engineer; decision: {decision.id})"),
            sha=existing_sha,
        )

    body = (
        output.pr_body
        + "\n\n---\n"
        + f"**Source Decision:** `{decision.id}`\n"
        + f"**Proposer:** {decision.proposer_display_name or decision.proposer_agent_id}"
    )
    pr = github.open_pull_request(
        title=output.pr_title,
        body=body,
        head=branch_name,
        base=base_branch,
        draft=True,
    )

    # §3.4 — operator-review notifier comment. Distinct from the TTL code review
    # below: this is a one-paragraph briefing for the human, posted whether or
    # not the LLM-based TTL review runs.
    operator_comment_posted = False
    try:
        github.comment_on_pull_request(
            number=pr.number,
            body=_build_operator_review_comment(decision, manifest, output, allowed_files),
        )
        operator_comment_posted = True
    except GitHubError:
        operator_comment_posted = False

    review_comment = None
    if api_key is not None:
        review_comment = _run_ttl_review(
            decision, manifest, output, allowed_files, pr.html_url, api_key
        )
        try:
            github.comment_on_pull_request(number=pr.number, body=review_comment)
        except GitHubError:
            # Don't fail the whole flow if the review comment can't be posted.
            review_comment = (review_comment or "") + "\n\n[note: comment post failed]"

    return EngineerResult(
        decision_id=str(decision.id),
        pr_url=pr.html_url,
        pr_number=pr.number,
        branch_name=branch_name,
        files_changed=[f.path for f in allowed_files],
        files_rejected=rejected,
        review_comment=review_comment,
        operator_comment_posted=operator_comment_posted,
        dry_run=False,
    )


# =============================================================================
# Operator review comment builder (§3.4)
# =============================================================================


def _build_operator_review_comment(
    decision: Decision,
    manifest: Manifest,
    output: EngineerOutput,
    allowed_files: list[FilePatch],
) -> str:
    """One-paragraph operator briefing posted as a PR comment.

    Distinct from the LLM TTL review — this fires whether or not Claude is
    available, and is aimed at the human rather than the codebase. Includes
    the source Decision id (so the operator can trace back to the email),
    the risk level, and a clear "Review and merge" call to action.
    """
    proposer = decision.proposer_display_name or decision.proposer_agent_id
    files_block = "\n".join(f"  - `{f.path}`" for f in allowed_files) or "  - _(none)_"
    return (
        "## 🤖 Operator review requested\n\n"
        f"This draft PR was opened by the **engineer crew** for project "
        f"`{manifest.name}` based on an approved Decision Record.\n\n"
        f"| | |\n"
        f"|---|---|\n"
        f"| **Decision** | `{decision.id}` |\n"
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


def _dry_run_output(decision: Decision) -> EngineerOutput:
    """Synthetic engineer output for --dry-run; lets the orchestrator demonstrate the
    full flow (file paths, branch name) without invoking Claude or mutating the repo."""
    return EngineerOutput(
        pr_title=f"[DRY RUN] {decision.summary[:60]}",
        pr_body=textwrap.dedent(
            f"""\
            DRY RUN — no LLM was invoked, no files would change.

            **Source Decision:** `{decision.id}`

            ## Plan from Decision
            {decision.diff_or_plan or "(none)"}
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

    description = textwrap.dedent(
        f"""\
        You are implementing an APPROVED change in project '{manifest.name}'.

        ## Approved Decision
        Title: {decision.summary}
        Plan / rationale:
        {decision.diff_or_plan or decision.rationale}

        ## Repo context (read-only, for grounding)
        Default branch: {base_branch}
        {context_block}

        ## Your task
        Produce a focused, MINIMAL change that addresses ONE concrete item from the plan.
        Strong preferences:
          - Prefer documentation, configuration, or small additive changes.
          - Do not modify CI config (anything under .github/, .gitlab/, ci/).
          - Do not delete files.
          - Do not touch secrets, credentials, or anything that looks like a key.
          - Stay within {MAX_FILES_PER_PR} files.
          - Each file is the COMPLETE post-change content (not a diff).

        ## Output schema (EngineerOutput)
        {{
          "pr_title": "<conventional commit format, e.g. 'docs: clarify setup steps'>",
          "pr_body": "<markdown body: context, change, testing approach>",
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
