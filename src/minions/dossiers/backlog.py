"""Wire a backlog proposal through the operator approval gate and onto GitHub.

Three jobs:

* :func:`build_backlog_proposal` — given a raw proposer crew output, run the
  GitHub-issue dedupe pass + apply the manifest's ``max_new_issues_per_cycle``
  cap. Returns the trimmed proposal plus the dedupe report (the operator
  sees both in the Decision body so they know what was filtered and why).
* :func:`file_backlog_decision` — file a ``DecisionType.OTHER`` Decision Record
  (kind ``backlog_proposal``, ``risk="medium"``) carrying the trimmed
  proposal on its extras.
* :func:`create_issues_for_decision` — worker invoked by
  ``minions backlog create``. Re-runs dedupe at create time (so issues filed
  during the approval window are still excluded), then opens one GitHub
  issue per remaining candidate, each with a ``minions/*`` label and the
  standard provenance footer.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from minions.approval.service import submit_for_approval
from minions.approval.store import DecisionStore
from minions.dossiers.dedupe import DedupeOutcome, dedupe_candidates
from minions.models.backlog import BacklogCandidate, BacklogProposal
from minions.models.decision import Decision, DecisionType
from minions.models.manifest import Manifest
from minions.notify.base import Notifier

if TYPE_CHECKING:
    from minions.github.client import GitHubClient
    from minions.github.models import Issue
    from minions.models.dossier import DossierDraft

PROPOSAL_KEY = "backlog_proposal"
KIND_KEY = "kind"
KIND_VALUE = "backlog_proposal"
DOSSIER_SHA_KEY = "dossier_commit_sha"

FOOTER_TEMPLATE = (
    "\n\n---\nFiled by minions discoverer from PROJECT_DOSSIER.md "
    "(commit {commit_sha})."
)


@dataclass(frozen=True)
class BuildResult:
    proposal: BacklogProposal
    dedupe: DedupeOutcome
    capped: int  # number of candidates dropped by the cycle cap


def build_backlog_proposal(
    *,
    raw: BacklogProposal,
    manifest: Manifest,
    existing_issues: list[Issue],
) -> BuildResult:
    """Run dedupe + cap. Pure — no GitHub mutations, no persistence."""
    dedupe = dedupe_candidates(raw.candidates, existing_issues=existing_issues)
    cap = max(0, manifest.dossier.max_new_issues_per_cycle)
    kept = dedupe.kept[:cap]
    capped = max(0, len(dedupe.kept) - cap)
    return BuildResult(
        proposal=BacklogProposal(
            project=raw.project,
            dossier_commit_sha=raw.dossier_commit_sha,
            candidates=kept,
        ),
        dedupe=dedupe,
        capped=capped,
    )


def is_backlog_proposal_decision(decision: Decision) -> bool:
    extra = getattr(decision, "model_extra", None) or {}
    return extra.get(KIND_KEY) == KIND_VALUE


def proposal_from_decision(decision: Decision) -> BacklogProposal | None:
    extra = getattr(decision, "model_extra", None) or {}
    raw = extra.get(PROPOSAL_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return BacklogProposal.model_validate(raw)
    except (TypeError, ValueError):
        return None


def file_backlog_decision(
    *,
    build: BuildResult,
    manifest: Manifest,
    dossier: DossierDraft,
    decision_store: DecisionStore,
    notifier: Notifier,
) -> Decision | None:
    """Submit a backlog proposal for operator approval.

    Returns ``None`` when no candidates survived dedupe + cap — there is
    nothing for the operator to approve and we should not pollute their
    inbox with empty proposals.
    """
    proposal = build.proposal
    if not proposal.candidates:
        return None

    body_lines = [
        "## Proposed GitHub issues",
        "",
        f"- Project: `{manifest.name}`",
        f"- Dossier commit: `{dossier.commit_sha}`",
        f"- Cycle cap: `{manifest.dossier.max_new_issues_per_cycle}` "
        f"({build.capped} dropped over cap)",
        f"- Dedupe: {len(build.dedupe.dropped)} dropped, "
        f"{len(build.dedupe.kept)} kept",
        "",
    ]
    if build.dedupe.dropped:
        body_lines.append("### Dropped by dedupe")
        for cand, reason in build.dedupe.dropped:
            body_lines.append(f"- _{cand.title}_ — {reason}")
        body_lines.append("")

    body_lines.append("### Candidates")
    for i, cand in enumerate(proposal.candidates, start=1):
        body_lines.append(
            f"#### {i}. [{cand.label()}] {cand.title}"
        )
        body_lines.append(cand.body)
        if cand.citations:
            body_lines.append(
                "Cites: " + ", ".join(f"`{c}`" for c in cand.citations)
            )
        body_lines.append("")

    decision = Decision(
        project=manifest.name,
        type=DecisionType.OTHER,
        risk="medium",
        summary=(
            f"backlog: {len(proposal.candidates)} new GitHub issue(s) "
            f"proposed for {manifest.name}"
        ),
        rationale=(
            "Backlog proposer crew converted dossier findings into "
            "user-visible work. Approve to open the listed issues with "
            "minions/* labels; the worker re-runs dedupe at create time."
        ),
        diff_or_plan="\n".join(body_lines),
        proposer_role="product_owner",
        proposer_agent_id=f"backlog_proposer@{manifest.name}",
        requested_by_role="product_owner",
    )
    decision.__pydantic_extra__ = {
        KIND_KEY: KIND_VALUE,
        PROPOSAL_KEY: proposal.model_dump(mode="json"),
        DOSSIER_SHA_KEY: dossier.commit_sha,
    }
    submit_for_approval(decision, store=decision_store, notifier=notifier)
    return decision


@dataclass(frozen=True)
class IssueCreated:
    title: str
    number: int
    html_url: str


@dataclass(frozen=True)
class CreateOutcome:
    created: list[IssueCreated]
    dropped: list[tuple[BacklogCandidate, str]]
    capped: int


def create_issues_for_decision(
    *,
    decision: Decision,
    manifest: Manifest,
    github: GitHubClient,
) -> CreateOutcome:
    """Worker: re-dedupe + create one GitHub issue per surviving candidate.

    Raises ``ValueError`` if the decision is not a backlog proposal or the
    payload is malformed. Per-issue creation errors are caught so a partial
    success still surfaces every issue we did open.
    """
    if not is_backlog_proposal_decision(decision):
        raise ValueError("decision is not a backlog proposal")
    proposal = proposal_from_decision(decision)
    if proposal is None:
        raise ValueError("decision has no parseable backlog proposal payload")

    existing = github.list_open_issues(per_page=50)
    fresh = dedupe_candidates(proposal.candidates, existing_issues=existing)
    cap = max(0, manifest.dossier.max_new_issues_per_cycle)
    survivors = fresh.kept[:cap]
    capped = max(0, len(fresh.kept) - cap)

    created: list[IssueCreated] = []
    for cand in survivors:
        body = cand.body + FOOTER_TEMPLATE.format(
            commit_sha=proposal.dossier_commit_sha
        )
        try:
            issue = github.create_issue(
                title=cand.title,
                body=body,
                labels=[cand.label()],
            )
        except Exception as e:  # noqa: BLE001 — surface per-issue failures
            fresh.dropped.append((cand, f"create_issue failed: {e}"))
            continue
        created.append(
            IssueCreated(
                title=cand.title,
                number=issue.number,
                html_url=issue.html_url,
            )
        )
    return CreateOutcome(
        created=created,
        dropped=list(fresh.dropped),
        capped=capped,
    )


def file_backlog_after_merge(
    *,
    raw: BacklogProposal,
    manifest: Manifest,
    dossier: DossierDraft,
    decision_store: DecisionStore,
    notifier: Notifier,
    github: GitHubClient | None,
) -> Decision | None:
    """Convenience for callers (e.g. dossier-sync) that need the whole flow."""
    existing: list[Issue] = []
    if github is not None:
        with suppress(Exception):
            existing = github.list_open_issues(per_page=50)
    build = build_backlog_proposal(
        raw=raw, manifest=manifest, existing_issues=existing
    )
    return file_backlog_decision(
        build=build,
        manifest=manifest,
        dossier=dossier,
        decision_store=decision_store,
        notifier=notifier,
    )


__all__ = [
    "BuildResult",
    "CreateOutcome",
    "DOSSIER_SHA_KEY",
    "FOOTER_TEMPLATE",
    "IssueCreated",
    "KIND_KEY",
    "KIND_VALUE",
    "PROPOSAL_KEY",
    "build_backlog_proposal",
    "create_issues_for_decision",
    "file_backlog_after_merge",
    "file_backlog_decision",
    "is_backlog_proposal_decision",
    "proposal_from_decision",
]
