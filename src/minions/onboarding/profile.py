"""Project profiler — read-only signal collection for the planning crew.

`build_profile(manifest, *, github_client=None)` walks the project's local
working tree (every manifest has `source.path`) and optionally enriches with
GitHub data when a client is supplied. No LLM calls, no writes, no network
beyond the optional GitHub client.

The result is a JSON-serializable Pydantic model that the planning crew
consumes as grounding context (see crews/planning.py).
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from minions.github.client import GitHubClient
    from minions.models.manifest import Manifest


# Files we never read (mirrors safety preamble + engineer crew forbidden list).
_FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {".env", ".env.local", ".env.production", ".env.development"}
)
_FORBIDDEN_GLOBS: tuple[str, ...] = (
    ".env*",
    "*.pem",
    "*.key",
    "credentials*",
)
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
_SOURCE_EXTS: frozenset[str] = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".swift",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".sql",
        ".sh",
        ".vue",
        ".svelte",
        ".css",
        ".scss",
    }
)
_TODO_PATTERN = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
# tasks.md "| 1.1 | foo | ✅ Done | bar |" or "| 2.3 | … | ⬜ Todo | … |"
_TASKS_MD_DONE = re.compile(r"✅\s*Done", re.IGNORECASE)
_TASKS_MD_TODO = re.compile(r"⬜\s*Todo|\bTodo\b", re.IGNORECASE)


class PackageFile(BaseModel):
    path: str
    kind: Literal["npm", "python", "rust", "go", "ruby", "other"]
    dep_count: int | None = None  # best-effort; None if not parseable cheaply


class TasksMdSummary(BaseModel):
    path: str
    total: int
    done: int
    remaining: int


class IssueRef(BaseModel):
    number: int
    title: str
    labels: list[str] = Field(default_factory=list)
    age_days: int | None = None


class CommitRef(BaseModel):
    sha: str  # short
    subject: str
    age_days: int


class ProjectProfile(BaseModel):
    """Read-only snapshot of a managed project, fed to the planning crew."""

    project: str
    source_kind: Literal["github", "local"]
    source_path: str
    repo: str | None = None
    default_branch: str = "main"

    languages: dict[str, int] = Field(default_factory=dict)  # ext (no dot) → file count
    package_files: list[PackageFile] = Field(default_factory=list)
    ci_files: list[str] = Field(default_factory=list)
    has_ci: bool = False

    readme_excerpt: str | None = None
    tasks_md: TasksMdSummary | None = None
    todo_count: int = 0

    open_issues: list[IssueRef] = Field(default_factory=list)
    recent_commits: list[CommitRef] = Field(default_factory=list)

    generated_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))

    def to_planning_context(self) -> str:
        """Compact Markdown rendering for the planning crew prompt."""
        out: list[str] = [f"# Project profile — {self.project}", ""]
        out.append(f"- Source: `{self.source_kind}` at `{self.source_path}`")
        if self.repo:
            out.append(f"- Repo: `{self.repo}` (default branch: `{self.default_branch}`)")
        if self.languages:
            top = sorted(self.languages.items(), key=lambda kv: -kv[1])[:5]
            out.append("- Top languages: " + ", ".join(f"{e}({n})" for e, n in top))
        if self.package_files:
            out.append("- Package files:")
            for p in self.package_files:
                dc = f" — {p.dep_count} deps" if p.dep_count is not None else ""
                out.append(f"  - `{p.path}` ({p.kind}{dc})")
        out.append(
            f"- CI configured: {self.has_ci}"
            + (f" ({', '.join(self.ci_files)})" if self.ci_files else "")
        )
        if self.tasks_md:
            t = self.tasks_md
            out.append(
                f"- tasks.md: `{t.path}` — {t.done}/{t.total} done, **{t.remaining} remaining**"
            )
        out.append(f"- TODO/FIXME hits in source: {self.todo_count}")
        if self.readme_excerpt:
            out.append("\n## README excerpt\n")
            out.append(self.readme_excerpt)
        if self.open_issues:
            out.append("\n## Open issues (top 10)\n")
            for i in self.open_issues[:10]:
                age = f" — {i.age_days}d old" if i.age_days is not None else ""
                labels = f" [{', '.join(i.labels)}]" if i.labels else ""
                out.append(f"- #{i.number} {i.title}{labels}{age}")
        if self.recent_commits:
            out.append("\n## Recent commits\n")
            for c in self.recent_commits:
                out.append(f"- `{c.sha}` ({c.age_days}d) — {c.subject}")
        return "\n".join(out)


def build_profile(
    manifest: Manifest,
    *,
    github_client: GitHubClient | None = None,
    issue_limit: int = 10,
    commit_limit: int = 10,
) -> ProjectProfile:
    """Profile a managed project from its local working tree.

    `github_client` is optional; when provided and the manifest source is
    GitHub, open issues are fetched. Filesystem scanning is identical for
    both source kinds (every manifest has a local `source.path`).
    """
    root = Path(manifest.source.path).expanduser()
    if not root.exists() or not root.is_dir():
        raise ValueError(f"manifest source path does not exist or is not a directory: {root}")

    languages = _count_languages(root)
    package_files = _find_package_files(root)
    ci_files = _find_ci_files(root)
    readme = _read_readme(root)
    tasks_md = _parse_tasks_md(root)
    todo_count = _count_todos(root)
    recent_commits = _git_recent_commits(root, limit=commit_limit)

    open_issues: list[IssueRef] = []
    if github_client is not None and manifest.source.kind == "github":
        try:
            raw = github_client.list_open_issues(per_page=issue_limit)
            now = datetime.now(tz=UTC)
            for it in raw:
                age: int | None = None
                created = getattr(it, "created_at", None)
                if isinstance(created, datetime):
                    age = (now - created).days
                open_issues.append(
                    IssueRef(
                        number=it.number,
                        title=it.title,
                        labels=list(getattr(it, "labels", []) or []),
                        age_days=age,
                    )
                )
        except Exception:  # noqa: BLE001 — GitHub fetch is opportunistic enrichment
            open_issues = []

    return ProjectProfile(
        project=manifest.name,
        source_kind=manifest.source.kind,
        source_path=str(root),
        repo=manifest.source.repo,
        default_branch=manifest.source.default_branch,
        languages=languages,
        package_files=package_files,
        ci_files=ci_files,
        has_ci=bool(ci_files),
        readme_excerpt=readme,
        tasks_md=tasks_md,
        todo_count=todo_count,
        open_issues=open_issues,
        recent_commits=recent_commits,
    )


# ---------------------------------------------------------------------------
# Filesystem walkers — small, dumb, deterministic.
# ---------------------------------------------------------------------------


def _is_forbidden(path: Path) -> bool:
    name = path.name
    if name in _FORBIDDEN_NAMES:
        return True
    return any(path.match(g) for g in _FORBIDDEN_GLOBS)


def _walk_source(root: Path) -> list[Path]:
    """Yield source files under root, skipping vendored/build/secret paths."""
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if _is_forbidden(p):
            continue
        out.append(p)
    return out


def _count_languages(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in _walk_source(root):
        ext = p.suffix.lower()
        if ext in _SOURCE_EXTS:
            counts[ext.lstrip(".")] = counts.get(ext.lstrip("."), 0) + 1
    return counts


def _find_package_files(root: Path) -> list[PackageFile]:
    candidates: list[tuple[str, str]] = [
        ("package.json", "npm"),
        ("pyproject.toml", "python"),
        ("requirements.txt", "python"),
        ("Cargo.toml", "rust"),
        ("go.mod", "go"),
        ("Gemfile", "ruby"),
    ]
    found: list[PackageFile] = []
    for fname, kind in candidates:
        for p in root.rglob(fname):
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            rel = str(p.relative_to(root))
            dep_count = _count_deps(p, kind)
            found.append(PackageFile(path=rel, kind=kind, dep_count=dep_count))  # type: ignore[arg-type]
    return found


def _count_deps(path: Path, kind: str) -> int | None:
    try:
        if kind == "npm":
            data = json.loads(path.read_text())
            return len(data.get("dependencies", {})) + len(data.get("devDependencies", {}))
        if kind == "python" and path.name == "requirements.txt":
            return sum(
                1
                for line in path.read_text().splitlines()
                if line.strip() and not line.startswith("#")
            )
        # pyproject.toml / Cargo.toml / go.mod / Gemfile — skip; not worth a TOML parse here.
        return None
    except Exception:  # noqa: BLE001
        return None


def _find_ci_files(root: Path) -> list[str]:
    found: list[str] = []
    workflows = root / ".github" / "workflows"
    if workflows.is_dir():
        for p in workflows.glob("*.y*ml"):
            found.append(str(p.relative_to(root)))
    for fname in ("amplify.yml", ".circleci/config.yml", ".gitlab-ci.yml", "azure-pipelines.yml"):
        p = root / fname
        if p.exists():
            found.append(fname)
    return sorted(found)


def _read_readme(root: Path, *, max_chars: int = 800) -> str | None:
    for name in ("README.md", "README.rst", "README.txt", "README"):
        p = root / name
        if p.exists():
            try:
                text = p.read_text(errors="replace").strip()
                return text[:max_chars] + ("…" if len(text) > max_chars else "")
            except Exception:  # noqa: BLE001
                return None
    return None


def _parse_tasks_md(root: Path) -> TasksMdSummary | None:
    candidates = [
        root / "tasks.md",
        root / "openspec" / "tasks.md",
        root / "TASKS.md",
    ]
    for p in candidates:
        if not p.exists():
            continue
        try:
            text = p.read_text(errors="replace")
        except Exception:  # noqa: BLE001
            continue
        done = len(_TASKS_MD_DONE.findall(text))
        todo = len(_TASKS_MD_TODO.findall(text))
        total = done + todo
        if total == 0:
            continue
        return TasksMdSummary(
            path=str(p.relative_to(root)),
            total=total,
            done=done,
            remaining=todo,
        )
    return None


def _count_todos(root: Path, *, cap: int = 5000) -> int:
    """Count TODO/FIXME/XXX/HACK occurrences across source files. Capped."""
    hits = 0
    scanned = 0
    for p in _walk_source(root):
        if p.suffix.lower() not in _SOURCE_EXTS:
            continue
        scanned += 1
        if scanned > cap:
            break
        try:
            text = p.read_text(errors="replace")
        except Exception:  # noqa: BLE001
            continue
        hits += len(_TODO_PATTERN.findall(text))
    return hits


def _git_recent_commits(root: Path, *, limit: int) -> list[CommitRef]:
    """Best-effort `git log` against the project's working tree."""
    if not (root / ".git").exists():
        return []
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "log",
                f"-n{limit}",
                "--pretty=format:%h\t%ct\t%s",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return []
    now = datetime.now(tz=UTC)
    out: list[CommitRef] = []
    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        sha, ts_str, subject = parts
        try:
            ts = datetime.fromtimestamp(int(ts_str), tz=UTC)
        except ValueError:
            continue
        age = max(0, (now - ts).days)
        out.append(CommitRef(sha=sha, subject=subject, age_days=age))
    # Cap age field to a sane window so prompt context stays bounded.
    return [c if c.age_days < timedelta(days=3650).days else c for c in out]
