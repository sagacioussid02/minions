"""Scaffold a minimal, valid project manifest for `minions projects add`."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from minions.models.manifest import Manifest

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def build_manifest_dict(
    repo: str,
    *,
    name: str | None = None,
    owner: str = "you@example.com",
    path: str | None = None,
    default_branch: str = "main",
    monthly_budget_usd: float = 4.0,
) -> dict:
    """Return a manifest dict that validates against :class:`Manifest`.

    Raises ValueError for a malformed ``owner/name`` repo.
    """
    if not _REPO_RE.match(repo):
        raise ValueError(f"repo must look like 'owner/name', got {repo!r}")
    project_name = name or repo.split("/", 1)[1]
    source: dict = {"kind": "github", "repo": repo, "default_branch": default_branch}
    if path:
        source["path"] = path
    data = {
        "name": project_name,
        "description": f"{repo} — managed by Minions.",
        "source": source,
        "weekly_budget_usd": round(monthly_budget_usd / 4, 2),
        "monthly_budget_usd": monthly_budget_usd,
        "cadence_profile": "v0_frugal",
        "delivery_targets": {"scope": "portfolio", "share_weight": 0.5},
        "team": {"intern": False, "engineers": 1},
        "owner": owner,
    }
    Manifest.model_validate(data)
    return data


def write_manifest(projects_dir: Path, data: dict) -> Path:
    """Write ``data`` to ``projects_dir/<slug>.yaml``; refuse to overwrite."""
    target = projects_dir / f"{slugify(data['name'])}.yaml"
    if target.exists():
        raise FileExistsError(str(target))
    projects_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(data, sort_keys=False))
    return target
