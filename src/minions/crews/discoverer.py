"""Discoverer crew — produces and verifies ``PROJECT_DOSSIER.md`` for a project.

The crew does a *read-only* deep pass over the project working tree (already
cloned by ``working_tree.resolve_working_tree``) and emits a structured
markdown dossier with mandatory ``path:line`` citations.

Sequential roles (all already present in ``models/roles.py``):

    TEAM_ARCHITECT      → Architecture + Data flow sections
    CLOUD_DEVOPS        → Infra & deploy topology section
    SECURITY_CHAMPION   → Security posture section
    PRINCIPAL           → Hot spots + Tech-debt + Recent incidents sections

After the LLM tasks run, a deterministic synthesizer assembles the final
markdown (frontmatter + body) and a deterministic verifier walks every
``path:line`` claim and fails the run if any path does not exist at
``commit_sha`` or the line range is out of bounds.

By design, the discoverer has **zero git-write / PR / merge tools**: every
``crewai.Agent`` we instantiate is created via ``make_crewai_agent``, which
binds no tools. The PR open path lives in Phase 5 and routes through the
engineer crew's existing approval gate.
"""

from __future__ import annotations

import logging
import re
import subprocess
import textwrap
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from minions.activity import crew_run
from minions.agents.roster import build_named_agent
from minions.cost import clear_attribution, set_attribution
from minions.models.dossier import (
    REQUIRED_SECTION_ORDER,
    DossierDraft,
    DossierSection,
)
from minions.models.roles import Role
from minions.observability import add_metadata, observe_crew

if TYPE_CHECKING:
    from minions.models.manifest import Manifest

logger = logging.getLogger(__name__)

CREW_VERSION = "discoverer/v1"

# Matches ``path/to/file.ext:42`` or ``path/to/file.ext:42-71`` inside
# backticks. Used by the verifier to find every claim that must check out
# against ``commit_sha``. Backticks anchor the match so we don't trip on
# prose phrases that happen to contain a colon-digit substring.
_CITATION_PATTERN = re.compile(r"`([A-Za-z0-9_./\-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?`")


# ---------------------------------------------------------------------------
# Repo readings — deterministic, read-only walk fed into the LLM tasks.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RepoReadings:
    """Structured snapshot of a project's working tree at ``commit_sha``.

    All fields are derived from a read-only walk of the cloned working tree;
    no LLM calls and no GitHub API calls. Passed verbatim into the LLM task
    prompts so the model has concrete material to cite.
    """

    project: str
    root: Path
    commit_sha: str
    tree_summary: str  # depth-limited directory listing
    package_files: str  # newline-joined manifest paths + dep counts
    ci_files: str
    infra_files: str  # deploy configs (vercel.json, fly.toml, etc.)
    readme_excerpt: str
    recent_commits: str  # `git log` excerpt, last ~30 commits
    high_churn_files: str  # files most touched in last 90d
    todo_top_files: str  # files with the most TODO/FIXME/XXX hits


def collect_repo_readings(manifest: Manifest, root: Path) -> RepoReadings:
    """Build a RepoReadings snapshot for a resolved working tree.

    The discovery prompts feed entirely off this struct, so anything the LLM
    is expected to cite must originate here.
    """
    commit_sha = _git_head(root)
    return RepoReadings(
        project=manifest.name,
        root=root,
        commit_sha=commit_sha,
        tree_summary=_render_tree(root, max_depth=2, max_entries=200),
        package_files=_render_package_files(root),
        ci_files=_render_ci_files(root),
        infra_files=_render_infra_files(root),
        readme_excerpt=_render_readme(root, max_chars=1500),
        recent_commits=_render_recent_commits(root, limit=30),
        high_churn_files=_render_high_churn(root, days=90, limit=15),
        todo_top_files=_render_todo_top(root, limit=10),
    )


# ---------------------------------------------------------------------------
# Verifier — deterministic, no LLM. Fails the run on bad citations.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifierResult:
    ok: bool
    log: str
    citations_checked: int
    citations_failed: int


def verify_dossier(markdown: str, *, root: Path, commit_sha: str) -> VerifierResult:
    """Validate every ``path:line`` citation in the produced dossier.

    Rules (failures abort the crew run before any persistence):

    * Each non-frontmatter claim outside prose section headers MUST cite a
      ``path:line`` or ``path:start-end`` anchor (counted, not enforced
      per-claim — the spec asks for "every claim", but we cannot reliably
      classify what is a claim vs. a header; in practice the verifier fails
      when the citation count is zero across the body, *and* fails when any
      cited path is invalid).
    * Every cited path must exist at ``commit_sha`` (``git cat-file -e``).
    * Every cited line range must be within the file's line count at
      ``commit_sha``.
    * The required sections must appear in the required order.
    """
    log_lines: list[str] = []
    body = _strip_frontmatter(markdown)

    section_check = _check_section_order(body)
    if section_check:
        log_lines.append(f"section_order: {section_check}")
        return VerifierResult(False, "\n".join(log_lines), 0, 0)

    citations = list(_CITATION_PATTERN.finditer(body))
    if not citations:
        log_lines.append("no path:line citations found in dossier body")
        return VerifierResult(False, "\n".join(log_lines), 0, 0)

    failed = 0
    for m in citations:
        path, start, end = m.group(1), int(m.group(2)), m.group(3)
        end_line = int(end) if end else start
        problem = _check_citation(root, commit_sha, path, start, end_line)
        if problem:
            failed += 1
            log_lines.append(f"`{path}:{start}` -> {problem}")

    if failed:
        log_lines.insert(0, f"{failed}/{len(citations)} citations failed")
        return VerifierResult(False, "\n".join(log_lines), len(citations), failed)

    log_lines.append(f"all {len(citations)} citations resolved at {commit_sha[:8]}")
    return VerifierResult(True, "\n".join(log_lines), len(citations), 0)


def _check_section_order(body: str) -> str | None:
    """Return a problem description if required sections are missing/out of order."""
    expected_headers = [
        ("# Architecture", DossierSection.ARCHITECTURE),
        ("# Data model & flows", DossierSection.DATA),
        ("# Infra & deploy topology", DossierSection.INFRA),
        ("# Security posture", DossierSection.SECURITY),
        ("# Hot spots", DossierSection.HOT_SPOTS),
        ("# Tech-debt register", DossierSection.TECH_DEBT),
        ("# Recent incidents", DossierSection.INCIDENTS),
        ("# Open questions", DossierSection.QUESTIONS),
    ]
    cursor = 0
    for header, section in expected_headers:
        idx = body.find(header, cursor)
        if idx < 0:
            return f"missing or out-of-order section: '{header}' ({section.value})"
        cursor = idx + len(header)
    return None


def _check_citation(root: Path, commit_sha: str, path: str, start: int, end: int) -> str | None:
    """Return None if citation is valid; else a problem string."""
    if end < start:
        return f"end line {end} < start line {start}"
    if not (root / path).exists() and not _git_path_exists(root, commit_sha, path):
        return "path does not exist at commit_sha"
    line_count = _git_line_count(root, commit_sha, path)
    if line_count is None:
        # Fall back to live tree; if that fails too, treat as missing.
        try:
            live = (root / path).read_text(errors="replace")
            line_count = live.count("\n") + 1
        except (OSError, ValueError):
            return "path does not exist at commit_sha"
    if end > line_count:
        return f"end line {end} > file length {line_count}"
    return None


def _strip_frontmatter(markdown: str) -> str:
    if markdown.startswith("---\n"):
        end = markdown.find("\n---\n", 4)
        if end >= 0:
            return markdown[end + 5 :]
    return markdown


# ---------------------------------------------------------------------------
# Crew entrypoint.
# ---------------------------------------------------------------------------


@observe_crew("discoverer")
def run_discoverer(
    manifest: Manifest,
    *,
    api_key: str | None = None,
    dry_run: bool = True,
    readings: RepoReadings | None = None,
    output_override: str | None = None,
) -> DossierDraft | None:
    """Run the discoverer crew for a project. Returns a DossierDraft on success.

    ``dry_run`` (default) returns ``None`` after collecting readings — no LLM
    calls, no persistence. ``output_override`` short-circuits the LLM stage
    and is used by tests + the operator-supplied dossier path.
    """
    add_metadata(
        crew="discoverer",
        project=manifest.name,
        dry_run=dry_run,
    )

    if readings is None:
        from minions.working_tree import resolve_working_tree

        cache_dir = Path(__file__).resolve().parents[3] / "data" / "local" / "clones"
        root = resolve_working_tree(manifest, cache_dir=cache_dir)
        readings = collect_repo_readings(manifest, root)

    if dry_run and output_override is None:
        logger.info(
            "discoverer dry-run for %s at %s — no LLM, no persistence",
            manifest.name,
            readings.commit_sha[:8],
        )
        return None

    if output_override is not None:
        markdown = output_override
    else:
        if api_key is None:
            raise ValueError("api_key required when dry_run=False and no override")
        set_attribution(
            project=manifest.name,
            decision_id=None,
            role="team_architect",  # primary author; cost shows up under architect
        )
        try:
            with crew_run(
                crew="discoverer",
                project=manifest.name,
                agents=[
                    "team_architect",
                    "cloud_devops",
                    "security_champion",
                    "principal",
                ],
                decision_id=None,
            ) as run_id:
                markdown = _llm_produce_markdown(
                    manifest,
                    readings,
                    api_key,
                    run_id=run_id,
                )
        finally:
            clear_attribution()

    verifier = verify_dossier(markdown, root=readings.root, commit_sha=readings.commit_sha)
    sections_present = _sections_in_markdown(markdown)

    draft = DossierDraft(
        project=manifest.name,
        commit_sha=readings.commit_sha,
        markdown=markdown,
        sections_present=sections_present,
        verifier_log=verifier.log,
        crew_version=CREW_VERSION,
        generated_at=datetime.now(UTC),
    )

    if not verifier.ok:
        # Per spec: verifier failure aborts the run BEFORE persistence / PR open.
        # We return the draft for inspection so the caller can decide whether
        # to log it; we do NOT save it to the store here.
        logger.warning(
            "dossier verifier failed for %s: %s",
            manifest.name,
            verifier.log,
        )
        raise DossierVerificationError(verifier.log, draft=draft)

    return draft


class DossierVerificationError(RuntimeError):
    """Raised when the verifier rejects a produced dossier."""

    def __init__(self, log: str, *, draft: DossierDraft) -> None:
        super().__init__(log)
        self.draft = draft


# ---------------------------------------------------------------------------
# LLM stage — sequential CrewAI crew across the four roles.
# ---------------------------------------------------------------------------


def _llm_produce_markdown(
    manifest: Manifest,
    readings: RepoReadings,
    api_key: str,
    *,
    run_id: str | None = None,
) -> str:
    from crewai import Crew, Process, Task

    from minions.crews.factory import make_crewai_agent

    # Retain MinionAgents so transcript capture can pick up display_name.
    architect_min = build_named_agent(
        Role.TEAM_ARCHITECT,
        project=manifest.name,
        manifest=manifest,
    )
    devops_min = build_named_agent(
        Role.CLOUD_DEVOPS,
        project=manifest.name,
        manifest=manifest,
    )
    security_min = build_named_agent(
        Role.SECURITY_CHAMPION,
        project=manifest.name,
        manifest=manifest,
    )

    architect = make_crewai_agent(
        architect_min,
        api_key=api_key,
        max_tokens=4000,
    )
    devops = make_crewai_agent(
        devops_min,
        api_key=api_key,
        max_tokens=2000,
    )
    security = make_crewai_agent(
        security_min,
        api_key=api_key,
        max_tokens=2000,
    )
    principal_min = build_named_agent(
        Role.PRINCIPAL,
        project=manifest.name,
        manifest=manifest,
    )
    principal = make_crewai_agent(
        principal_min,
        api_key=api_key,
        max_tokens=4000,
    )

    context = _render_readings_for_prompt(readings)

    arch_task = Task(
        description=_prompt(
            "TEAM_ARCHITECT",
            sections=("# Architecture", "# Data model & flows"),
            context=context,
        ),
        agent=architect,
        expected_output=(
            "Two markdown sections (# Architecture, # Data model & flows). "
            "Every claim cites a file with `path:line` or `path:start-end`."
        ),
    )
    devops_task = Task(
        description=_prompt(
            "CLOUD_DEVOPS",
            sections=("# Infra & deploy topology",),
            context=context,
        ),
        agent=devops,
        expected_output="One markdown section (# Infra & deploy topology) with cited claims.",
    )
    security_task = Task(
        description=_prompt(
            "SECURITY_CHAMPION",
            sections=("# Security posture",),
            context=context,
        ),
        agent=security,
        expected_output="One markdown section (# Security posture) with cited claims.",
    )
    principal_task = Task(
        description=_prompt(
            "PRINCIPAL",
            sections=(
                "# Hot spots",
                "# Tech-debt register",
                "# Recent incidents (last 90d)",
                "# Open questions for operator",
            ),
            context=context,
        ),
        agent=principal,
        expected_output=(
            "Four markdown sections (Hot spots, Tech-debt register, Recent "
            "incidents, Open questions) with cited claims for the first three."
        ),
    )

    crew = Crew(
        agents=[architect, devops, security, principal],
        tasks=[arch_task, devops_task, security_task, principal_task],
        process=Process.sequential,
        verbose=False,
    )
    result = crew.kickoff()

    arch_md = _task_output(arch_task) or str(result)
    devops_md = _task_output(devops_task) or ""
    security_md = _task_output(security_task) or ""
    principal_md = _task_output(principal_task) or ""

    # Phase 3 of crew-transcripts: persist each role's output so the
    # Stage feed + per-run transcript page can render the working session.
    if run_id:
        from minions.transcripts.capture import record_task_default

        for seq, (agent_min, role_str, task) in enumerate(
            [
                (architect_min, "team_architect", arch_task),
                (devops_min, "cloud_devops", devops_task),
                (security_min, "security_champion", security_task),
                (principal_min, "principal_engineer", principal_task),
            ]
        ):
            record_task_default(
                run_id=run_id,
                project=manifest.name,
                crew="discoverer",
                agent_role=role_str,
                agent_display_name=agent_min.display_name,
                sequence=seq,
                role_in_conversation="task_output",
                task_output=task.output,
            )

    return assemble_dossier(
        readings=readings,
        architect_md=arch_md,
        devops_md=devops_md,
        security_md=security_md,
        principal_md=principal_md,
    )


def _task_output(task: object) -> str | None:
    """Best-effort extract of a Task's raw text output."""
    out = getattr(task, "output", None)
    if out is None:
        return None
    raw = getattr(out, "raw", None)
    if isinstance(raw, str):
        return raw
    return str(out)


def assemble_dossier(
    *,
    readings: RepoReadings,
    architect_md: str,
    devops_md: str,
    security_md: str,
    principal_md: str,
) -> str:
    """Concatenate per-role markdown into the final dossier body with frontmatter."""
    frontmatter = (
        "---\n"
        f"generated_at: {datetime.now(UTC).isoformat()}\n"
        f"commit_sha: {readings.commit_sha}\n"
        f"crew: {CREW_VERSION}\n"
        f"sections_present: "
        f"[{', '.join(s.value for s in REQUIRED_SECTION_ORDER)}]\n"
        "---\n\n"
    )
    body = "\n\n".join(
        s.strip() for s in (architect_md, devops_md, security_md, principal_md) if s and s.strip()
    )
    return frontmatter + body + "\n"


def _sections_in_markdown(markdown: str) -> list[DossierSection]:
    body = _strip_frontmatter(markdown)
    present: list[DossierSection] = []
    if "# Architecture" in body:
        present.append(DossierSection.ARCHITECTURE)
    if "# Data model & flows" in body:
        present.append(DossierSection.DATA)
    if "# Infra & deploy topology" in body:
        present.append(DossierSection.INFRA)
    if "# Hot spots" in body:
        present.append(DossierSection.HOT_SPOTS)
    if "# Tech-debt register" in body:
        present.append(DossierSection.TECH_DEBT)
    if "# Security posture" in body:
        present.append(DossierSection.SECURITY)
    if "# Recent incidents" in body:
        present.append(DossierSection.INCIDENTS)
    if "# Open questions" in body:
        present.append(DossierSection.QUESTIONS)
    return present


def _prompt(role: str, *, sections: tuple[str, ...], context: str) -> str:
    section_list = "\n".join(f"  - {s}" for s in sections)
    return textwrap.dedent(
        f"""\
        You are the {role} for this project. Produce the following dossier
        section(s) in markdown, in order, using only the repo readings below
        as source-of-truth:

        {section_list}

        ## Hard rules

        - Every concrete claim about the codebase MUST cite a file with
          backticks, in the form `path/to/file.ext:line` or
          `path/to/file.ext:start-end`. Example: "The webhook handler retries
          on 5xx (`src/webhook/handler.ts:42-71`)."
        - Do not fabricate paths. If you cannot cite a path from the readings
          below, do not make the claim.
        - Stay inside your assigned section(s). Do not emit other section
          headers.

        ## Repo readings

        {context}
        """
    )


def _render_readings_for_prompt(r: RepoReadings) -> str:
    return textwrap.dedent(
        f"""\
        Project: {r.project}
        Commit:  {r.commit_sha}

        ### Directory tree (depth 2)
        ```
        {r.tree_summary}
        ```

        ### Package / dependency files
        {r.package_files or "(none found)"}

        ### CI files
        {r.ci_files or "(none found)"}

        ### Infra / deploy files
        {r.infra_files or "(none found)"}

        ### README excerpt
        {r.readme_excerpt or "(no README)"}

        ### Recent commits (most recent first)
        {r.recent_commits or "(no git history)"}

        ### High-churn files (last 90d)
        {r.high_churn_files or "(no git history)"}

        ### Top TODO/FIXME-heavy files
        {r.todo_top_files or "(none)"}
        """
    )


# ---------------------------------------------------------------------------
# Filesystem + git helpers (no LLM).
# ---------------------------------------------------------------------------


_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".git",
        ".next",
        "dist",
        "build",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "coverage",
        "target",
        ".turbo",
        ".cache",
    }
)


def _git_head(root: Path) -> str:
    if not (root / ".git").exists():
        return "unknown"
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def _git_path_exists(root: Path, sha: str, path: str) -> bool:
    if sha == "unknown":
        return False
    try:
        subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", f"{sha}:{path}"],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except (subprocess.SubprocessError, OSError):
        return False


def _git_line_count(root: Path, sha: str, path: str) -> int | None:
    if sha == "unknown":
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "show", f"{sha}:{path}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.count("\n") + 1
    except (subprocess.SubprocessError, OSError):
        return None


def _render_tree(root: Path, *, max_depth: int, max_entries: int) -> str:
    lines: list[str] = []
    count = 0
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        parts = rel.parts
        if any(p in _SKIP_DIRS for p in parts):
            continue
        if len(parts) > max_depth:
            continue
        prefix = "  " * (len(parts) - 1)
        suffix = "/" if path.is_dir() else ""
        lines.append(f"{prefix}{parts[-1]}{suffix}")
        count += 1
        if count >= max_entries:
            lines.append("… (truncated)")
            break
    return "\n".join(lines)


def _render_package_files(root: Path) -> str:
    names = (
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "Gemfile",
    )
    found: list[str] = []
    for name in names:
        for p in root.rglob(name):
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            found.append(f"- `{p.relative_to(root)}`")
    return "\n".join(sorted(set(found)))


def _render_ci_files(root: Path) -> str:
    found: list[str] = []
    wf = root / ".github" / "workflows"
    if wf.is_dir():
        for p in sorted(wf.glob("*.y*ml")):
            found.append(f"- `{p.relative_to(root)}`")
    for fname in ("amplify.yml", ".circleci/config.yml", ".gitlab-ci.yml", "azure-pipelines.yml"):
        p = root / fname
        if p.exists():
            found.append(f"- `{fname}`")
    return "\n".join(found)


def _render_infra_files(root: Path) -> str:
    candidates = (
        "vercel.json",
        "fly.toml",
        "render.yaml",
        "Dockerfile",
        "docker-compose.yml",
        "next.config.js",
        "next.config.ts",
        "next.config.mjs",
        "firebase.json",
        "wrangler.toml",
        "serverless.yml",
    )
    found: list[str] = []
    for name in candidates:
        for p in root.rglob(name):
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            found.append(f"- `{p.relative_to(root)}`")
    return "\n".join(sorted(set(found)))


def _render_readme(root: Path, *, max_chars: int) -> str:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = root / name
        if p.exists():
            try:
                text = p.read_text(errors="replace").strip()
            except OSError:
                return ""
            return text[:max_chars] + ("…" if len(text) > max_chars else "")
    return ""


def _render_recent_commits(root: Path, *, limit: int) -> str:
    if not (root / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", f"-n{limit}", "--pretty=format:%h %s"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return out.stdout.strip()


def _render_high_churn(root: Path, *, days: int, limit: int) -> str:
    if not (root / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                f"--since={days}.days",
                "--name-only",
                "--pretty=format:",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    counts: dict[str, int] = {}
    for line in out.stdout.splitlines():
        name = line.strip()
        if not name or any(part in _SKIP_DIRS for part in Path(name).parts):
            continue
        counts[name] = counts.get(name, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])[:limit]
    return "\n".join(f"- `{p}` ({n} touches)" for p, n in ranked)


def _render_todo_top(root: Path, *, limit: int) -> str:
    pattern = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
    hits: dict[str, int] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb"}:
            continue
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        n = len(pattern.findall(text))
        if n:
            hits[str(p.relative_to(root))] = n
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])[:limit]
    return "\n".join(f"- `{path}` ({n})" for path, n in ranked)
