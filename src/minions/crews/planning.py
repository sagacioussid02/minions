"""Weekly sprint planning crew — PO + Principal + Manager.

The crew runs sequentially: PO discovers and ranks candidates, Principal
validates feasibility, Manager packages the sprint. Output is wrapped as a
Decision Record of type 'feature' (the primary type for a sprint proposal),
which then flows through the standard approval gate.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

from minions.activity import crew_run
from minions.agents.base import MinionAgent
from minions.agents.roster import build_named_agent
from minions.cost import clear_attribution, set_attribution
from minions.models.decision import Decision, DecisionType
from minions.models.roles import Role
from minions.observability import add_metadata, observe_crew

if TYPE_CHECKING:
    from minions.models.manifest import Manifest
    from minions.onboarding.profile import ProjectProfile


@observe_crew("planning_crew")
def run_planning_crew(
    manifest: Manifest,
    *,
    dry_run: bool = True,
    api_key: str | None = None,
    base_sha: str | None = None,
    profile: ProjectProfile | None = None,
) -> Decision:
    """Run the weekly planning crew for a project.

    Returns a sprint-proposal Decision Record (status=pending). Caller is
    responsible for routing it through the approval flow.

    With ``dry_run=True`` (default) no LLM calls are made — useful for
    smoke-testing the wiring without burning tokens. With ``dry_run=False``
    an Anthropic API key is required.
    """
    project = manifest.name
    cadence = manifest.cadence_profile

    add_metadata(
        crew="planning",
        project=project,
        cadence=cadence,
        dry_run=dry_run,
        owner=manifest.owner,
        grounded=profile is not None,
    )

    # Build the three planning agents (with display names from manifest, if set).
    po_min = build_named_agent(
        Role.PRODUCT_OWNER, project=project, manifest=manifest, cadence=cadence
    )
    princ_min = build_named_agent(
        Role.PRINCIPAL, project=project, manifest=manifest, cadence=cadence
    )
    mgr_min = build_named_agent(Role.MANAGER, project=project, manifest=manifest, cadence=cadence)

    if dry_run:
        return _dry_run_proposal(manifest, manager=mgr_min, base_sha=base_sha, profile=profile)

    if api_key is None:
        raise ValueError("api_key is required when dry_run=False")

    # Lazy imports so dry-run paths don't require crewai/langchain to be installed.
    from crewai import Crew, Process, Task

    from minions.crews.factory import make_crewai_agent

    po = make_crewai_agent(po_min, api_key=api_key)
    princ = make_crewai_agent(princ_min, api_key=api_key)
    mgr = make_crewai_agent(mgr_min, api_key=api_key)

    profile_block = (
        "\n\n## Project profile (read-only, current)\n" + profile.to_planning_context()
        if profile is not None
        else ""
    )
    grounding_rule = (
        "Use ONLY signals present in the project profile above. Do NOT invent issues, "
        "deps, or TODOs that aren't documented. If the profile is sparse, propose fewer "
        "items rather than fabricating."
        if profile is not None
        else "If you cannot ground a candidate in a real signal, omit it (no fabrication)."
    )

    discovery = Task(
        description=textwrap.dedent(
            f"""\
            Discover and rank up to 5 candidate work items for the next sprint of '{project}'.
            Sources you may reason about: open GitHub issues, code TODOs/FIXMEs, dependency
            freshness, security advisories, performance signals, tasks.md backlog, recent
            commit activity, operator-supplied roadmap.
            For each candidate provide: title, type (feature|bug|tech_debt), impact,
            estimated effort, risk, source signal (cite exactly which line of the profile
            it came from).

            {grounding_rule}{profile_block}"""
        ),
        agent=po,
        expected_output="Markdown list of up to 5 ranked candidates with the fields above.",
    )

    feasibility = Task(
        description=textwrap.dedent(
            f"""\
            Validate the candidate list for technical feasibility on '{project}'. Flag any
            blockers (missing skills, hidden dependencies, tooling gaps, risk concentration).
            Recommend which to keep and which to defer."""
        ),
        agent=princ,
        expected_output="Markdown: a 'keep' list and a 'defer' list with one-line rationale each.",
        context=[discovery],
    )

    packaging = Task(
        description=textwrap.dedent(
            f"""\
            Package next week's sprint for '{project}'. Target: 1 feature + 1 tech-debt item +
            bugs as available (no fabrication — only real items). Produce a sprint proposal
            with title, items, estimated cost, and risk score (low|medium|high)."""
        ),
        agent=mgr,
        expected_output="Markdown sprint proposal with the fields above.",
        context=[discovery, feasibility],
    )

    crew = Crew(
        agents=[po, princ, mgr],
        tasks=[discovery, feasibility, packaging],
        process=Process.sequential,
        verbose=False,
    )

    set_attribution(project=project, role="planning")
    try:
        with crew_run(
            crew="planning",
            project=project,
            agents=["product_owner", "principal_engineer", "manager"],
        ):
            result = crew.kickoff()
    finally:
        clear_attribution()
    proposal_text = str(result)

    risk = _infer_risk(proposal_text)

    return Decision(
        project=project,
        type=DecisionType.FEATURE,
        summary=f"Sprint proposal for {project}",
        rationale=f"Generated by planning crew (PO + Principal + Manager) on {cadence}.",
        diff_or_plan=proposal_text,
        risk=risk,
        proposer_role=Role.MANAGER.value,
        proposer_agent_id=mgr_min.name,
        proposer_display_name=mgr_min.display_name,
        base_sha=base_sha,
    )


def _dry_run_proposal(
    manifest: Manifest,
    *,
    manager: MinionAgent,
    base_sha: str | None = None,
    profile: ProjectProfile | None = None,
) -> Decision:
    grounding = ""
    if profile is not None:
        bullets: list[str] = []
        if profile.tasks_md:
            bullets.append(
                f"- tasks.md backlog: {profile.tasks_md.remaining} items remaining "
                f"({profile.tasks_md.path})"
            )
        if profile.open_issues:
            bullets.append(f"- {len(profile.open_issues)} open GitHub issue(s)")
        if profile.todo_count:
            bullets.append(f"- {profile.todo_count} TODO/FIXME marker(s) in source")
        if profile.package_files:
            kinds = ", ".join(sorted({p.kind for p in profile.package_files}))
            bullets.append(f"- Package ecosystems present: {kinds}")
        if bullets:
            grounding = "\n## Grounding signals\n" + "\n".join(bullets) + "\n"

    plan = textwrap.dedent(
        f"""\
        # Sprint proposal — {manifest.name} (DRY RUN)

        This is a dry-run sprint proposal generated without any LLM calls.
        A real run would invoke PO + Principal + Manager (Sonnet x3 in v0 frugal).
        {grounding}
        ## Proposed items
        - **Feature** (TBD) — Product Owner would mine open issues + roadmap signals.
        - **Tech debt** (TBD) — Principal would surface from code annotations + dep freshness.
        - **Bugs** (0–N, depends on real signals) — only real bugs; no fabrication.

        ## Estimated cost
        ~$0.10 (dry-run); real planning crew run ≈ $0.05–$0.15 in v0 frugal mode.

        ## Risk
        low (no live changes proposed in this dry run)
        """
    ).strip()

    return Decision(
        project=manifest.name,
        type=DecisionType.FEATURE,
        summary=f"[DRY RUN] Sprint proposal for {manifest.name}",
        rationale="Dry-run smoke test of the planning crew wiring. No LLM calls were made.",
        diff_or_plan=plan,
        risk="low",
        proposer_role=Role.MANAGER.value,
        proposer_agent_id=manager.name,
        proposer_display_name=manager.display_name,
        base_sha=base_sha,
    )


def _infer_risk(proposal_text: str) -> str:
    """Best-effort risk inference from the LLM's textual proposal.

    Looks for a 'Risk: X' line; defaults to 'low'. Crews are encouraged to
    declare a risk explicitly so this stays predictable.
    """
    lower = proposal_text.lower()
    if "risk: high" in lower or "risk score: high" in lower:
        return "high"
    if "risk: medium" in lower or "risk score: medium" in lower:
        return "medium"
    return "low"
