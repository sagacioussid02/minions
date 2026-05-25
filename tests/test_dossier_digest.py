"""Tests for src/minions/dossiers/digest.py."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from minions.dossiers.digest import (
    digest_from_draft,
    parse_sections,
    render_digest_for_planning,
)
from minions.models.dossier import DossierDraft, DossierStatus
from minions.models.manifest import load_manifest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _manifest(tmp_path: Path, name: str = "p"):
    src = REPO_ROOT / "projects" / "Demo.yaml"
    data = yaml.safe_load(src.read_text())
    data["name"] = name
    out = tmp_path / f"{name}.yaml"
    out.write_text(yaml.safe_dump(data))
    return load_manifest(out)


def _full_markdown(commit: str = "abc") -> str:
    return (
        f"---\ncommit_sha: {commit}\n---\n\n"
        "# Architecture\nThe spine of the system, see `src/x.py:1`.\n\n"
        "# Data model & flows\nStores: see `src/store.py:10`.\n\n"
        "# Infra & deploy topology\nVercel, see `vercel.json:1`.\n\n"
        "# Security posture\nNo SCA yet — see `package.json:1`.\n\n"
        "# Hot spots\nChecked `src/checkout.ts:42-71`.\n\n"
        "# Tech-debt register\n1. Rip out old auth `src/auth.ts:99`.\n\n"
        "# Recent incidents (last 90d)\n2026-05-19 checkout 500.\n\n"
        "# Open questions for operator\nShould we sunset the legacy admin?\n"
    )


def _draft(project: str = "p", commit: str = "abcdef0123456789") -> DossierDraft:
    return DossierDraft(
        project=project, commit_sha=commit,
        markdown=_full_markdown(commit),
        status=DossierStatus.MERGED,
        generated_at=datetime.now(UTC),
    )


def test_parse_sections_picks_up_all_headers() -> None:
    sections = parse_sections(_full_markdown())
    for key in (
        "architecture", "data", "infra", "hot_spots",
        "tech_debt", "security", "incidents", "questions",
    ):
        assert key in sections, key
    assert "src/x.py:1" in sections["architecture"]
    assert "src/auth.ts:99" in sections["tech_debt"]


def test_parse_sections_handles_missing_optional_subtitle() -> None:
    md = (
        "# Architecture\nx `a.py:1`.\n\n"
        "# Recent incidents\n2026-04-01 thing.\n"
    )
    sections = parse_sections(md)
    assert "incidents" in sections


def test_parse_sections_drops_empty_section_bodies() -> None:
    md = "# Architecture\n\n# Data model & flows\nsomething `a.py:1`.\n"
    sections = parse_sections(md)
    assert "architecture" not in sections  # empty body dropped
    assert "data" in sections


def test_digest_from_draft_populates_freshness_and_sections(tmp_path: Path) -> None:
    m = _manifest(tmp_path)
    digest = digest_from_draft(_draft(m.name), manifest=m)
    assert digest.project == m.name
    assert digest.freshness == "ok"
    assert "src/x.py:1" in digest.architecture_summary
    assert "src/checkout.ts:42-71" in digest.hot_spots_md
    assert "src/auth.ts:99" in digest.tech_debt_md
    assert "legacy admin" in digest.open_questions_md


def test_digest_marks_stale_freshness(tmp_path: Path) -> None:
    m = _manifest(tmp_path)
    old = _draft(m.name)
    old.generated_at = datetime.now(UTC) - timedelta(days=20)
    digest = digest_from_draft(old, manifest=m)
    assert digest.freshness == "stale"


def test_render_digest_includes_freshness_and_sections(tmp_path: Path) -> None:
    m = _manifest(tmp_path)
    digest = digest_from_draft(_draft(m.name), manifest=m)
    rendered = render_digest_for_planning(digest)
    assert "PROJECT_DOSSIER digest" in rendered
    assert "freshness=ok" in rendered
    assert "Hot spots" in rendered
    assert "Open questions for operator" in rendered
