"""Maintainer-bot skeleton — the external "scout" that keeps minions improving.

The bot is intentionally thin: it does **not** write code. It gathers
maintenance *signals*, ranks them, and surfaces a backlog. On
``--no-dry-run`` it hands the top candidates to the existing human-gated
pipeline via :func:`_propose` — which is left as a deliberate seam so the
skeleton is safe to run as-is.

Wire :func:`_propose` to one of:

  * ``approval.service.submit_for_approval(Decision(...), store, notifier)``
    — file a propose-only Decision the operator approves; the existing
    ``cron execute-approved`` + engineer crew then opens the **draft PR**.
  * ``crews.planning.run_planning(<self-repo>)`` — let the internal planning
    crew scope it; its output still lands as a Decision behind the gate.

Either way it stays propose-only: nothing merges, nothing touches ``main``,
and your normal approval gate is the trust boundary. Scheduled via
``.github/workflows/maintainer_bot.yml`` (dry-run by default).

Next signal sources to add (documented, not yet wired):
  * GitHub issues labelled ``bug`` / ``improvement`` on the managed repos
    (needs a ``GitHubClient``; see ``github/client.py``).
  * Recently failed CI on open engineer PRs (``engineer_runs.ci_conclusion``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from minions.models.question import QuestionStatus
from minions.questions.store_factory import QuestionStoreLike

# Noise control: never propose more than this per run regardless of backlog.
DEFAULT_MAX_PROPOSALS = 2

_PRIORITY_BY_QUESTION_STATUS = {
    QuestionStatus.ESCALATED: 100,
    QuestionStatus.OPEN: 50,
}


@dataclass(frozen=True)
class MaintainerSignal:
    """One actionable maintenance signal the bot found."""

    source: str  # "question" | "issue" | "ci" | ...
    project: str
    ref: str  # id / issue number / run id
    title: str
    priority: int  # higher = more urgent


@dataclass
class MaintainerReport:
    signals: list[MaintainerSignal] = field(default_factory=list)
    proposed: list[MaintainerSignal] = field(default_factory=list)
    dry_run: bool = True

    def to_markdown(self) -> str:
        lines = ["# Maintainer bot", ""]
        mode = "dry-run (report only)" if self.dry_run else "live (proposing)"
        lines.append(f"_Mode: {mode} — {len(self.signals)} signal(s) found._")
        lines.append("")
        if not self.signals:
            lines.append("No maintenance signals right now. Nothing to do. ✅")
            return "\n".join(lines)
        for s in self.signals:
            picked = "→ proposed" if s in self.proposed else ""
            lines.append(
                f"- **[{s.priority}]** ({s.source}/{s.project}) {s.title}  `{s.ref}` {picked}"
            )
        return "\n".join(lines)


def _gather_question_signals(questions_store: QuestionStoreLike) -> list[MaintainerSignal]:
    """Open + escalated Questions are the cheapest real signal: an agent is
    blocked and is asking for help. Escalated ones outrank open ones."""
    signals: list[MaintainerSignal] = []
    for q in questions_store.list_all():
        priority = _PRIORITY_BY_QUESTION_STATUS.get(q.status)
        if priority is None:  # answered / cancelled — skip
            continue
        signals.append(
            MaintainerSignal(
                source="question",
                project=q.project,
                ref=str(q.id)[:8],
                title=q.question[:120],
                priority=priority,
            )
        )
    return signals


def run_maintainer(
    *,
    questions_store: QuestionStoreLike,
    max_proposals: int = DEFAULT_MAX_PROPOSALS,
    dry_run: bool = True,
    now: datetime | None = None,
) -> MaintainerReport:
    """Gather + rank maintenance signals; propose the top ``max_proposals``.

    Safe to run repeatedly. In dry-run it only reports. In live mode it calls
    :func:`_propose` for the top candidates — a no-op seam until you wire it
    to the approval pipeline (see module docstring)."""
    _ = now or datetime.now(tz=UTC)
    signals = _gather_question_signals(questions_store)
    # TODO(next): extend with GitHub-issue + failing-CI signals here.
    signals.sort(key=lambda s: s.priority, reverse=True)

    report = MaintainerReport(signals=signals, dry_run=dry_run)
    for signal in signals[:max_proposals]:
        if not dry_run:
            _propose(signal)
        report.proposed.append(signal)
    return report


def _propose(signal: MaintainerSignal) -> None:
    """Hand a signal to the human-gated pipeline.

    SKELETON: intentionally a no-op so the bot is safe to enable before the
    wiring is done. Replace the body with a call to
    ``approval.service.submit_for_approval(...)`` (propose-only Decision) or
    ``crews.planning.run_planning(...)``. Keep it propose-only — the operator
    approves, and the existing execute-approved cron opens the draft PR.
    """
