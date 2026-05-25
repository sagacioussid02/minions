"""Code Auditor — samples merged engineer-crew PRs for quality review.

Sampling rules (deterministic via hash of decision_id so the same PR never
gets audited twice and the rate stays predictable):

  | Decision risk | Audit probability |
  |---------------|-------------------|
  | high          | 100%              |
  | medium        | 50%               |
  | low           | 25%               |

Triggered from the daily cron's PR sync step: when an engineer run
transitions to ``pr_state == "merged"`` for the first time, ``maybe_audit``
gets called. Findings are written to the AuditFindingStore.

Cost discipline: per-PR cost ≈ $0.05–$0.20 in v0_frugal (Sonnet for the
auditor). At ~5 merges/week × 25% baseline rate ≈ 1–2 audits/week → ~$0.50/mo.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import textwrap
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import BaseModel, Field

from minions.activity import crew_run
from minions.agents.roster import build_named_agent
from minions.cost import clear_attribution, set_attribution
from minions.models.audit import AuditFinding, FindingCategory
from minions.models.decision import Decision
from minions.models.roles import Role
from minions.observability import add_metadata, observe_crew

if TYPE_CHECKING:
    from minions.config.portfolio import PortfolioConfig
    from minions.crews.engineer_runs_store import EngineerRunRecord
    from minions.github.client import GitHubClient

logger = logging.getLogger(__name__)


# Sampling rates per decision risk level (probability that a merged PR with
# this risk gets audited). Hash-based, deterministic per PR.
_SAMPLE_RATES: dict[str, int] = {"high": 100, "medium": 50, "low": 25}


def should_audit(decision_id: str, risk: str) -> bool:
    """Deterministic sample gate based on hash(decision_id) mod 100.

    Same input always yields the same answer — repeated daily-cron runs
    won't re-audit, and skipped PRs stay skipped (no flicker).
    """
    rate = _SAMPLE_RATES.get(risk, _SAMPLE_RATES["low"])
    if rate >= 100:
        return True
    h = int(hashlib.sha256(decision_id.encode("utf-8")).hexdigest(), 16) % 100
    return h < rate


class CodeAuditOutput(BaseModel):
    """Structured output expected from the Code Auditor agent."""

    severity: str = Field(description="advisory | medium | high")
    summary: str = Field(description="one-sentence verdict")
    evidence: str = Field(description="specific concerns with file references; use 'none' if clean")
    recommendation: str = Field(description="concrete next step; use 'no action' if clean")


@observe_crew("code_auditor")
def audit_pr(
    record: "EngineerRunRecord",
    decision: Decision,
    *,
    github: "GitHubClient",
    api_key: str | None = None,
    portfolio: "PortfolioConfig | None" = None,
    output_override: CodeAuditOutput | None = None,
    max_files: int = 10,
) -> AuditFinding | None:
    """Run the Code Auditor against a merged PR. Returns an AuditFinding or None.

    ``output_override`` short-circuits the LLM and uses the provided output —
    for tests. ``api_key=None`` without an override returns None (dry-run).
    """
    if record.pr_number is None or record.pr_url is None:
        return None

    add_metadata(
        crew="code_auditor",
        project=record.project,
        decision_id=record.decision_id,
        decision_risk=decision.risk,
        pr_number=record.pr_number,
    )

    # Pull the diff (capped at max_files) before invoking the LLM. Failure to
    # fetch the diff means we can't audit — return None rather than fabricate.
    try:
        files = github.list_pull_request_files(record.pr_number, per_page=max_files)
    except Exception as e:  # noqa: BLE001
        logger.warning("code auditor: cannot fetch PR files: %s", e)
        return None

    if not files and output_override is None:
        return None  # no diff to audit (binary-only PR, deletion-only, etc.)

    set_attribution(
        project=record.project,
        decision_id=record.decision_id,
        role="code_auditor",
    )
    try:
        with crew_run(
            crew="code_auditor",
            project=record.project,
            agents=["code_auditor"],
            decision_id=record.decision_id,
        ):
            if output_override is not None:
                out: CodeAuditOutput | None = output_override
            elif api_key is None:
                return None
            else:
                out = _run_llm(
                    record=record,
                    decision=decision,
                    files=files,
                    api_key=api_key,
                    portfolio=portfolio,
                )
    finally:
        clear_attribution()

    if out is None:
        return None

    severity_norm = out.severity.lower().strip()
    if severity_norm not in ("advisory", "medium", "high"):
        severity_norm = "advisory"

    return AuditFinding(
        source_project=record.project,
        source_decision_id=_decision_uuid(decision),
        source_pr_url=record.pr_url,
        category=FindingCategory.CODE,
        severity=severity_norm,  # type: ignore[arg-type]
        summary=out.summary[:300],
        evidence=out.evidence[:2000],
        recommendation=out.recommendation[:1000],
        auditor_role=Role.CODE_AUDITOR.value,
        auditor_agent_id=f"{Role.CODE_AUDITOR.value}@org",
    )


def _decision_uuid(decision: Decision) -> "UUID":
    """Decision.id may be UUID or str depending on how it was built; normalize."""
    return decision.id if isinstance(decision.id, UUID) else UUID(str(decision.id))


def _run_llm(
    *,
    record: "EngineerRunRecord",
    decision: Decision,
    files: list[dict[str, Any]],
    api_key: str,
    portfolio: "PortfolioConfig | None",
) -> CodeAuditOutput | None:
    from crewai import Crew, Process, Task

    from minions.crews.factory import make_crewai_agent

    auditor_min = build_named_agent(
        Role.CODE_AUDITOR,
        project=None,
        portfolio=portfolio,
        cadence="v0_frugal",
    )
    auditor = make_crewai_agent(auditor_min, api_key=api_key)

    diff_block = _render_diff(files)
    description = textwrap.dedent(
        f"""\
        You are the Code Auditor — independent reviewer reporting directly to the
        operator. Your job: review this merged PR for issues the engineer crew
        may have missed. Be specific. Cite filenames.

        ## PR context
        Project:    {record.project}
        Decision:   {record.decision_id} (type={decision.type.value}, risk={decision.risk})
        PR:         {record.pr_url}
        Title:      {decision.summary}
        Files:      {len(files)} changed

        ## Diff
        {diff_block}

        ## Your task
        Produce a CodeAuditOutput with FOUR fields:
          - severity: one of "advisory", "medium", "high".
            * high     = security/correctness bug, data loss, or privilege escalation.
            * medium   = quality issue likely to bite (missing tests, broken edge case,
                         performance regression, unclear contract).
            * advisory = minor / stylistic / clean review with nothing to flag.
          - summary: ONE sentence stating the verdict.
          - evidence: specific concerns with `file.py:line` references. If clean,
                      write "none — diff reviewed and passes" (this is fine).
          - recommendation: concrete next step ("revert", "add test for X", "no action").

        Be honest about clean PRs — most of them are fine. Don't invent issues.
        """
    )

    task = Task(
        description=description,
        agent=auditor,
        expected_output=(
            "A CodeAuditOutput with severity (str), summary (str), "
            "evidence (str), recommendation (str)."
        ),
        output_pydantic=CodeAuditOutput,
    )
    crew = Crew(agents=[auditor], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff()

    pydantic_out = getattr(result, "pydantic", None)
    if isinstance(pydantic_out, CodeAuditOutput):
        return pydantic_out
    return _parse_loose(str(result))


def _render_diff(files: list[dict[str, Any]], *, max_chars_per_file: int = 4000) -> str:
    """Compact diff representation for the LLM prompt. Truncates large patches."""
    parts: list[str] = []
    for f in files:
        header = (
            f"### {f['filename']} ({f.get('status', '')}, "
            f"+{f.get('additions', 0)}/-{f.get('deletions', 0)})"
        )
        patch = f.get("patch")
        if not patch:
            parts.append(f"{header}\n_(no patch — binary or oversized)_")
            continue
        if len(patch) > max_chars_per_file:
            patch = patch[:max_chars_per_file] + "\n... [truncated]"
        parts.append(f"{header}\n```diff\n{patch}\n```")
    return "\n\n".join(parts) if parts else "_(no files)_"


def _parse_loose(text: str) -> CodeAuditOutput | None:
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    body = m.group(1) if m else text
    try:
        return CodeAuditOutput.model_validate(json.loads(body))
    except (ValueError, json.JSONDecodeError):
        logger.warning("Code Auditor output unparseable: %r", text[:200])
        return None
