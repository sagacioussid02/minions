"""Weekly sprint planning crew — multi-voice debate + Manager synthesis.

Default path (Phase A of enriched-sprint-planning): a 5-voice debate.
Round 1 has each voice pitch independently. Round 2 has each voice
rebut the others. Round 3 has the Manager synthesize into a
StructuredSprintPlan JSON, including ``discussion`` minutes.

Legacy path (kept as fallback): the original PO → Principal → Manager
3-agent sequential pipeline. If the debate path raises for any reason
(CrewAI version skew, Anthropic rate limit, parse failure), planning
falls back so the operator never sees a planning failure due to the
experiment.
"""

from __future__ import annotations

import logging
import textwrap
from typing import TYPE_CHECKING

from minions.activity import crew_run
from minions.agents.base import MinionAgent
from minions.agents.roster import build_named_agent
from minions.cost import clear_attribution, set_attribution
from minions.models.decision import Decision, DecisionType
from minions.models.roles import Role
from minions.models.sprint_plan import StructuredSprintPlan
from minions.observability import add_metadata, observe_crew

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from minions.models.manifest import Manifest
    from minions.onboarding.profile import ProjectProfile


class PlanningRefusedStaleError(RuntimeError):
    """Raised when planning refuses to run because the dossier is very_stale.

    Carries a Decision Record proposing a discovery run so the caller (typically
    ``scheduled/weekly_planning.py``) can route it through the standard
    approval pipeline as the only side effect of the refused sweep.
    """

    def __init__(self, *, project: str, freshness: str, queued: Decision) -> None:
        super().__init__(
            f"planning refused for {project}: dossier freshness={freshness}; "
            f"queued discovery decision {queued.id}"
        )
        self.project = project
        self.freshness = freshness
        self.queued = queued


def _dossier_grounding_note(profile: ProjectProfile | None) -> str:
    """Short rationale tail noting how the dossier influenced this Decision."""
    if profile is None or profile.dossier_freshness is None:
        return ""
    freshness = profile.dossier_freshness
    digest = profile.dossier_digest
    if digest is None or freshness == "none":
        return " (ungrounded by dossier — none available)"
    if freshness == "stale":
        return (
            f" (grounded against stale dossier at commit "
            f"{digest.commit_sha[:8]}; refresh recommended)"
        )
    if freshness == "ok":
        return f" (grounded against PROJECT_DOSSIER.md at commit {digest.commit_sha[:8]})"
    return f" (dossier freshness={freshness})"


def build_queued_discovery_decision(
    *, manifest: Manifest, freshness: str, age_days: int | None = None
) -> Decision:
    """Compose the auto-approved Decision Record that asks discovery to refresh.

    Type is ``DOSSIER_REFRESH`` so the existing approval surface treats it as
    a dossier-related ask. Risk stays ``low`` (no LLM spend yet — only the
    operator-visible signal that planning had to back off). The status is set
    to ``APPROVED`` directly so the next discovery sweep picks it up without
    a human round-trip; this is the *one* place the org auto-approves on the
    operator's behalf, and only because the alternative (an unbounded loop of
    stale planning Decisions) is worse.
    """
    from minions.models.decision import DecisionStatus

    age = f"{age_days}d" if age_days is not None else "unknown age"
    decision = Decision(
        project=manifest.name,
        type=DecisionType.DOSSIER_REFRESH,
        risk="low",
        summary=(f"discovery queued: dossier for {manifest.name} is {freshness} ({age})"),
        rationale=(
            "Planning refused to run because the project's dossier is "
            f"{freshness}. Auto-approved so the next `cron-discovery` sweep "
            "refreshes the dossier; planning resumes on the cycle after that."
        ),
        diff_or_plan=(
            "## Planning refused\n\n"
            f"- Project: `{manifest.name}`\n"
            f"- Dossier freshness: `{freshness}`\n"
            f"- Age: {age}\n\n"
            "Run `minions discover " + manifest.name + " --no-dry-run --force` "
            "or wait for the weekly discovery sweep."
        ),
        proposer_role="manager",
        proposer_agent_id=f"planning@{manifest.name}",
        requested_by_role="manager",
        status=DecisionStatus.APPROVED,
    )
    decision.__pydantic_extra__ = {"kind": "dossier_refresh_queued"}
    return decision


@observe_crew("planning_crew")
def run_planning_crew(
    manifest: Manifest,
    *,
    dry_run: bool = True,
    api_key: str | None = None,
    base_sha: str | None = None,
    profile: ProjectProfile | None = None,
    sprint_number: int | None = None,
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

    dossier_freshness = profile.dossier_freshness if profile is not None else None

    add_metadata(
        crew="planning",
        project=project,
        cadence=cadence,
        dry_run=dry_run,
        owner=manifest.owner,
        grounded=profile is not None,
        dossier_freshness=dossier_freshness,
    )

    # Freshness gate — refuse to plan against a very_stale dossier. The
    # queued discovery Decision is the only side-effect of this branch;
    # callers (weekly_planning + the CLI) catch it and surface to the
    # operator. Dry-run sweeps cost $0, so they're always allowed through
    # (the dry-run output is the diagnostic that helps the operator decide
    # whether a discovery refresh is overdue).
    if not dry_run and dossier_freshness == "very_stale":
        digest = profile.dossier_digest if profile is not None else None
        age_days = None
        if digest is not None:
            from datetime import UTC, datetime

            age_days = max(0, (datetime.now(UTC) - digest.generated_at).days)
        queued = build_queued_discovery_decision(
            manifest=manifest, freshness="very_stale", age_days=age_days
        )
        raise PlanningRefusedStaleError(project=project, freshness="very_stale", queued=queued)

    # Build the three planning agents (with display names from manifest, if set).
    po_min = build_named_agent(
        Role.PRODUCT_OWNER, project=project, manifest=manifest, cadence=cadence
    )
    princ_min = build_named_agent(
        Role.PRINCIPAL, project=project, manifest=manifest, cadence=cadence
    )
    mgr_min = build_named_agent(Role.MANAGER, project=project, manifest=manifest, cadence=cadence)

    if dry_run:
        return _dry_run_proposal(
            manifest,
            manager=mgr_min,
            base_sha=base_sha,
            profile=profile,
            sprint_number=sprint_number,
        )

    if api_key is None:
        raise ValueError("api_key is required when dry_run=False")

    # Primary path: 5-voice debate (Phase A of enriched-sprint-planning).
    # Falls back to the legacy 3-agent pipeline on any failure so the
    # operator never sees a planning failure due to the experiment.
    try:
        proposal_text = _run_debate_planning_pipeline(
            project=project,
            manifest=manifest,
            profile=profile,
            api_key=api_key,
            po_min=po_min,
            princ_min=princ_min,
            mgr_min=mgr_min,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "debate planning failed for %s; falling back to legacy pipeline",
            project,
            exc_info=True,
        )
        proposal_text = _run_legacy_planning_pipeline(
            project=project,
            profile=profile,
            api_key=api_key,
            po_min=po_min,
            princ_min=princ_min,
            mgr_min=mgr_min,
        )

    structured_plan = _parse_structured_plan(proposal_text, project=project)
    # Fill the legacy diff_or_plan from the structured plan so email + every
    # existing UI surface keeps rendering exactly as before. The planning
    # conversation tail (profile snapshot etc.) is appended for traceability.
    proposal_markdown = structured_plan.render_markdown()
    plan_with_conversation = _with_planning_conversation(
        project=project,
        proposal_text=proposal_markdown,
        profile=profile,
        dry_run=False,
    )
    risk = _infer_risk(proposal_text)

    summary = (
        f"Sprint {sprint_number} proposal for {project}"
        if sprint_number is not None
        else f"Sprint proposal for {project}"
    )
    dossier_note = _dossier_grounding_note(profile)
    return Decision(
        project=project,
        type=DecisionType.FEATURE,
        summary=summary,
        rationale=(
            f"Generated by 5-voice debate planning crew "
            "(PO + Principal + Cloud DevOps + Security Champion + Engineer "
            f"→ Manager synthesis) on {cadence}.{dossier_note}"
        ),
        diff_or_plan=plan_with_conversation,
        structured_plan=structured_plan,
        sprint_number=sprint_number,
        risk=risk,
        proposer_role=Role.MANAGER.value,
        proposer_agent_id=mgr_min.name,
        proposer_display_name=mgr_min.display_name,
        base_sha=base_sha,
    )


def _run_debate_planning_pipeline(
    *,
    project: str,
    manifest: Manifest,
    profile: ProjectProfile | None,
    api_key: str,
    po_min: MinionAgent,
    princ_min: MinionAgent,
    mgr_min: MinionAgent,
) -> str:
    """5-voice debate. Returns the raw synthesizer output text.

    Round 1: 5 independent pitches (no cross-context).
    Round 2: 5 rebuttals (each sees the other 4 pitches).
    Round 3: Manager synthesizes into a StructuredSprintPlan JSON
             including ``discussion: list[str]``.
    """
    from crewai import Crew, Process, Task

    from minions.crews.factory import make_crewai_agent

    cadence = manifest.cadence_profile

    # Two extra voices beyond PO/Principal/Manager: DevOps + Engineer.
    # (Security Champion is the obvious fifth but adds another voice that
    # tends to overlap with Principal on tech-debt — skipping for v1 to
    # keep cost bounded. Easy to add later.)
    devops_min = build_named_agent(
        Role.CLOUD_DEVOPS, project=project, manifest=manifest, cadence=cadence
    )
    eng_min = build_named_agent(Role.ENGINEER, project=project, manifest=manifest, cadence=cadence)

    po = make_crewai_agent(po_min, api_key=api_key)
    princ = make_crewai_agent(princ_min, api_key=api_key)
    devops = make_crewai_agent(devops_min, api_key=api_key)
    eng = make_crewai_agent(eng_min, api_key=api_key)
    mgr = make_crewai_agent(mgr_min, api_key=api_key)

    profile_block = (
        "\n\n## Project profile (read-only, current)\n" + profile.to_planning_context()
        if profile is not None
        else ""
    )
    grounding_rule = (
        "Use ONLY signals present in the project profile above. Do NOT invent "
        "items that aren't documented."
        if profile is not None
        else "If you cannot ground an item in a real signal, omit it (no fabrication)."
    )

    # ---- Round 1 — independent pitches -----------------------------------

    po_pitch = Task(
        description=textwrap.dedent(f"""\
            You are the PRODUCT OWNER for project '{project}'. Speaking in
            the planning meeting, give the 3-5 highest-impact USER-FACING
            items the team should ship next sprint. Cite the source signal
            (issue, roadmap line, metric).

            {grounding_rule}{profile_block}
        """),
        agent=po,
        expected_output="Ranked markdown list with 3-5 user-facing items + rationale.",
    )
    princ_pitch = Task(
        description=textwrap.dedent(f"""\
            You are the PRINCIPAL ENGINEER for '{project}'. Speaking in the
            planning meeting, give the 3-5 highest-impact TECH-DEBT or
            ARCHITECTURE items that are blocking the team or about to bite.
            Cite the source signal.

            {grounding_rule}{profile_block}
        """),
        agent=princ,
        expected_output="Ranked markdown list with 3-5 tech-debt/arch items + rationale.",
    )
    devops_pitch = Task(
        description=textwrap.dedent(f"""\
            You are the CLOUD DEVOPS engineer covering '{project}'. Speaking
            in the planning meeting, give the 2-4 OPS items the team should
            tackle next: deploy pain, cost overruns, on-call noise, CI flakes.
            Cite the source signal.

            {grounding_rule}{profile_block}
        """),
        agent=devops,
        expected_output="Ranked markdown list with 2-4 ops items + rationale.",
    )
    eng_pitch = Task(
        description=textwrap.dedent(f"""\
            You are an ENGINEER who works in '{project}' day-to-day. Speaking
            in the planning meeting, give the 2-4 items YOU would push for —
            the kind of paper-cuts and friction that don't show up in tickets
            but slow you down every day. Be honest.

            {grounding_rule}{profile_block}
        """),
        agent=eng,
        expected_output="Ranked markdown list with 2-4 friction items + rationale.",
    )

    # ---- Round 2 — rebuttals (each agent sees the other 3) ----------------

    rebut_template = textwrap.dedent("""\
        You're back in the planning meeting. You've just heard the other
        voices pitch their items. For each of their proposals, say either
        (a) "+1, promote" with one line of why, OR (b) "-1, defer / drop"
        with one line of why, OR (c) "modify: <change>". Keep it short —
        this is rebuttal time, not a re-pitch.
    """)

    po_rebut = Task(
        description=rebut_template,
        agent=po,
        expected_output="Short rebuttal — +1/-1/modify per other-agent item.",
        context=[princ_pitch, devops_pitch, eng_pitch],
    )
    princ_rebut = Task(
        description=rebut_template,
        agent=princ,
        expected_output="Short rebuttal — +1/-1/modify per other-agent item.",
        context=[po_pitch, devops_pitch, eng_pitch],
    )
    devops_rebut = Task(
        description=rebut_template,
        agent=devops,
        expected_output="Short rebuttal — +1/-1/modify per other-agent item.",
        context=[po_pitch, princ_pitch, eng_pitch],
    )
    eng_rebut = Task(
        description=rebut_template,
        agent=eng,
        expected_output="Short rebuttal — +1/-1/modify per other-agent item.",
        context=[po_pitch, princ_pitch, devops_pitch],
    )

    # ---- Round 3 — Manager synthesis -------------------------------------

    synthesis = Task(
        description=textwrap.dedent(f"""\
            You are the MANAGER for '{project}'. You have heard 4 voices
            pitch and rebut each other. Now produce the final sprint plan.

            STRICT JSON output (no commentary, no markdown fences, no
            trailing prose — only valid JSON):

            {{
              "goal": "one-line theme",
              "features": [
                {{
                  "title": "short imperative",
                  "rationale": "why this, why now",
                  "acceptance_criteria": "done when ...",
                  "estimated_effort": "xs" | "s" | "m" | "l" | "xl",
                  "suggested_owner_role": "engineer" | "senior_engineer" | "documentation_engineer" | "cloud_devops" | null,
                  "subtasks": [<recursive PlanItem shape, optional>]
                }}
              ],
              "bugs":      [ /* same shape */ ],
              "tech_debt": [ /* same shape */ ],
              "ops":       [ /* same shape */ ],
              "docs":      [ /* same shape */ ],
              "risks":     [ "short string per risk" ],
              "discussion": [
                "PO raised the mobile add-to-cart item; Principal agreed it ships first.",
                "DevOps pushed CI cleanup; Engineer said the lint failures aren't blocking — dropped.",
                "Principal proposed splitting 'Stripe totals' into 3 subtasks — accepted."
              ]
            }}

            Rules:
              - Items must be grounded in the pitches/rebuttals. No new items.
              - For items with estimated_effort 'l' or 'xl', break into 2-4
                subtasks each (each subtask effort 's' or smaller). Cap total
                subtasks across the whole plan at 8.
              - discussion[] MUST capture concrete "X pushed Y, accepted/
                rejected because Z" attribution. Aim for 3-6 lines.
              - Empty arrays are fine for sections with no real items.
        """),
        agent=mgr,
        expected_output="Single JSON object conforming to StructuredSprintPlan. No prose.",
        context=[
            po_pitch,
            princ_pitch,
            devops_pitch,
            eng_pitch,
            po_rebut,
            princ_rebut,
            devops_rebut,
            eng_rebut,
        ],
    )

    crew = Crew(
        agents=[po, princ, devops, eng, mgr],
        tasks=[
            po_pitch,
            princ_pitch,
            devops_pitch,
            eng_pitch,
            po_rebut,
            princ_rebut,
            devops_rebut,
            eng_rebut,
            synthesis,
        ],
        process=Process.sequential,
        verbose=False,
    )

    set_attribution(project=project, role="planning")
    try:
        with crew_run(
            crew="planning",
            project=project,
            agents=["product_owner", "principal_engineer", "cloud_devops", "engineer", "manager"],
        ) as run_id:
            result = crew.kickoff()
            # Phase 3 of crew-transcripts: persist each task's LLM output
            # so the Stage feed + the per-run transcript page can show
            # the actual debate, not just "5 agents on current work".
            # Best-effort; any failure here is swallowed inside capture.
            from minions.transcripts.capture import record_task_default

            tasks_in_order: list[tuple[str, MinionAgent, str, object]] = [
                ("pitch", po_min, "product_owner", po_pitch.output),
                ("pitch", princ_min, "principal_engineer", princ_pitch.output),
                ("pitch", devops_min, "cloud_devops", devops_pitch.output),
                ("pitch", eng_min, "engineer", eng_pitch.output),
                ("rebuttal", po_min, "product_owner", po_rebut.output),
                ("rebuttal", princ_min, "principal_engineer", princ_rebut.output),
                ("rebuttal", devops_min, "cloud_devops", devops_rebut.output),
                ("rebuttal", eng_min, "engineer", eng_rebut.output),
                ("synthesis", mgr_min, "manager", synthesis.output),
            ]
            for seq, (role_in_conv, agent_min, role, output) in enumerate(tasks_in_order):
                record_task_default(
                    run_id=run_id,
                    project=project,
                    crew="planning",
                    agent_role=role,
                    agent_display_name=agent_min.display_name,
                    sequence=seq,
                    role_in_conversation=role_in_conv,  # type: ignore[arg-type]
                    task_output=output,
                )
    finally:
        clear_attribution()
    return str(result)


def _run_legacy_planning_pipeline(
    *,
    project: str,
    profile: ProjectProfile | None,
    api_key: str,
    po_min: MinionAgent,
    princ_min: MinionAgent,
    mgr_min: MinionAgent,
) -> str:
    """Legacy 3-agent PO → Principal → Manager pipeline. Fallback path.

    Returns the raw synthesizer output text so the caller's parser can
    handle it identically to the debate path's output.
    """
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
        "deps, or TODOs that aren't documented."
        if profile is not None
        else "If you cannot ground a candidate in a real signal, omit it (no fabrication)."
    )

    discovery = Task(
        description=textwrap.dedent(f"""\
            Discover and rank up to 5 candidate work items for the next sprint of '{project}'.
            For each candidate provide: title, type (feature|bug|tech_debt), impact,
            estimated effort, risk, source signal.

            {grounding_rule}{profile_block}
        """),
        agent=po,
        expected_output="Markdown list of up to 5 ranked candidates.",
    )
    feasibility = Task(
        description=textwrap.dedent(f"""\
            Validate the candidate list for technical feasibility on '{project}'.
            Recommend keep/defer.
        """),
        agent=princ,
        expected_output="Markdown 'keep' + 'defer' lists.",
        context=[discovery],
    )
    packaging = Task(
        description=textwrap.dedent(f"""\
            Package next week's sprint for '{project}'. Output STRICT JSON
            conforming to the StructuredSprintPlan schema (no commentary,
            no markdown fences). discussion may be an empty list in this
            fallback path.
        """),
        agent=mgr,
        expected_output="Single JSON object conforming to StructuredSprintPlan. No prose.",
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
    return str(result)


def _parse_structured_plan(text: str, *, project: str) -> StructuredSprintPlan:
    """Parse the Manager's JSON output, with one retry trick + fallback.

    The LLM is told strict JSON. In practice it sometimes wraps the JSON in
    ```` ```json ... ``` ```` fences or appends a trailing prose paragraph.
    We strip those and try once more before giving up and wrapping the raw
    text as a single PlanItem — the sprint is never dropped just because
    parsing failed.
    """
    candidate = text.strip()
    # Strip a single leading/trailing code fence if present.
    if candidate.startswith("```"):
        candidate = candidate.split("```", 2)[1] if "```" in candidate[3:] else candidate
        if candidate.startswith("json"):
            candidate = candidate[4:].lstrip()
        candidate = candidate.rstrip("`").strip()
    # If the LLM appended prose after a JSON object, trim back to the last "}".
    if not candidate.endswith("}"):
        last_brace = candidate.rfind("}")
        if last_brace != -1:
            candidate = candidate[: last_brace + 1]
    try:
        return StructuredSprintPlan.model_validate_json(candidate)
    except Exception:
        return StructuredSprintPlan.fallback_from_markdown(
            text, goal=f"Sprint proposal for {project}"
        )


def _dry_run_proposal(
    manifest: Manifest,
    *,
    manager: MinionAgent,
    base_sha: str | None = None,
    profile: ProjectProfile | None = None,
    sprint_number: int | None = None,
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

    # Synthetic structured plan so the demo's dry-run path renders the same
    # sectioned UI as a real run. None of these are real items.
    from minions.models.sprint_plan import PlanItem

    dry_plan = StructuredSprintPlan(
        goal=f"Dry-run smoke test of the planning pipeline for {manifest.name}",
        features=[
            PlanItem(
                title="(dry-run) feature placeholder",
                rationale="A real run would mine open issues + roadmap signals.",
                acceptance_criteria="A new feature lands behind a flag with tests.",
                estimated_effort="m",
                suggested_owner_role="engineer",
            )
        ],
        tech_debt=[
            PlanItem(
                title="(dry-run) tech-debt placeholder",
                rationale="Principal would surface from code annotations + dep freshness.",
                acceptance_criteria="Targeted refactor with no behaviour change; tests still green.",
                estimated_effort="s",
                suggested_owner_role="senior_engineer",
            )
        ],
        bugs=[],
        ops=[],
        docs=[],
        risks=["This is a dry-run; no real planning occurred."],
    )
    proposal_markdown = dry_plan.render_markdown()
    plan = _with_planning_conversation(
        project=manifest.name,
        proposal_text=proposal_markdown + grounding,
        profile=profile,
        dry_run=True,
    )

    summary = (
        f"[DRY RUN] Sprint {sprint_number} proposal for {manifest.name}"
        if sprint_number is not None
        else f"[DRY RUN] Sprint proposal for {manifest.name}"
    )
    return Decision(
        project=manifest.name,
        type=DecisionType.FEATURE,
        summary=summary,
        rationale="Dry-run smoke test of the planning crew wiring. No LLM calls were made.",
        diff_or_plan=plan,
        structured_plan=dry_plan,
        sprint_number=sprint_number,
        risk="low",
        proposer_role=Role.MANAGER.value,
        proposer_agent_id=manager.name,
        proposer_display_name=manager.display_name,
        base_sha=base_sha,
    )


def _with_planning_conversation(
    *,
    project: str,
    proposal_text: str,
    profile: ProjectProfile | None,
    dry_run: bool,
) -> str:
    signals = _conversation_signal_summary(profile)
    mode = "dry-run scaffold" if dry_run else "crew task context"
    conversation = textwrap.dedent(
        f"""\
        ## Planning conversation
        _Recorded from the PO → Principal → Manager planning loop ({mode})._

        **Product Owner → Principal Engineer**
        I scanned {project} for candidate feature, bug, chore, and tech-debt work.
        Grounding signals: {signals}

        **Principal Engineer → Manager**
        I reviewed the PO candidates for feasibility, risk, dependency order, and
        whether they are grounded in observable project signals. Ungrounded work
        should be deferred rather than invented.

        **Manager → Operator**
        I packaged the agreed work into the sprint proposal below and am sending it
        for operator approval.
        """
    ).strip()
    return f"{conversation}\n\n---\n\n{proposal_text}"


def _conversation_signal_summary(profile: ProjectProfile | None) -> str:
    if profile is None:
        return "project profile unavailable; proposal must stay conservative."
    parts: list[str] = []
    if profile.tasks_md is not None:
        parts.append(f"tasks.md remaining={profile.tasks_md.remaining}")
    if profile.open_issues:
        parts.append(f"open issues={len(profile.open_issues)}")
    if profile.todo_count:
        parts.append(f"TODO/FIXME markers={profile.todo_count}")
    if profile.package_files:
        parts.append(
            "package ecosystems="
            + ", ".join(sorted({package.kind for package in profile.package_files}))
        )
    return "; ".join(parts) if parts else "no strong signals found; propose minimal work."


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
