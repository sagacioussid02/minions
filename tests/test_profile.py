"""Tests for src/minions/onboarding/profile.py."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from minions.models.manifest import Manifest
from minions.onboarding import build_profile
from minions.onboarding.profile import (
    _count_deps,
    _count_languages,
    _count_todos,
    _find_ci_files,
    _find_package_files,
    _is_forbidden,
    _parse_tasks_md,
    _read_readme,
)


def _make_manifest(name: str, source_path: Path, kind: str = "local") -> Manifest:
    return Manifest.model_validate(
        {
            "name": name,
            "description": "test",
            "source": {"kind": kind, "path": str(source_path), "default_branch": "main"},
            "weekly_budget_usd": 1.0,
            "monthly_budget_usd": 4.0,
            "owner": "owner@example.com",
        }
    )


def test_is_forbidden_blocks_env_and_secret_files(tmp_path: Path) -> None:
    assert _is_forbidden(tmp_path / ".env")
    assert _is_forbidden(tmp_path / ".env.local")
    assert _is_forbidden(tmp_path / "site.pem")
    assert _is_forbidden(tmp_path / "credentials.json")
    assert not _is_forbidden(tmp_path / "src" / "main.py")


def test_count_languages_groups_by_extension(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x = 1\n")
    (tmp_path / "b.py").write_text("y = 2\n")
    (tmp_path / "c.ts").write_text("export const z = 3\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.js").write_text("nope")

    counts = _count_languages(tmp_path)
    assert counts == {"py": 2, "ts": 1}


def test_count_deps_npm(tmp_path: Path) -> None:
    pkg = tmp_path / "package.json"
    pkg.write_text(
        json.dumps({"dependencies": {"a": "1", "b": "2"}, "devDependencies": {"c": "3"}})
    )
    assert _count_deps(pkg, "npm") == 3


def test_count_deps_requirements_txt(tmp_path: Path) -> None:
    req = tmp_path / "requirements.txt"
    req.write_text("# header comment\nfoo==1.0\nbar>=2\n\n")
    assert _count_deps(req, "python") == 2


def test_count_deps_returns_none_on_unparseable(tmp_path: Path) -> None:
    bad = tmp_path / "package.json"
    bad.write_text("{not json")
    assert _count_deps(bad, "npm") is None


def test_find_package_files_skips_node_modules(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"a": "1"}}))
    nm = tmp_path / "node_modules" / "x"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text("{}")

    found = _find_package_files(tmp_path)
    assert len(found) == 1
    assert found[0].path == "package.json"
    assert found[0].kind == "npm"
    assert found[0].dep_count == 1


def test_find_ci_files_picks_up_workflows_and_amplify(tmp_path: Path) -> None:
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("name: ci")
    (tmp_path / "amplify.yml").write_text("version: 1")

    out = _find_ci_files(tmp_path)
    assert ".github/workflows/ci.yml" in out
    assert "amplify.yml" in out


def test_read_readme_truncates_with_ellipsis(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("a" * 2000)
    excerpt = _read_readme(tmp_path, max_chars=100)
    assert excerpt is not None
    assert excerpt.endswith("…")
    assert len(excerpt) == 101


def test_parse_tasks_md_counts_done_and_todo(tmp_path: Path) -> None:
    (tmp_path / "openspec").mkdir()
    (tmp_path / "openspec" / "tasks.md").write_text(
        "| 1.1 | x | ✅ Done | f |\n| 1.2 | y | ⬜ Todo | g |\n| 1.3 | z | ✅ Done | h |\n"
    )
    s = _parse_tasks_md(tmp_path)
    assert s is not None
    assert s.done == 2
    assert s.remaining == 1
    assert s.total == 3
    assert s.path == "openspec/tasks.md"


def test_parse_tasks_md_returns_none_when_absent(tmp_path: Path) -> None:
    assert _parse_tasks_md(tmp_path) is None


def test_count_todos_finds_markers_and_skips_vendored(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("# TODO: do thing\n# FIXME: oops\nx = 1\n")
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "vendored.js").write_text("// TODO this should not count")
    assert _count_todos(tmp_path) == 2


def test_build_profile_full_local_repo(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Hello\n")
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"a": "1"}}))
    (tmp_path / "main.ts").write_text("// TODO refactor\nexport const x = 1\n")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("name: ci")
    (tmp_path / "openspec").mkdir()
    (tmp_path / "openspec" / "tasks.md").write_text(
        "| 1 | a | ✅ Done | f |\n| 2 | b | ⬜ Todo | g |\n"
    )

    prof = build_profile(_make_manifest("demo", tmp_path))

    assert prof.project == "demo"
    assert prof.source_kind == "local"
    assert prof.languages == {"ts": 1}
    assert len(prof.package_files) == 1
    assert prof.has_ci is True
    assert prof.ci_files == [".github/workflows/ci.yml"]
    assert prof.tasks_md is not None
    assert prof.tasks_md.remaining == 1
    assert prof.todo_count == 1
    assert prof.readme_excerpt == "# Hello"
    assert prof.recent_commits == []  # no .git
    assert prof.open_issues == []  # no github client

    md = prof.to_planning_context()
    assert "demo" in md
    assert "tasks.md" in md
    assert "1 remaining" in md.replace("**", "")


def test_build_profile_raises_on_missing_path_and_no_repo(tmp_path: Path) -> None:
    """When neither path resolves nor repo is set, the resolver gives up."""
    from minions.working_tree import WorkingTreeError

    bogus = tmp_path / "does-not-exist"
    manifest = _make_manifest("demo", bogus)
    # _make_manifest doesn't populate source.repo — confirm that's still true.
    assert manifest.source.repo is None
    with pytest.raises(WorkingTreeError, match="cannot resolve"):
        build_profile(manifest, cache_dir=tmp_path / "clones")


def test_build_profile_with_git_recent_commits(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "README.md").write_text("# x")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "initial"], check=True)

    prof = build_profile(_make_manifest("demo", tmp_path))
    assert len(prof.recent_commits) == 1
    assert prof.recent_commits[0].subject == "initial"


def test_build_profile_skips_github_fetch_when_kind_local(tmp_path: Path) -> None:
    """Even with a (mock) client, kind=local should not call list_open_issues."""
    (tmp_path / "README.md").write_text("# x")

    class ExplodingClient:
        def list_open_issues(self, **_: object) -> list[object]:
            raise AssertionError("must not be called for kind=local")

    prof = build_profile(
        _make_manifest("demo", tmp_path, kind="local"), github_client=ExplodingClient()
    )  # type: ignore[arg-type]
    assert prof.open_issues == []
