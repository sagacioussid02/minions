"""LLM-driven PR reviewer used by ``scheduled/pr_review_loop``.

Replaces the deterministic stub that defaulted to APPROVE. Each reviewer
role gets one LLM task with:

* The PR's actual diff (file list + per-file patches, capped).
* The last N issue comments — so prior review concerns are visible.
* The original Decision (intent grounding).
* The fresh CI conclusion (with the explicit rule that ``None`` →
  ``comment``, never ``approve``).

Falls back to the legacy stub on any LLM/parse failure so a flaky API
never crashes the review sweep.
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from typing import TYPE_CHECKING, Any

from minions.agents.roster import build_named_agent
from minions.models.roles import Role

if TYPE_CHECKING:
    from minions.crews.engineer_runs_store import (
        EngineerRunRecord,
        PRReviewerAssignment,
    )
    from minions.models.decision import Decision
    from minions.scheduled.pr_review_loop import StructuredReview

logger = logging.getLogger(__name__)

# Cap the diff context — large PRs get truncated but stay reviewable.
MAX_DIFF_CHARS = 12000
MAX_PATCH_CHARS_PER_FILE = 4000
MAX_COMMENTS = 8
MAX_COMMENT_CHARS = 1500


_ROLE_TO_CREW_ROLE: dict[str, Role] = {
    "ttl": Role.TTL,
    "qa_engineer": Role.QA_ENGINEER,
    "security_champion": Role.SECURITY_CHAMPION,
}


def run_pr_review(
    *,
    role: str,
    decision: Decision,
    record: EngineerRunRecord,
    reviewer: PRReviewerAssignment,
    pr_files: list[dict[str, Any]],
    prior_comments: list[dict[str, Any]],
    ci_conclusion: str | None,
    ci_details_url: str | None,
    api_key: str | None,
) -> StructuredReview:
    """Produce a real reviewer's verdict by reading the diff + prior comments.

    Stub-fallback contract: if ``api_key`` is None OR anything blows up
    during the LLM call, returns the legacy deterministic review so the
    review sweep itself never crashes.
    """
    from minions.scheduled.pr_review_loop import StructuredReview as _SRModel
    from minions.scheduled.pr_review_loop import _default_review_builder as _stub

    if api_key is None:
        return _stub(decision, record, reviewer, ci_conclusion, ci_details_url)

    crew_role = _ROLE_TO_CREW_ROLE.get(role)
    if crew_role is None:
        return _stub(decision, record, reviewer, ci_conclusion, ci_details_url)

    try:
        from crewai import Crew, Process, Task

        from minions.crews.factory import make_crewai_agent

        minion = build_named_agent(
            crew_role, project=decision.project, manifest=None,
        )
        agent = make_crewai_agent(minion, api_key=api_key, max_tokens=2000)

        description = _prompt(
            role=role,
            decision=decision,
            record=record,
            pr_files=pr_files,
            prior_comments=prior_comments,
            ci_conclusion=ci_conclusion,
            ci_details_url=ci_details_url,
        )
        task = Task(
            description=description,
            agent=agent,
            expected_output=(
                "A StructuredReview JSON with role, verdict "
                "(approve|request_changes|comment), summary, body."
            ),
            output_pydantic=_SRModel,
        )
        crew = Crew(
            agents=[agent], tasks=[task],
            process=Process.sequential, verbose=False,
        )
        result = crew.kickoff()
    except Exception:  # noqa: BLE001
        logger.warning(
            "pr_reviewer (%s) LLM dispatch failed; falling back to stub",
            role, exc_info=True,
        )
        return _stub(decision, record, reviewer, ci_conclusion, ci_details_url)

    parsed = getattr(result, "pydantic", None)
    if isinstance(parsed, _SRModel):
        return _ensure_role(parsed, role)

    loose = _parse_loose(str(result), role)
    if loose is not None:
        return loose

    logger.warning(
        "pr_reviewer (%s) output unparseable; falling back to stub", role,
    )
    return _stub(decision, record, reviewer, ci_conclusion, ci_details_url)


def _ensure_role(review: StructuredReview, role: str) -> StructuredReview:
    """LLMs sometimes ignore the role field. Pin it back to what we asked for."""
    if review.role != role:
        review = review.model_copy(update={"role": role})
    return review


def _parse_loose(text: str, role: str) -> StructuredReview | None:
    from minions.scheduled.pr_review_loop import StructuredReview as _SRModel

    block = _find_json_object(text)
    if block is None:
        return None
    try:
        payload = json.loads(block)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    payload.setdefault("role", role)
    try:
        return _SRModel.model_validate(payload)
    except (TypeError, ValueError):
        return None


_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _find_json_object(text: str) -> str | None:
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    m = _JSON_OBJECT.search(text)
    return m.group(0) if m else None


# ---------------------------------------------------------------------------
# Prompt construction.
# ---------------------------------------------------------------------------


_ROLE_FRAMING: dict[str, str] = {
    "ttl": (
        "Tech Team Lead. Care about: scope creep, missing tests for "
        "behavioral changes, hidden breaking changes, risky patterns."
    ),
    "qa_engineer": (
        "QA Engineer. Care about: test coverage for the change, edge cases, "
        "regression risk."
    ),
    "security_champion": (
        "Security Champion. Care about: secret handling, auth changes, "
        "input validation, dependency risk."
    ),
}


def _prompt(
    *,
    role: str,
    decision: Decision,
    record: EngineerRunRecord,
    pr_files: list[dict[str, Any]],
    prior_comments: list[dict[str, Any]],
    ci_conclusion: str | None,
    ci_details_url: str | None,
) -> str:
    framing = _ROLE_FRAMING.get(role, "Senior reviewer.")
    diff_block = _render_diff(pr_files)
    comments_block = _render_comments(prior_comments)
    ci_line = (
        f"ci_conclusion={ci_conclusion} ({ci_details_url})"
        if ci_conclusion is not None
        else "ci_conclusion=UNKNOWN (no GitHub check status visible to us)"
    )
    plan_excerpt = (decision.diff_or_plan or "(no plan attached)")[:2000]

    return textwrap.dedent(
        f"""\
        You are the {framing} reviewing a PR. Your job is NOT to rubber-stamp.

        ## Hard rules (must follow)
        1. If a prior review comment raised a SPECIFIC concern AND the diff
           below does NOT address it, your verdict MUST be `request_changes`.
           Cite the unaddressed concern verbatim in your summary.
        2. If `ci_conclusion=UNKNOWN`, your verdict MUST be `comment` (NOT
           approve). Say in the summary that checks aren't visible.
        3. `approve` is only allowed when (a) the diff is concretely safe
           for your area AND (b) you can name at least one filename from
           the diff in your summary AND (c) prior concerns are addressed
           or absent.

        ## Output
        Return a JSON object exactly matching this shape:
        {{
          "role": "{role}",
          "verdict": "approve" | "request_changes" | "comment",
          "summary": "<1-3 sentences naming specific files / concerns>",
          "body":    "<markdown comment to post on the PR>"
        }}

        ## Decision (intent)
        Project: {decision.project}
        Type:    {decision.type.value}
        Risk:    {decision.risk}
        Summary: {decision.summary}

        Plan excerpt:
        {plan_excerpt}

        ## CI status
        {ci_line}

        ## Prior review comments (most recent last)
        {comments_block}

        ## Diff (files + patches; long patches truncated)
        {diff_block}
        """
    )


def _render_diff(pr_files: list[dict[str, Any]]) -> str:
    if not pr_files:
        return "(no files reported by the PR API)"
    out: list[str] = []
    used = 0
    for f in pr_files:
        filename = f.get("filename") or "?"
        status = f.get("status") or "?"
        adds = f.get("additions") or 0
        dels = f.get("deletions") or 0
        patch = f.get("patch") or "(binary or empty patch)"
        if isinstance(patch, str) and len(patch) > MAX_PATCH_CHARS_PER_FILE:
            patch = patch[:MAX_PATCH_CHARS_PER_FILE] + "\n…(patch truncated)"
        block = (
            f"### `{filename}` ({status}, +{adds}/-{dels})\n"
            f"```diff\n{patch}\n```\n"
        )
        if used + len(block) > MAX_DIFF_CHARS:
            out.append("…(remaining files omitted to fit context)")
            break
        out.append(block)
        used += len(block)
    return "\n".join(out)


def _render_comments(comments: list[dict[str, Any]]) -> str:
    if not comments:
        return "(no prior review comments)"
    out: list[str] = []
    for c in comments[-MAX_COMMENTS:]:
        body = (c.get("body") or "").strip()
        if len(body) > MAX_COMMENT_CHARS:
            body = body[:MAX_COMMENT_CHARS] + "…(truncated)"
        out.append(
            f"--- {c.get('user', '?')} @ {c.get('created_at', '?')} ---\n"
            f"{body}"
        )
    return "\n\n".join(out)


__all__ = ["run_pr_review"]
