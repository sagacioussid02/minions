"""Tests for the working-tree resolver.

We exercise three paths:
- ``source.path`` exists → use it as-is, no clone.
- ``source.path`` is missing but ``source.repo`` is set → fall back to clone.
- Neither set → raise WorkingTreeError.

The "clone" tests use a *local* upstream repo created with `git init`, so we
hit a real git executable but never reach the network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from minions.models.manifest import (
    AutoApproveRule,
    DeliveryTargets,
    Manifest,
    ManifestSource,
    NeverAutoApproveRule,
    TeamOverrides,
    TierOverrides,
)
from minions.working_tree import WorkingTreeError, resolve_working_tree


def _manifest(*, name: str, path: str | None, repo: str | None) -> Manifest:
    return Manifest(
        name=name,
        description="x",
        source=ManifestSource(kind="github", path=path, repo=repo),
        weekly_budget_usd=1.0,
        monthly_budget_usd=4.0,
        cadence_profile="v0_frugal",
        delivery_targets=DeliveryTargets(scope="portfolio", share_weight=0.5),
        team=TeamOverrides(),
        tier_overrides=TierOverrides(),
        auto_approve=[AutoApproveRule(**{"class": "docs_only"})],
        never_auto_approve=[NeverAutoApproveRule(**{"class": "auth_change"})],
        owner="x@y.com",
    )


def _seed_upstream(path: Path, *, branch: str = "main") -> None:
    """Create a real git repo on disk with one commit on ``branch``."""
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "README.md").write_text("# upstream")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True)


def test_resolves_to_local_path_when_present(tmp_path: Path) -> None:
    local = tmp_path / "local"
    local.mkdir()
    m = _manifest(name="demo", path=str(local), repo="ignored/whatever")
    resolved = resolve_working_tree(m, cache_dir=tmp_path / "cache")
    assert resolved == local
    # No cache dir was created — no clone happened.
    assert not (tmp_path / "cache").exists()


def test_clones_when_path_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    upstream = tmp_path / "upstream"
    _seed_upstream(upstream)

    # Patch the URL-builder so we clone the local upstream rather than github.com.
    import minions.working_tree as wt

    monkeypatch.setattr(
        wt, "_clone_url", lambda repo, token: f"file://{upstream}", raising=True
    )

    m = _manifest(name="demo", path=None, repo="org/demo")
    resolved = resolve_working_tree(m, cache_dir=tmp_path / "cache", token=None)

    assert resolved == tmp_path / "cache" / "demo"
    assert (resolved / "README.md").read_text() == "# upstream"
    assert (resolved / ".git").is_dir()


def test_re_runs_use_fetch_not_reclone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = tmp_path / "upstream"
    _seed_upstream(upstream)

    import minions.working_tree as wt

    monkeypatch.setattr(
        wt, "_clone_url", lambda repo, token: f"file://{upstream}", raising=True
    )

    m = _manifest(name="demo", path=None, repo="org/demo")
    cache = tmp_path / "cache"

    first = resolve_working_tree(m, cache_dir=cache, token=None)
    first_inode = first.stat().st_ino

    # Add a new commit upstream.
    (upstream / "TASKS.md").write_text("# tasks")
    subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
    subprocess.run(["git", "-C", str(upstream), "commit", "-q", "-m", "tasks"], check=True)

    second = resolve_working_tree(m, cache_dir=cache, token=None)

    assert second == first
    assert second.stat().st_ino == first_inode  # same dir, not re-cloned
    assert (second / "TASKS.md").exists()  # but updated


def test_raises_when_neither_path_nor_repo(tmp_path: Path) -> None:
    m = _manifest(name="demo", path=None, repo=None)
    with pytest.raises(WorkingTreeError, match="cannot resolve"):
        resolve_working_tree(m, cache_dir=tmp_path / "cache")


def test_falls_back_to_repo_when_path_does_not_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = tmp_path / "upstream"
    _seed_upstream(upstream)

    import minions.working_tree as wt

    monkeypatch.setattr(
        wt, "_clone_url", lambda repo, token: f"file://{upstream}", raising=True
    )

    bogus = tmp_path / "does-not-exist-on-this-host"
    m = _manifest(name="demo", path=str(bogus), repo="org/demo")
    resolved = resolve_working_tree(m, cache_dir=tmp_path / "cache", token=None)

    assert resolved == tmp_path / "cache" / "demo"
    assert (resolved / "README.md").exists()
