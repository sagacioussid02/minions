"""Backlog proposer crew — turns a merged dossier into GitHub-issue candidates.

Sequential CrewAI crew, two roles:

* **PRODUCT_OWNER** — reads the dossier's Tech-debt register, Hot spots, and
  Open questions sections, and proposes user-visible candidates with kind +
  citations.
* **SR_ENGINEER** — sanity-checks each candidate against the live repo,
  drops anything where the cited path no longer exists at HEAD, and emits
  the final ``BacklogProposal``.

The actual GitHub-issue dedupe pass runs *after* the crew (in
``dossiers.backlog.build_backlog_proposal``) so the dedupe rules stay
deterministic and testable.

The crew never opens issues; that is gated through the operator's approval
of a ``DOSSIER_BACKLOG`` Decision (see ``dossiers/backlog.py``).
"""

from __future__ import annotations

import json
import logging
import re
import textwrap
from typing import TYPE_CHECKING

from minions.activity import crew_run
from minions.agents.roster import build_named_agent
from minions.cost import clear_attribution, set_attribution
from minions.models.backlog import BacklogCandidate, BacklogKind, BacklogProposal
from minions.models.roles import Role
from minions.observability import add_metadata, observe_crew

if TYPE_CHECKING:
    from minions.models.dossier import DossierDraft
    from minions.models.manifest import Manifest

logger = logging.getLogger(__name__)

CREW_VERSION = "backlog_proposer/v1"


@observe_crew("backlog_proposer")
def run_backlog_proposer(
    manifest: Manifest,
    dossier: DossierDraft,
    *,
    api_key: str | None = None,
    dry_run: bool = True,
    output_override: BacklogProposal | None = None,
    max_candidates: int = 8,
) -> BacklogProposal | None:
    """Produce a (raw, pre-dedupe) ``BacklogProposal`` from a merged dossier.

    ``dry_run`` (default) returns ``None`` — no LLM, no persistence.
    ``output_override`` short-circuits the LLM stage (used by tests + the
    operator-supplied override path).

    The caller is responsible for running the GitHub dedupe pass + cap before
    handing the proposal to the operator (see
    ``dossiers/backlog.build_backlog_proposal``).
    """
    add_metadata(
        crew="backlog_proposer",
        project=manifest.name,
        dossier_commit_sha=dossier.commit_sha,
        dry_run=dry_run,
    )

    if dry_run and output_override is None:
        logger.info(
            "backlog_proposer dry-run for %s at %s — no LLM",
            manifest.name,
            dossier.commit_sha[:8],
        )
        return None

    if output_override is not None:
        return output_override

    if api_key is None:
        raise ValueError("api_key required when dry_run=False and no override")

    set_attribution(project=manifest.name, decision_id=None, role="product_owner")
    try:
        with crew_run(
            crew="backlog_proposer",
            project=manifest.name,
            agents=["product_owner", "senior_engineer"],
            decision_id=None,
        ) as run_id:
            raw = _llm_propose(manifest, dossier, api_key, max_candidates)
            if raw is not None:
                from minions.transcripts.capture import record_crew_summary

                record_crew_summary(
                    run_id=run_id,
                    project=manifest.name,
                    crew="backlog_proposer",
                    agent_role="product_owner",
                    result=raw,
                    role_in_conversation="task_output",
                )
    finally:
        clear_attribution()

    return raw


def _llm_propose(
    manifest: Manifest,
    dossier: DossierDraft,
    api_key: str,
    max_candidates: int,
) -> BacklogProposal:
    from crewai import Crew, Process, Task

    from minions.crews.factory import make_crewai_agent

    po = make_crewai_agent(
        build_named_agent(Role.PRODUCT_OWNER, project=manifest.name, manifest=manifest),
        api_key=api_key,
        max_tokens=3000,
    )
    sre = make_crewai_agent(
        build_named_agent(Role.SR_ENGINEER, project=manifest.name, manifest=manifest),
        api_key=api_key,
        max_tokens=3000,
    )

    propose_task = Task(
        description=_propose_prompt(manifest, dossier, max_candidates),
        agent=po,
        expected_output=(
            "A JSON array of up to "
            f"{max_candidates} candidates, each with title, body, kind "
            "(feature|bug|tech_debt|security), source_section, "
            "and citations (path:line list)."
        ),
    )
    review_task = Task(
        description=_review_prompt(manifest, dossier, max_candidates),
        agent=sre,
        expected_output="A JSON array — the same shape, after sanity-checks.",
        context=[propose_task],
    )

    crew = Crew(
        agents=[po, sre],
        tasks=[propose_task, review_task],
        process=Process.sequential,
        verbose=False,
    )
    result = crew.kickoff()

    raw_text = _task_output(review_task) or str(result)
    candidates = _parse_candidates(raw_text)[:max_candidates]
    return BacklogProposal(
        project=manifest.name,
        dossier_commit_sha=dossier.commit_sha,
        candidates=candidates,
    )


def _propose_prompt(manifest: Manifest, dossier: DossierDraft, max_candidates: int) -> str:
    return textwrap.dedent(
        f"""\
        You are the Product Owner for project {manifest.name}. Read the
        dossier markdown below and propose up to {max_candidates} GitHub
        issue candidates that would deliver the most value or pay down the
        most pressing tech debt in the next 1–2 sprints.

        ## Hard rules
        - Pull ONLY from the dossier's Tech-debt register, Hot spots, and
          Open questions for operator sections. Do not invent items the
          dossier does not support.
        - Every candidate MUST include at least one `path:line` citation
          from the dossier — copy the backticked anchor verbatim.
        - Mark each candidate's `kind` from: feature, bug, tech_debt, security.
        - Set `source_section` to the dossier section the candidate came from
          (e.g. tech_debt, hot_spots, open_questions).

        ## Output
        Emit a JSON array. Each element shape:
        {{
          "title": "<one-line title, no leading [tag]>",
          "body": "<3-8 sentence description with cited anchors>",
          "kind": "feature|bug|tech_debt|security",
          "source_section": "<section name>",
          "citations": ["src/x.py:42", ...]
        }}

        ## Dossier markdown
        ```
        {dossier.markdown}
        ```
        """
    )


def _review_prompt(manifest: Manifest, dossier: DossierDraft, max_candidates: int) -> str:
    return textwrap.dedent(
        f"""\
        You are the Senior Engineer. The Product Owner produced a draft set
        of GitHub issue candidates for {manifest.name}. Review each one:

        - Drop candidates whose cited path is not present in the dossier
          markdown — silently filter rather than rewrite.
        - Drop duplicates (multiple candidates citing the same anchor for
          the same kind).
        - Keep at most {max_candidates} candidates.

        Emit the filtered list as a JSON array in the same shape the
        Product Owner used. No commentary outside the JSON.

        ## Dossier markdown (for verification)
        ```
        {dossier.markdown}
        ```
        """
    )


def _task_output(task: object) -> str | None:
    out = getattr(task, "output", None)
    if out is None:
        return None
    raw = getattr(out, "raw", None)
    if isinstance(raw, str):
        return raw
    return str(out)


def _parse_candidates(text: str) -> list[BacklogCandidate]:
    """Best-effort JSON extraction from the LLM output."""
    block = _extract_json_array(text)
    if block is None:
        logger.warning("backlog_proposer output had no JSON array: %r", text[:200])
        return []
    try:
        raw_list = json.loads(block)
    except json.JSONDecodeError as e:
        logger.warning("backlog_proposer JSON parse failed: %s", e)
        return []
    if not isinstance(raw_list, list):
        return []
    out: list[BacklogCandidate] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        kind_raw = (item.get("kind") or "").lower()
        try:
            kind = BacklogKind(kind_raw)
        except ValueError:
            continue
        title = (item.get("title") or "").strip()
        body = (item.get("body") or "").strip()
        if not title or not body:
            continue
        citations = [
            str(c).strip("` ") for c in (item.get("citations") or []) if isinstance(c, str)
        ]
        out.append(
            BacklogCandidate(
                title=title,
                body=body,
                kind=kind,
                source_section=str(item.get("source_section") or "unknown"),
                citations=citations,
            )
        )
    return out


_JSON_ARRAY_FENCED = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)


def _extract_json_array(text: str) -> str | None:
    m = _JSON_ARRAY_FENCED.search(text)
    if m:
        return m.group(1)
    # Fallback: first [...] block in the text.
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        return text[start : end + 1]
    return None


__all__ = [
    "CREW_VERSION",
    "run_backlog_proposer",
]
