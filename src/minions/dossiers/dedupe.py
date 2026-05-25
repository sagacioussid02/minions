"""Dedupe backlog candidates against existing GitHub issues.

Two signals collapse a candidate onto an existing issue:

1. **Title similarity** ≥ ``TITLE_SIMILARITY_THRESHOLD``. Computed with
   ``difflib.SequenceMatcher`` on lowercased titles, ignoring leading
   ``[feature]`` / ``[bug]`` style brackets.
2. **Shared ``path:line`` anchor**. Extracted from the candidate's
   ``citations`` and from the existing issue body. Any overlap collapses.

Pure functions — no GitHub calls here; the caller supplies the existing
issue list. This keeps the policy testable and reusable for both proposal
time and create time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING

from minions.models.backlog import BacklogCandidate

if TYPE_CHECKING:
    from minions.github.models import Issue

TITLE_SIMILARITY_THRESHOLD: float = 0.6

_BRACKET_PREFIX = re.compile(r"^\s*\[[^\]]+\]\s*")
_CITATION = re.compile(r"`([A-Za-z0-9_./\-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?`")


def _normalize_title(title: str) -> str:
    return _BRACKET_PREFIX.sub("", title or "").strip().lower()


def _title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, _normalize_title(a), _normalize_title(b)).ratio()


def _citations_from_text(text: str | None) -> set[str]:
    if not text:
        return set()
    return {f"{m.group(1)}:{m.group(2)}" for m in _CITATION.finditer(text)}


@dataclass(frozen=True)
class DedupeOutcome:
    kept: list[BacklogCandidate]
    dropped: list[tuple[BacklogCandidate, str]]  # (candidate, reason)


def dedupe_candidates(
    candidates: list[BacklogCandidate],
    *,
    existing_issues: list[Issue],
) -> DedupeOutcome:
    """Drop candidates that overlap an existing open issue.

    Reasons returned in ``dropped`` are stable strings so the Decision body
    can quote them verbatim — operators see exactly why a candidate was
    filtered.
    """
    kept: list[BacklogCandidate] = []
    dropped: list[tuple[BacklogCandidate, str]] = []

    existing_titles = [(iss.number, iss.title) for iss in existing_issues]
    existing_cites = {iss.number: _citations_from_text(iss.body) for iss in existing_issues}

    for cand in candidates:
        cand_cites = {c.split(":", 1)[0] + ":" + c.split(":", 1)[1] for c in cand.citations}
        # Title similarity check.
        title_hit: tuple[int, float] | None = None
        for number, title in existing_titles:
            sim = _title_similarity(cand.title, title)
            if sim >= TITLE_SIMILARITY_THRESHOLD:
                title_hit = (number, sim)
                break
        if title_hit:
            number, sim = title_hit
            dropped.append((cand, f"title similarity {sim:.2f} with issue #{number}"))
            continue
        # Citation overlap check.
        overlap_hit: int | None = None
        for number, cites in existing_cites.items():
            if cand_cites & cites:
                overlap_hit = number
                break
        if overlap_hit is not None:
            shared = sorted(cand_cites & existing_cites[overlap_hit])
            dropped.append((cand, f"shared anchor {shared[0]} with issue #{overlap_hit}"))
            continue
        kept.append(cand)

    return DedupeOutcome(kept=kept, dropped=dropped)
