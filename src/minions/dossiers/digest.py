"""Dossier → DossierDigest projection used by the planning crew.

The discoverer crew writes a flat markdown body with predictable section
headers (``# Architecture``, ``# Data model & flows``, etc.; see
``REQUIRED_SECTION_ORDER`` in ``models/dossier.py``). Planning needs a
machine-extractable subset of those sections so the prompt context stays
bounded.

This module is the projection. Pure functions; no store access.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from minions.dossiers.freshness import compute_freshness
from minions.models.dossier import DossierDigest, DossierDraft
from minions.models.manifest import Manifest

_SECTION_HEADERS: list[tuple[str, str]] = [
    ("architecture", r"^# Architecture\s*$"),
    ("data", r"^# Data model & flows\s*$"),
    ("infra", r"^# Infra & deploy topology\s*$"),
    ("security", r"^# Security posture\s*$"),
    ("hot_spots", r"^# Hot spots\s*$"),
    ("tech_debt", r"^# Tech-debt register\s*$"),
    ("incidents", r"^# Recent incidents(?:.*)?\s*$"),
    ("questions", r"^# Open questions(?:.*)?\s*$"),
]


def _strip_frontmatter(markdown: str) -> str:
    if markdown.startswith("---\n"):
        end = markdown.find("\n---\n", 4)
        if end >= 0:
            return markdown[end + 5 :]
    return markdown


def parse_sections(markdown: str) -> dict[str, str]:
    """Return ``{key: section_body_md}`` for the known dossier sections.

    Unknown headers are ignored. Empty sections (header present, body blank)
    are dropped so callers can default-check by ``in``.
    """
    body = _strip_frontmatter(markdown)
    lines = body.splitlines()

    # Build a list of (line_index, key) for known headers, in source order.
    header_positions: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        for key, pattern in _SECTION_HEADERS:
            if re.match(pattern, line.strip()):
                header_positions.append((idx, key))
                break

    out: dict[str, str] = {}
    for i, (line_idx, key) in enumerate(header_positions):
        next_line = (
            header_positions[i + 1][0] if i + 1 < len(header_positions) else len(lines)
        )
        section_body = "\n".join(lines[line_idx + 1 : next_line]).strip()
        if section_body:
            out[key] = section_body
    return out


_MAX_PROSE_CHARS = 1500
_MAX_FOCUS_CHARS = 3000


def _truncate(text: str, *, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n…(truncated)"


def digest_from_draft(
    draft: DossierDraft,
    *,
    manifest: Manifest,
    repo_root: Path | None = None,
    now: datetime | None = None,
) -> DossierDigest:
    """Project a merged ``DossierDraft`` into the planning-bound ``DossierDigest``.

    Hot spots / Tech-debt / Recent incidents / Open questions are kept verbatim
    (capped at ``_MAX_FOCUS_CHARS``) because those are the sections planning
    leans on most. Architecture / Data / Infra are truncated to short
    summaries.
    """
    freshness = compute_freshness(
        draft,
        overrides=manifest.dossier.freshness_overrides,
        repo_root=repo_root,
        now=now or datetime.now(UTC),
    )
    sections = parse_sections(draft.markdown)
    return DossierDigest(
        project=draft.project,
        commit_sha=draft.commit_sha,
        generated_at=draft.generated_at,
        freshness=freshness.label,
        hot_spots_md=_truncate(sections.get("hot_spots", ""), limit=_MAX_FOCUS_CHARS),
        tech_debt_md=_truncate(sections.get("tech_debt", ""), limit=_MAX_FOCUS_CHARS),
        recent_incidents_md=_truncate(
            sections.get("incidents", ""), limit=_MAX_FOCUS_CHARS
        ),
        open_questions_md=_truncate(
            sections.get("questions", ""), limit=_MAX_FOCUS_CHARS
        ),
        architecture_summary=_truncate(
            sections.get("architecture", ""), limit=_MAX_PROSE_CHARS
        ),
        data_summary=_truncate(sections.get("data", ""), limit=_MAX_PROSE_CHARS),
        infra_summary=_truncate(sections.get("infra", ""), limit=_MAX_PROSE_CHARS),
    )


def render_digest_for_planning(digest: DossierDigest) -> str:
    """Compact Markdown rendering attached to the planning prompt."""
    out: list[str] = [
        f"# PROJECT_DOSSIER digest — {digest.project} "
        f"(commit {digest.commit_sha[:8]}, freshness={digest.freshness})",
        "",
    ]
    if digest.architecture_summary:
        out.append("## Architecture\n" + digest.architecture_summary)
    if digest.data_summary:
        out.append("\n## Data\n" + digest.data_summary)
    if digest.infra_summary:
        out.append("\n## Infra\n" + digest.infra_summary)
    if digest.hot_spots_md:
        out.append("\n## Hot spots\n" + digest.hot_spots_md)
    if digest.tech_debt_md:
        out.append("\n## Tech-debt register\n" + digest.tech_debt_md)
    if digest.recent_incidents_md:
        out.append("\n## Recent incidents\n" + digest.recent_incidents_md)
    if digest.open_questions_md:
        out.append("\n## Open questions for operator\n" + digest.open_questions_md)
    return "\n".join(out)


__all__ = [
    "digest_from_draft",
    "parse_sections",
    "render_digest_for_planning",
]
