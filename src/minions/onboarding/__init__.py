"""Onboarding — read-only profiling of a managed project.

Produces a structured `ProjectProfile` that grounds the planning crew's
proposals in real repo signals (open issues, tasks.md gaps, dep freshness,
TODOs, recent activity) instead of just the manifest text.
"""

from minions.onboarding.profile import (
    CommitRef,
    IssueRef,
    PackageFile,
    ProjectProfile,
    TasksMdSummary,
    build_profile,
)

__all__ = [
    "CommitRef",
    "IssueRef",
    "PackageFile",
    "ProjectProfile",
    "TasksMdSummary",
    "build_profile",
]
