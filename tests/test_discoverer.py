"""Tests for src/minions/crews/discoverer.py.

Cover the deterministic surfaces:

* the citation verifier (rejects bad paths, bad ranges, missing sections,
  out-of-order sections, dossiers with zero citations)
* dry-run returns None and never opens the LLM path
* override path produces a DossierDraft when the override passes the verifier
* override path raises DossierVerificationError when verifier rejects it
* tool-allowlist guard — the crew's CrewAI agents bind no tools

Real LLM dispatch (the crewai.Crew kickoff) is exercised in the end-to-end
test under Phase 9; here we use ``output_override`` so the suite stays free.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from minions.crews.discoverer import (
    CREW_VERSION,
    DossierVerificationError,
    RepoReadings,
    assemble_dossier,
    collect_repo_readings,
    run_discoverer,
    verify_dossier,
)
from minions.models.dossier import DossierSection, DossierStatus

# ---------------------------------------------------------------------------
# Test fixtures — a tiny throwaway git repo with one real source file.
# ---------------------------------------------------------------------------


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
    """Create a tiny initialized repo with one committed source file."""
    root = tmp_path / "repo"
    root.mkdir()
    src = root / "src"
    src.mkdir()
    (src / "x.py").write_text("# line 1\n" * 50)
    (root / "README.md").write_text("# tiny\n\nA tiny repo.\n")

    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@test"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)
    sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return root, sha


def _full_dossier(sha: str, citations: list[str] | None = None) -> str:
    """A dossier with all required sections + at least one citation per default."""
    citation_block = "\n".join(citations or ["See `src/x.py:1` for entry."])
    return (
        f"---\n"
        f"generated_at: 2026-05-20T00:00:00Z\n"
        f"commit_sha: {sha}\n"
        f"crew: {CREW_VERSION}\n"
        f"sections_present: [architecture, data, infra, security, "
        f"hot_spots, tech_debt, incidents, questions]\n"
        f"---\n\n"
        f"# Architecture\n{citation_block}\n\n"
        f"# Data model & flows\nSee `src/x.py:1`.\n\n"
        f"# Infra & deploy topology\nSee `src/x.py:1`.\n\n"
        f"# Security posture\nSee `src/x.py:1`.\n\n"
        f"# Hot spots\nSee `src/x.py:1`.\n\n"
        f"# Tech-debt register\nSee `src/x.py:1`.\n\n"
        f"# Recent incidents (last 90d)\nSee `src/x.py:1`.\n\n"
        f"# Open questions for operator\nNone.\n"
    )


# ---------------------------------------------------------------------------
# Verifier tests.
# ---------------------------------------------------------------------------


def test_verifier_accepts_well_formed_dossier(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)
    result = verify_dossier(_full_dossier(sha), root=root, commit_sha=sha)
    assert result.ok
    assert result.citations_failed == 0
    assert result.citations_checked >= 7


def test_verifier_rejects_unknown_path(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)
    md = _full_dossier(sha, citations=["See `src/nonexistent.py:5`."])
    result = verify_dossier(md, root=root, commit_sha=sha)
    assert not result.ok
    assert "src/nonexistent.py" in result.log
    assert result.citations_failed >= 1


def test_verifier_rejects_line_out_of_range(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)
    md = _full_dossier(sha, citations=["See `src/x.py:9999`."])
    result = verify_dossier(md, root=root, commit_sha=sha)
    assert not result.ok
    assert "9999" in result.log or "file length" in result.log


def test_verifier_rejects_end_before_start(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)
    md = _full_dossier(sha, citations=["See `src/x.py:20-10`."])
    result = verify_dossier(md, root=root, commit_sha=sha)
    assert not result.ok


def test_verifier_rejects_missing_section(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)
    md = _full_dossier(sha).replace("# Security posture\nSee `src/x.py:1`.\n\n", "")
    result = verify_dossier(md, root=root, commit_sha=sha)
    assert not result.ok
    assert "security" in result.log.lower() or "Security" in result.log


def test_verifier_rejects_out_of_order_sections(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)
    md = _full_dossier(sha)
    # Swap Security and Hot spots — order is now violated.
    original = (
        "# Security posture\nSee `src/x.py:1`.\n\n"
        "# Hot spots\nSee `src/x.py:1`.\n\n"
        "# Tech-debt register\nSee `src/x.py:1`."
    )
    swapped = (
        "# Hot spots\nSee `src/x.py:1`.\n\n"
        "# Tech-debt register\nSee `src/x.py:1`.\n\n"
        "# Security posture\nSee `src/x.py:1`."
    )
    md = md.replace(original, swapped)
    result = verify_dossier(md, root=root, commit_sha=sha)
    assert not result.ok


def test_verifier_rejects_zero_citations(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)
    md = (
        f"---\ncommit_sha: {sha}\n---\n\n"
        "# Architecture\nNone.\n\n# Data model & flows\nNone.\n\n"
        "# Infra & deploy topology\nNone.\n\n# Security posture\nNone.\n\n"
        "# Hot spots\nNone.\n\n# Tech-debt register\nNone.\n\n"
        "# Recent incidents (last 90d)\nNone.\n\n"
        "# Open questions for operator\nNone.\n"
    )
    result = verify_dossier(md, root=root, commit_sha=sha)
    assert not result.ok
    assert "no path:line citations" in result.log


# ---------------------------------------------------------------------------
# RepoReadings tests.
# ---------------------------------------------------------------------------


def test_collect_repo_readings_captures_commit_and_tree(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)

    class _M:
        name = "tiny"

    r = collect_repo_readings(_M(), root)  # type: ignore[arg-type]
    assert r.commit_sha == sha
    assert "src" in r.tree_summary
    assert "README" in r.readme_excerpt or "tiny" in r.readme_excerpt


def test_assemble_dossier_emits_frontmatter_and_body() -> None:
    readings = RepoReadings(
        project="p",
        root=Path("/tmp"),
        commit_sha="abc1234",
        tree_summary="",
        package_files="",
        ci_files="",
        infra_files="",
        readme_excerpt="",
        recent_commits="",
        high_churn_files="",
        todo_top_files="",
    )
    out = assemble_dossier(
        readings=readings,
        architect_md="# Architecture\nSee `x.py:1`.\n\n# Data model & flows\nSee `x.py:1`.",
        devops_md="# Infra & deploy topology\nSee `x.py:1`.",
        security_md="# Security posture\nSee `x.py:1`.",
        principal_md=(
            "# Hot spots\nSee `x.py:1`.\n\n"
            "# Tech-debt register\nSee `x.py:1`.\n\n"
            "# Recent incidents (last 90d)\nSee `x.py:1`.\n\n"
            "# Open questions for operator\nNone."
        ),
    )
    assert out.startswith("---\n")
    assert "commit_sha: abc1234" in out
    assert "# Architecture" in out
    assert "# Open questions" in out


# ---------------------------------------------------------------------------
# Crew dispatch tests (no LLM via output_override).
# ---------------------------------------------------------------------------


def test_dry_run_returns_none(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)
    readings = RepoReadings(
        project="tiny",
        root=root,
        commit_sha=sha,
        tree_summary="",
        package_files="",
        ci_files="",
        infra_files="",
        readme_excerpt="",
        recent_commits="",
        high_churn_files="",
        todo_top_files="",
    )

    class _M:
        name = "tiny"

    out = run_discoverer(_M(), dry_run=True, readings=readings)  # type: ignore[arg-type]
    assert out is None


def test_override_produces_draft_when_verifier_passes(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)
    readings = RepoReadings(
        project="tiny",
        root=root,
        commit_sha=sha,
        tree_summary="",
        package_files="",
        ci_files="",
        infra_files="",
        readme_excerpt="",
        recent_commits="",
        high_churn_files="",
        todo_top_files="",
    )

    class _M:
        name = "tiny"

    out = run_discoverer(  # type: ignore[arg-type]
        _M(),
        dry_run=False,
        readings=readings,
        output_override=_full_dossier(sha),
    )
    assert out is not None
    assert out.project == "tiny"
    assert out.commit_sha == sha
    assert out.status is DossierStatus.DRAFTED
    assert DossierSection.ARCHITECTURE in out.sections_present
    assert "all" in (out.verifier_log or "")


def test_override_raises_on_bad_citation(tmp_path: Path) -> None:
    root, sha = _init_repo(tmp_path)
    readings = RepoReadings(
        project="tiny",
        root=root,
        commit_sha=sha,
        tree_summary="",
        package_files="",
        ci_files="",
        infra_files="",
        readme_excerpt="",
        recent_commits="",
        high_churn_files="",
        todo_top_files="",
    )
    bad = _full_dossier(sha, citations=["See `src/missing.py:5`."])

    class _M:
        name = "tiny"

    with pytest.raises(DossierVerificationError) as excinfo:
        run_discoverer(  # type: ignore[arg-type]
            _M(), dry_run=False, readings=readings, output_override=bad
        )
    # The draft is attached for inspection but was NOT persisted.
    assert excinfo.value.draft.project == "tiny"
    assert excinfo.value.draft.commit_sha == sha


# ---------------------------------------------------------------------------
# Tool-allowlist guard: the discoverer's crewai.Agent objects bind no tools.
# ---------------------------------------------------------------------------


def test_discoverer_agents_have_no_tools() -> None:
    """make_crewai_agent (used by the discoverer) instantiates Agent without
    any tools=. The discoverer therefore cannot push, merge, or open PRs.
    """
    import inspect

    from minions.crews import factory

    src = inspect.getsource(factory.make_crewai_agent)
    assert "tools=" not in src, (
        "make_crewai_agent must not bind tools to discoverer agents. "
        "If a tools= kwarg is added, the discoverer needs a separate "
        "constructor or an explicit empty allowlist."
    )
