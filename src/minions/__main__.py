"""CLI entry point for ``minions``.

Top-level commands:
  check        — validate config/portfolio.yaml and all project manifests
  org          — print the resolved org topology with model tiers
  roster       — print the agent roster for a given project (or all)
  plan         — run the weekly planning crew for a project (defaults to dry-run)

Subgroups:
  decisions list / show / approve / reject — inspect and resolve Decision Records
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich import print as rprint
from rich.console import Console
from rich.table import Table

from minions import anthropic_check
from minions import secrets as secrets_module
from minions.agents.roster import (
    AUDIT,
    SHARED_EXECUTIVE,
    SHARED_SPECIALIST,
    build_project_agents,
    build_shared_agents,
)
from minions.approval.service import resolve, submit_for_approval
from minions.approval.store import DecisionStore
from minions.config.portfolio import load_portfolio_config
from minions.crews.engineer import run_engineer_crew
from minions.crews.planning import run_planning_crew
from minions.github.auth import get_github_token
from minions.github.client import GitHubClient, GitHubError
from minions.models.decision import Decision, DecisionStatus, DecisionType
from minions.models.manifest import Manifest, load_active_manifests
from minions.notify.base import Notifier
from minions.notify.console import ConsoleNotifier
from minions.notify.gmail import GmailNotifier
from minions.observability import (
    auth_check,
    init_langfuse,
)
from minions.observability import (
    has_credentials as langfuse_has_credentials,
)
from minions.observability import (
    host_url as langfuse_host_url,
)
from minions.secrets import SecretNotFound, get_anthropic_api_key

app = typer.Typer(
    help="Minions — autonomous AI engineering organization.",
    no_args_is_help=True,
)
decisions_app = typer.Typer(
    help="Inspect and resolve Decision Records.",
    no_args_is_help=True,
)
secrets_app = typer.Typer(
    help="Diagnose secret resolution (env + AWS Secrets Manager).",
    no_args_is_help=True,
)
github_app = typer.Typer(
    help="GitHub diagnostics (read-only — never merges, never pushes to main).",
    no_args_is_help=True,
)
cron_app = typer.Typer(
    help="Manually trigger scheduled entrypoints (weekly planning, daily monitor, Friday digest).",
    no_args_is_help=True,
)
cost_app = typer.Typer(
    help="Inspect per-project LLM cost (read from data/local/cost_log.jsonl).",
    no_args_is_help=True,
)
audit_app = typer.Typer(
    help="Inspect Audit & Challenge findings (Code/Process/Cost auditor + Devil's Advocate).",
    no_args_is_help=True,
)
db_app = typer.Typer(
    help="Postgres (Neon) backend management — migrations, status, JSON backfill.",
    no_args_is_help=True,
)
questions_app = typer.Typer(
    help="Inter-agent Question Records (escalation channel).",
    no_args_is_help=True,
)
dossier_app = typer.Typer(
    help="Inspect per-project PROJECT_DOSSIER.md state (latest draft + freshness).",
    no_args_is_help=True,
)
backlog_app = typer.Typer(
    help="Propose and create GitHub issues from a merged PROJECT_DOSSIER.md.",
    no_args_is_help=True,
)
transcripts_app = typer.Typer(
    help="Inspect per-run crew transcripts (what each agent said in a session).",
    no_args_is_help=True,
)
app.add_typer(decisions_app, name="decisions")
app.add_typer(secrets_app, name="secrets")
app.add_typer(github_app, name="github")
app.add_typer(cron_app, name="cron")
app.add_typer(cost_app, name="cost")
app.add_typer(audit_app, name="audit")
app.add_typer(db_app, name="db")
app.add_typer(questions_app, name="questions")
app.add_typer(dossier_app, name="dossier")
app.add_typer(backlog_app, name="backlog")
app.add_typer(transcripts_app, name="transcripts")
console = Console()


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "portfolio.yaml"
PROJECTS_DIR = REPO_ROOT / "projects"
DECISION_STORE_PATH = REPO_ROOT / "data" / "local" / "decisions.json"
CREW_TRANSCRIPTS_PATH = REPO_ROOT / "data" / "local" / "crew_transcripts.json"
DEPLOYMENTS_PATH = REPO_ROOT / "data" / "local" / "deployments.json"
DOSSIER_DRAFTS_PATH = REPO_ROOT / "data" / "local" / "dossier_drafts.json"
DOTENV_PATH = REPO_ROOT / ".env"


def _parse_dotenv() -> dict[str, str]:
    """Parse REPO_ROOT/.env into a dict (no env mutation). Empty if file missing."""
    out: dict[str, str] = {}
    if not DOTENV_PATH.exists():
        return out
    for raw_line in DOTENV_PATH.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        out[key] = value
    return out


def _load_dotenv() -> None:
    """Load REPO_ROOT/.env into os.environ — orchestrator-only convenience.

    Agents NEVER read .env (filesystem deny-list). By default existing env
    vars take precedence (12-factor pattern: shell/CI wins over the dev
    fallback). Set ``MINIONS_DOTENV_OVERRIDE=1`` to flip — useful when you
    have a stale shell var hiding a fresh .env value.
    """
    parsed = _parse_dotenv()
    override = os.environ.get("MINIONS_DOTENV_OVERRIDE", "").lower() in {"1", "true", "yes"}
    for key, value in parsed.items():
        if override or key not in os.environ:
            os.environ[key] = value


_load_dotenv()
init_langfuse()  # no-op if LANGFUSE_PUBLIC_KEY/SECRET_KEY not set

# Cost tracking — JSONL log under data/local. Always on; storage is cheap.
from minions.cost import init_cost_tracking  # noqa: E402

COST_LOG_PATH = REPO_ROOT / "data" / "local" / "cost_log.jsonl"
BUDGET_NOTIFICATIONS_PATH = REPO_ROOT / "data" / "local" / "budget_notifications.json"
ACTIVITY_LOG_PATH = REPO_ROOT / "data" / "local" / "activity.jsonl"
ENGINEER_RUNS_PATH = REPO_ROOT / "data" / "local" / "engineer_runs.json"
AUDIT_FINDINGS_PATH = REPO_ROOT / "data" / "local" / "audit_findings.json"
QUESTIONS_PATH = REPO_ROOT / "data" / "local" / "questions.json"
AGILE_PATH = REPO_ROOT / "data" / "local" / "agile.json"
INTERVIEWS_PATH = REPO_ROOT / "data" / "local" / "interviews.json"
SPRINTS_PATH = REPO_ROOT / "data" / "local" / "sprints.json"
TASKS_PATH = REPO_ROOT / "data" / "local" / "tasks.json"
AGENT_MEMORY_PATH = REPO_ROOT / "data" / "local" / "agent_memory.json"
AGENT_LEARNING_PATH = REPO_ROOT / "data" / "local" / "agent_learning.json"
init_cost_tracking(log_path=COST_LOG_PATH)

from minions.activity import set_log_path as _set_activity_log_path  # noqa: E402

_set_activity_log_path(ACTIVITY_LOG_PATH, force_jsonl=False)


if TYPE_CHECKING:
    from minions.approval.store_factory import DecisionStoreLike


def _store() -> DecisionStoreLike:
    from minions.approval.store_factory import make_decision_store

    return make_decision_store(DECISION_STORE_PATH)


def _notifier() -> Notifier:
    """Pick the notifier from env. Default ConsoleNotifier; opt in to Gmail.

    Set ``MINIONS_NOTIFIER=gmail`` to send real emails. Requires
    ``MINIONS_SECRET_GMAIL_APP_PASSWORD`` (or AWS SM ``minions/gmail-app-password``).
    Owner address comes from ``portfolio.yaml.owner``.
    """
    chosen = os.environ.get("MINIONS_NOTIFIER", "console").lower()
    if chosen == "gmail":
        try:
            password = secrets_module.get_secret("gmail-app-password")
        except SecretNotFound as e:
            rprint(f"[red]MINIONS_NOTIFIER=gmail but no gmail-app-password secret:[/red] {e}")
            rprint("[dim]Falling back to ConsoleNotifier for this run.[/dim]")
            return ConsoleNotifier()
        portfolio = load_portfolio_config(CONFIG_PATH)
        return GmailNotifier(smtp_user=portfolio.owner, smtp_password=password)
    return ConsoleNotifier()


def _resolve_project(name: str, manifests: dict[str, Manifest]) -> Manifest:
    if name in manifests:
        return manifests[name]
    # Case-insensitive fallback so `demo` matches `Demo`.
    for k, v in manifests.items():
        if k.lower() == name.lower():
            return v
    rprint(f"[red]unknown project '{name}'[/red] — known: {list(manifests)}")
    raise typer.Exit(1)


# =============================================================================
# Top-level commands
# =============================================================================


@app.command()
def check() -> None:
    """Validate config/portfolio.yaml and all active project manifests."""
    rprint(f"[bold]Loading portfolio config from[/bold] {CONFIG_PATH.relative_to(REPO_ROOT)}")
    portfolio = load_portfolio_config(CONFIG_PATH)
    rprint(
        f"  ✓ portfolio config valid (owner: {portfolio.owner}, "
        f"locked_in_at: {portfolio.locked_in_at})"
    )
    rprint(
        f"  ✓ delivery cadence: option {portfolio.delivery_cadence.option} "
        f"({portfolio.delivery_cadence.scope})"
    )
    rprint(
        f"  ✓ procurement: budget ${portfolio.procurement.monthly_budget_usd}/mo, "
        f"secret_storage={portfolio.procurement.secret_storage}, "
        f"delegated_card={portfolio.procurement.delegated_card.enabled}"
    )
    rprint(
        f"  ✓ audit team enabled: {portfolio.audit.enabled} "
        f"(write_access={portfolio.audit.write_access})"
    )

    rprint(f"\n[bold]Loading active manifests from[/bold] {PROJECTS_DIR.relative_to(REPO_ROOT)}")
    manifests = load_active_manifests(PROJECTS_DIR)
    if not manifests:
        rprint("  [yellow]no active manifests found[/yellow]")
        return

    total_monthly = 0.0
    for name, m in manifests.items():
        total_monthly += m.monthly_budget_usd
        rprint(
            f"  ✓ [bold]{name}[/bold] — "
            f"weekly ${m.weekly_budget_usd}, monthly ${m.monthly_budget_usd}, "
            f"cadence {m.cadence_profile}, share_weight {m.delivery_targets.share_weight}"
        )
        publish_label = "in-repo" if m.dossier.publish else "local-only"
        rprint(
            f"      [dim]dossier: publish={publish_label}, "
            f"max_new_issues/cycle={m.dossier.max_new_issues_per_cycle}[/dim]"
        )

    floor = portfolio.budget_envelope.monthly_total_floor_usd
    ceiling = portfolio.budget_envelope.monthly_total_ceiling_usd
    color = "green" if floor <= total_monthly <= ceiling else "yellow"
    rprint(
        f"\n[bold]Total monthly budget:[/bold] [{color}]${total_monthly:.2f}[/{color}] "
        f"(envelope: ${floor}–${ceiling})"
    )


@app.command()
def org() -> None:
    """Print the resolved org topology with model tiers (v0 frugal cadence)."""
    portfolio = load_portfolio_config(CONFIG_PATH)
    manifests = load_active_manifests(PROJECTS_DIR)

    table = Table(title="Minions Org Topology (v0 frugal)", show_lines=False)
    table.add_column("Layer", style="bold")
    table.add_column("Role")
    table.add_column("Name", style="green")
    table.add_column("Project")
    table.add_column("Tier", style="cyan")

    for agent in build_shared_agents(portfolio, SHARED_EXECUTIVE, "v0_frugal"):
        table.add_row(
            "Executive", agent.role.value, agent.display_name or "—", "—", agent.tier.value
        )
    for agent in build_shared_agents(portfolio, SHARED_SPECIALIST, "v0_frugal"):
        table.add_row(
            "Specialist", agent.role.value, agent.display_name or "—", "—", agent.tier.value
        )
    for agent in build_shared_agents(portfolio, AUDIT, "v0_frugal"):
        table.add_row("Audit", agent.role.value, agent.display_name or "—", "—", agent.tier.value)
    for project_name, manifest in manifests.items():
        for agent in build_project_agents(manifest, manifest.cadence_profile):
            table.add_row(
                "Project",
                agent.role.value,
                agent.display_name or "—",
                project_name,
                agent.tier.value,
            )
    console.print(table)


@app.command()
def roster(
    project: str | None = typer.Argument(None, help="Project name; omit for all."),
) -> None:
    """Print the per-project agent roster (after manifest overrides + display names)."""
    manifests = load_active_manifests(PROJECTS_DIR)
    if project:
        manifests = {project: _resolve_project(project, manifests)}
    for name, manifest in manifests.items():
        unnamed = sum(
            1
            for a in build_project_agents(manifest, manifest.cadence_profile)
            if not a.display_name
        )
        rprint(
            f"\n[bold]{name}[/bold] (${manifest.monthly_budget_usd}/mo) "
            + (
                f"[dim]({unnamed} unnamed seats — edit projects/{name.lower()}.yaml `agents:` to personalize)[/dim]"
                if unnamed
                else "[green](all named)[/green]"
            )
        )
        for agent in build_project_agents(manifest, manifest.cadence_profile):
            seat_suffix = f"#{agent.seat_index}" if agent.seat_index > 0 else ""
            display = agent.display_name or "[dim]<unnamed>[/dim]"
            rprint(
                f"  - {agent.role.value:24s}{seat_suffix:3s}  {display:28s} → {agent.tier.value}"
            )


@app.command()
def implement(
    decision_id: str = typer.Argument(..., help="Approved Decision id (prefix OK)."),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run skips LLM calls and does NOT mutate the repo. Use --no-dry-run to actually open a PR.",
    ),
) -> None:
    """Run the engineer crew against an approved Decision — opens a draft PR."""
    decision = _find_by_prefix(decision_id)
    if decision is None:
        raise typer.Exit(1)
    if decision.status is not DecisionStatus.APPROVED:
        rprint(
            f"[red]Decision is {decision.status.value}; engineer crew only runs on APPROVED decisions.[/red]"
        )
        raise typer.Exit(1)

    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(decision.project, manifests)
    if manifest.source.kind != "github":
        rprint(
            f"[red]Project {decision.project} is not GitHub-hosted "
            f"(source.kind={manifest.source.kind}); engineer crew supports GitHub only in v0.[/red]"
        )
        raise typer.Exit(1)
    if not manifest.source.repo:
        rprint(f"[red]Project {decision.project} has no source.repo set.[/red]")
        raise typer.Exit(1)

    api_key: str | None = None
    if not dry_run:
        try:
            api_key = get_anthropic_api_key()
        except SecretNotFound as e:
            rprint(f"[red]Missing Anthropic API key:[/red] {e}")
            raise typer.Exit(1) from e
        ok, message = anthropic_check.auth_check(api_key)
        if not ok:
            rprint(f"[red]Anthropic preflight failed:[/red] {message}")
            rprint("[dim]Run `minions anthropic` to retry the auth check on its own.[/dim]")
            raise typer.Exit(1)
    try:
        token = get_github_token()
    except SecretNotFound as e:
        rprint(f"[red]Missing GitHub token:[/red] {e}")
        raise typer.Exit(1) from e

    rprint(f"\n[bold]Engineer crew working on[/bold] [cyan]{decision.summary}[/cyan]")
    rprint(f"  Decision: {decision.id}")
    rprint(f"  Project:  {manifest.name} → {manifest.source.repo}")
    rprint(
        f"  Mode:     {'[yellow]DRY RUN[/yellow]' if dry_run else '[red bold]LIVE[/red bold] (will open a real PR)'}\n"
    )

    from minions.budget import BudgetBreachError

    try:
        with GitHubClient(token=token, repo=manifest.source.repo) as github_client:
            result = run_engineer_crew(
                decision,
                manifest,
                github=github_client,
                dry_run=dry_run,
                api_key=api_key,
                cost_log_path=COST_LOG_PATH,
            )
    except BudgetBreachError as e:
        rprint(f"\n[red bold]Budget breach — refusing engineer run.[/red bold]\n{e}")
        rprint(
            "[dim]Raise the cap in projects/<name>.yaml monthly_budget_usd, or wait "
            "for next month, or run --dry-run to inspect what would happen.[/dim]"
        )
        raise typer.Exit(2) from e

    # Persist the run so the dashboard's sprint board can show real PR state.
    if not dry_run:
        try:
            from minions.crews.engineer_runs_store_factory import make_engineer_runs_store

            make_engineer_runs_store(ENGINEER_RUNS_PATH).save(result, project=manifest.name)
        except Exception as e:  # noqa: BLE001 — persistence failure must not crash CLI
            rprint(f"[yellow]warning: failed to persist engineer run: {e}[/yellow]")

    if result.skipped:
        rprint(f"[yellow]Skipped:[/yellow] {result.skip_reason}")
        if result.files_rejected:
            rprint(f"  rejected paths: {result.files_rejected}")
        return

    if result.dry_run:
        rprint(f"[dim]{result.pr_url}[/dim]")
        rprint(f"  branch: [bold]{result.branch_name}[/bold]")
        rprint(f"  files (would commit): {len(result.files_changed)}")
        for f in result.files_changed:
            rprint(f"    + {f}")
        if result.files_rejected:
            rprint(f"  files (rejected by safety filter): {result.files_rejected}")
        return

    rprint(f"[green]✓ PR opened:[/green] {result.pr_url}")
    rprint(f"  branch: {result.branch_name}")
    rprint(f"  files changed: {len(result.files_changed)}")
    for f in result.files_changed:
        rprint(f"    + {f}")
    if result.files_rejected:
        rprint(f"  [yellow]rejected by safety filter:[/yellow] {result.files_rejected}")
    if result.review_comment:
        rprint(f"\n[bold]TTL review (posted on PR):[/bold]\n{result.review_comment[:600]}")

    # Mark Decision as executed and persist the PR URL.
    store = _store()
    persisted = store.get(decision.id)
    if persisted is not None:
        persisted.pr_url = result.pr_url
        persisted.status = DecisionStatus.EXECUTED
        store.save(persisted)


@app.command()
def profile(
    project: str = typer.Argument(..., help="Project name (case-insensitive)."),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Emit the profile as JSON instead of the planning-context Markdown.",
    ),
    fetch_issues: bool = typer.Option(
        False,
        "--issues/--no-issues",
        help="Fetch open issues via the GitHub client (kind=github only).",
    ),
) -> None:
    """Profile a managed project — read-only signals fed to the planning crew."""
    from minions.onboarding import build_profile

    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(project, manifests)

    gh: GitHubClient | None = None
    if fetch_issues and manifest.source.kind == "github":
        gh = _open_github_client(manifest)

    prof = build_profile(manifest, github_client=gh)

    if json_out:
        rprint(prof.model_dump_json(indent=2))
    else:
        rprint(prof.to_planning_context())


@app.command()
def plan(
    project: str = typer.Argument(..., help="Project name (case-insensitive)."),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run skips LLM calls (default). Use --no-dry-run to invoke real Claude.",
    ),
    grounded: bool = typer.Option(
        True,
        "--grounded/--ungrounded",
        help="Build a ProjectProfile and pass it to the planning crew so proposals "
        "are grounded in real repo signals (default on).",
    ),
) -> None:
    """Run the weekly planning crew for a project. Produces a Decision Record awaiting approval."""
    from minions.onboarding import build_profile

    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(project, manifests)

    rprint(
        f"\n[bold]Planning sprint for[/bold] [cyan]{manifest.name}[/cyan] "
        f"(cadence={manifest.cadence_profile}, ${manifest.monthly_budget_usd}/mo)"
    )

    api_key: str | None = None
    if not dry_run:
        try:
            api_key = get_anthropic_api_key()
        except SecretNotFound as e:
            rprint(f"[red]Missing API key:[/red] {e}")
            raise typer.Exit(1) from e
        ok, message = anthropic_check.auth_check(api_key)
        if not ok:
            rprint(f"[red]Anthropic preflight failed:[/red] {message}")
            rprint("[dim]Run `minions anthropic` to retry the auth check on its own.[/dim]")
            raise typer.Exit(1)

    profile = None
    if grounded:
        from minions.dossiers.store_factory import make_dossier_store

        gh = _open_github_client(manifest) if manifest.source.kind == "github" else None
        try:
            profile = build_profile(
                manifest,
                github_client=gh,
                dossier_store=make_dossier_store(DOSSIER_DRAFTS_PATH),
            )
            rprint(
                f"[dim]Grounded with profile: {len(profile.languages)} langs, "
                f"{len(profile.package_files)} pkg files, "
                f"{profile.todo_count} TODOs, "
                f"{len(profile.open_issues)} open issues, "
                f"tasks.md remaining={profile.tasks_md.remaining if profile.tasks_md else 'n/a'}, "
                f"dossier={profile.dossier_freshness}[/dim]"
            )
        except Exception as e:  # noqa: BLE001
            rprint(f"[yellow]Profile build failed, continuing ungrounded:[/yellow] {e}")
            profile = None

    from minions.crews.planning import PlanningRefusedStaleError

    try:
        decision = run_planning_crew(manifest, dry_run=dry_run, api_key=api_key, profile=profile)
    except PlanningRefusedStaleError as refused:
        rprint(
            f"\n[yellow]Planning refused — dossier is very_stale.[/yellow]\n"
            f"Filed auto-approved discovery decision [bold]{refused.queued.id}[/bold].\n"
            f"Run `minions discover {manifest.name} --no-dry-run --force` or wait for "
            "the weekly discovery sweep."
        )
        submit_for_approval(refused.queued, store=_store(), notifier=_notifier())
        raise typer.Exit(0) from refused

    # §9.3 — risk≥medium decisions get a Devil's Advocate counter-argument
    # before they hit the operator's inbox. Skipped silently for dry-run / low-risk.
    from minions.config.portfolio import load_portfolio_config
    from minions.crews.devils_advocate import attach_critique, should_critique

    if should_critique(decision) and api_key is not None:
        portfolio = load_portfolio_config(CONFIG_PATH)
        try:
            attach_critique(decision, api_key=api_key, portfolio=portfolio)
            if decision.critique is not None:
                rprint(
                    f"[dim]Devil's Advocate attached "
                    f"({len(decision.critique.failure_modes)} failure modes flagged)[/dim]"
                )
        except Exception as e:  # noqa: BLE001 — never block approval flow on critique failure
            rprint(f"[yellow]Devil's Advocate failed (non-blocking): {e}[/yellow]")

    submit_for_approval(decision, store=_store(), notifier=_notifier())
    rprint(
        f"\n[green]Decision created and submitted for approval.[/green] "
        f"ID: [bold]{decision.id}[/bold]"
    )


@app.command()
def discover(
    project: str = typer.Argument(..., help="Project name (case-insensitive)."),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run collects RepoReadings + freshness but skips LLM calls and "
        "writes nothing. Use --no-dry-run to invoke the discoverer crew.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Ignore the freshness gate and re-run discovery even if the latest "
        "merged dossier is still 'ok'.",
    ),
) -> None:
    """Run the discoverer crew for a project. Produces a DossierDraft (drafted)."""
    from minions.dossiers.store_factory import make_dossier_store
    from minions.scheduled import run_discovery_sweep

    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(project, manifests)

    rprint(
        f"\n[bold]Discovering dossier for[/bold] [cyan]{manifest.name}[/cyan] "
        f"(monthly cap ${manifest.monthly_budget_usd}/mo, "
        f"force={force}, dry_run={dry_run})"
    )

    api_key: str | None = None
    if not dry_run:
        try:
            api_key = get_anthropic_api_key()
        except SecretNotFound as e:
            rprint(f"[red]Missing API key:[/red] {e}")
            raise typer.Exit(1) from e
        ok, message = anthropic_check.auth_check(api_key)
        if not ok:
            rprint(f"[red]Anthropic preflight failed:[/red] {message}")
            raise typer.Exit(1)

    store = make_dossier_store(DOSSIER_DRAFTS_PATH)
    report = run_discovery_sweep(
        projects_dir=PROJECTS_DIR,
        dossier_store=store,
        api_key=api_key,
        dry_run=dry_run,
        force=force,
        cost_log_path=COST_LOG_PATH,
        projects=[manifest.name],
        decision_store=_store(),
        notifier=_notifier(),
    )
    for o in report.outcomes:
        if o.status == "submitted":
            rprint(
                f"  [green]✓[/green] {o.project} — draft {(o.draft_id or '')[:8]} "
                f"at {(o.commit_sha or '')[:8]} (freshness was {o.freshness})"
            )
        elif (
            o.status == "skipped_fresh"
            or o.status == "skipped_target_missing"
            or o.status == "throttled"
        ):
            rprint(f"  [yellow]⊘[/yellow] {o.project} — {o.reason}")
        elif o.status == "verifier_failed":
            rprint(f"  [red]✗[/red] {o.project} — verifier rejected: {o.reason}")
        else:
            rprint(f"  [red]✗[/red] {o.project} — {o.reason}")


@dossier_app.command("show")
def dossier_show(
    project: str = typer.Argument(..., help="Project name (case-insensitive)."),
    full: bool = typer.Option(False, "--full", help="Print the full dossier markdown body."),
) -> None:
    """Print the latest merged dossier for a project, with freshness label."""
    from minions.dossiers.freshness import compute_freshness
    from minions.dossiers.store_factory import make_dossier_store

    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(project, manifests)
    store = make_dossier_store(DOSSIER_DRAFTS_PATH)

    latest = store.latest_merged(manifest.name)
    if latest is None:
        # Fall back to the most recent draft of any status so the operator sees
        # *something* during the period between first discovery and first merge.
        rows = store.list_for_project(manifest.name, limit=1)
        latest = rows[0] if rows else None

    if latest is None:
        rprint(
            f"[yellow]No dossier on file for {manifest.name}.[/yellow] "
            f"Run `minions discover {manifest.name} --no-dry-run` to create one."
        )
        return

    freshness = compute_freshness(
        latest if latest.status.value == "merged" else None,
        overrides=manifest.dossier.freshness_overrides,
    )
    rprint(
        f"\n[bold]{manifest.name}[/bold] dossier "
        f"({latest.status.value}, commit {latest.commit_sha[:8]}, "
        f"crew {latest.crew_version})"
    )
    rprint(
        f"  freshness: [cyan]{freshness.label}[/cyan] "
        f"(age {freshness.age_days}d) — {freshness.reason}"
    )
    if latest.verifier_log:
        rprint(f"  verifier: {latest.verifier_log.splitlines()[0]}")
    if latest.pr_url:
        rprint(f"  pr: {latest.pr_url}")
    if full:
        rprint("\n---\n")
        rprint(latest.markdown)


@dossier_app.command("sync")
def dossier_sync() -> None:
    """Reconcile dossier draft status against Decision + EngineerRun state."""
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.dossiers.store_factory import make_dossier_store
    from minions.dossiers.sync import sync_dossier_drafts
    from minions.learning.store_factory import make_agent_learning_store

    drafts = make_dossier_store(DOSSIER_DRAFTS_PATH)
    runs = make_engineer_runs_store(ENGINEER_RUNS_PATH)
    learning = make_agent_learning_store(AGENT_LEARNING_PATH)
    report = sync_dossier_drafts(
        dossier_store=drafts,
        decision_store=_store(),
        engineer_runs_store=runs,
        learning_store=learning,
    )
    rprint(
        f"\n[bold]Dossier sync[/bold] — "
        f"merged {report.merged}, rejected {report.rejected}, "
        f"superseded {report.superseded}, total transitions {len(report.transitions)}"
    )
    for t in report.transitions:
        rprint(
            f"  [cyan]{t.project}[/cyan] {t.draft_id[:8]}: "
            f"{t.from_status.value} → {t.to_status.value} ({t.reason})"
        )


@dossier_app.command("freshness")
def dossier_freshness() -> None:
    """Table: per-project dossier age + commit drift + freshness label."""
    from minions.dossiers.freshness import compute_freshness
    from minions.dossiers.store_factory import make_dossier_store

    store = make_dossier_store(DOSSIER_DRAFTS_PATH)
    manifests = load_active_manifests(PROJECTS_DIR)

    table = Table(title="Dossier freshness", show_lines=False)
    table.add_column("Project", style="bold")
    table.add_column("Status")
    table.add_column("Commit")
    table.add_column("Age (d)")
    table.add_column("Freshness")
    table.add_column("Reason")

    for name, manifest in manifests.items():
        latest = store.latest_merged(name)
        report = compute_freshness(latest, overrides=manifest.dossier.freshness_overrides)
        if latest is None:
            table.add_row(name, "—", "—", "—", report.label, report.reason)
        else:
            table.add_row(
                name,
                latest.status.value,
                latest.commit_sha[:8],
                str(report.age_days),
                report.label,
                report.reason,
            )
    console.print(table)


@backlog_app.command("propose")
def backlog_propose(
    project: str = typer.Argument(..., help="Project name (case-insensitive)."),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run skips LLM calls and never files a Decision Record.",
    ),
) -> None:
    """Run the backlog proposer crew against a project's latest merged dossier.

    Files a `backlog_proposal` Decision Record for the operator to approve;
    creation lands via `minions backlog create <decision-id>` after approval.
    """
    from minions.crews.backlog_proposer import run_backlog_proposer
    from minions.dossiers.backlog import file_backlog_after_merge
    from minions.dossiers.store_factory import make_dossier_store

    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(project, manifests)

    drafts = make_dossier_store(DOSSIER_DRAFTS_PATH)
    latest = drafts.latest_merged(manifest.name)
    if latest is None:
        rprint(
            f"[red]No merged dossier for {manifest.name}.[/red] "
            f"Run `minions discover {manifest.name} --no-dry-run` first."
        )
        raise typer.Exit(1)

    api_key: str | None = None
    if not dry_run:
        try:
            api_key = get_anthropic_api_key()
        except SecretNotFound as e:
            rprint(f"[red]Missing API key:[/red] {e}")
            raise typer.Exit(1) from e

    raw = run_backlog_proposer(manifest, latest, api_key=api_key, dry_run=dry_run)
    if raw is None:
        rprint(
            f"[yellow]Dry-run: skipped LLM. Re-run with --no-dry-run to "
            f"actually propose backlog issues for {manifest.name}.[/yellow]"
        )
        return

    rprint(
        f"[dim]Proposer returned {len(raw.candidates)} raw candidates; "
        f"applying dedupe + cap...[/dim]"
    )

    github = _open_github_client(manifest)
    decision = file_backlog_after_merge(
        raw=raw,
        manifest=manifest,
        dossier=latest,
        decision_store=_store(),
        notifier=_notifier(),
        github=github,
    )
    if decision is None:
        rprint(
            f"[yellow]Nothing left after dedupe + cap "
            f"({manifest.dossier.max_new_issues_per_cycle}/cycle) — no Decision filed.[/yellow]"
        )
        return
    rprint(
        f"[green]Backlog Decision filed:[/green] [bold]{decision.id}[/bold] "
        f"(approve via `minions decisions approve {str(decision.id)[:8]}`, "
        f"then `minions backlog create {str(decision.id)[:8]}`)."
    )


@backlog_app.command("create")
def backlog_create(
    decision_id: str = typer.Argument(..., help="Approved backlog-proposal Decision id."),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run skips GitHub mutations; prints what would be opened.",
    ),
) -> None:
    """Create one GitHub issue per surviving backlog candidate."""
    from minions.dossiers.backlog import (
        create_issues_for_decision,
        is_backlog_proposal_decision,
        proposal_from_decision,
    )
    from minions.models.decision import DecisionStatus

    decision = _find_by_prefix(decision_id)
    if decision is None:
        raise typer.Exit(1)
    if not is_backlog_proposal_decision(decision):
        rprint("[red]Decision is not a backlog proposal[/red] — wrong type or missing payload.")
        raise typer.Exit(1)
    if decision.status is not DecisionStatus.APPROVED:
        rprint(
            f"[red]Decision is {decision.status.value}; backlog worker only "
            f"runs on APPROVED decisions.[/red]"
        )
        raise typer.Exit(1)

    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(decision.project, manifests)

    if dry_run:
        proposal = proposal_from_decision(decision)
        if proposal is None:
            rprint("[red]Proposal payload could not be parsed.[/red]")
            raise typer.Exit(1)
        rprint(
            f"\n[bold]Dry run[/bold] — would file {len(proposal.candidates)} "
            f"issue(s) on [cyan]{manifest.source.repo}[/cyan]:"
        )
        for cand in proposal.candidates:
            rprint(f"  [{cand.label()}] {cand.title}")
        return

    github = _open_github_client(manifest)
    if github is None:
        rprint("[red]Failed to open GitHub client.[/red]")
        raise typer.Exit(1)
    with github:
        outcome = create_issues_for_decision(decision=decision, manifest=manifest, github=github)
    rprint(
        f"\n[bold]Backlog create[/bold] — "
        f"created {len(outcome.created)}, "
        f"dropped {len(outcome.dropped)}, capped {outcome.capped}"
    )
    for issued in outcome.created:
        rprint(f"  [green]✓[/green] #{issued.number} {issued.title} — {issued.html_url}")
    for cand, reason in outcome.dropped:
        rprint(f"  [yellow]⊘[/yellow] {cand.title} — {reason}")

    # Emit one CTO learning event so the executive layer's memory captures
    # the backlog-proposal round-trip. Best-effort — never blocks the worker.
    from contextlib import suppress

    from minions.dossiers.exec_events import record_backlog_proposed
    from minions.learning.store_factory import make_agent_learning_store

    proposal_payload = proposal_from_decision(decision)
    if proposal_payload is not None:
        with suppress(Exception):
            learning_store = make_agent_learning_store(AGENT_LEARNING_PATH)
            event = record_backlog_proposed(
                decision=decision,
                proposal=proposal_payload,
                learning_store=learning_store,
                created_count=len(outcome.created),
            )
            if event is not None:
                rprint(f"  [dim]cto/learning: {event.id}[/dim]")


@transcripts_app.command("show")
def transcripts_show(
    run_id: str = typer.Argument(..., help="run_id from activity feed / engineer run."),
) -> None:
    """Print the per-agent conversation for one crew run."""
    from minions.transcripts.store_factory import make_transcript_store

    store = make_transcript_store(CREW_TRANSCRIPTS_PATH)
    rows = store.list_by_run(run_id)
    if not rows:
        rprint(f"[yellow]No transcript rows for run_id={run_id!r}.[/yellow]")
        raise typer.Exit(0)

    head = rows[0]
    rprint(
        f"\n[bold]{head.crew} crew · {head.project}[/bold] "
        f"[dim]({len(rows)} message(s), run {run_id[:12]})[/dim]\n"
    )
    phase_color = {
        "pitch": "cyan",
        "rebuttal": "yellow",
        "synthesis": "green",
        "review": "magenta",
        "task_output": "white",
        "other": "dim",
    }
    for m in rows:
        color = phase_color.get(m.role_in_conversation, "white")
        name = m.agent_display_name or m.agent_role
        rprint(
            f"[{color}]#{m.sequence:>2} [{m.role_in_conversation}][/{color}] "
            f"[bold]{name}[/bold] [dim]({m.agent_role})[/dim]"
        )
        rprint(m.content)
        rprint("[dim]" + "─" * 60 + "[/dim]")


@transcripts_app.command("list")
def transcripts_list(
    project: str = typer.Argument(..., help="Project name (case-insensitive)."),
    limit: int = typer.Option(20, "--limit", "-n", help="Max recent runs to list."),
) -> None:
    """List recent crew runs for a project with their message counts."""
    from minions.transcripts.store_factory import make_transcript_store

    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(project, manifests)
    store = make_transcript_store(CREW_TRANSCRIPTS_PATH)
    rows = store.list_for_project(manifest.name, limit=200)
    if not rows:
        rprint(f"[yellow]No transcripts recorded yet for {manifest.name}.[/yellow]")
        return

    # Group by run_id (ordered by recency from the store).
    by_run: dict[str, list] = {}
    for m in rows:
        by_run.setdefault(m.run_id, []).append(m)

    table = Table(
        title=f"Recent crew runs · {manifest.name}",
        show_lines=False,
    )
    table.add_column("run", style="dim")
    table.add_column("crew")
    table.add_column("msgs")
    table.add_column("agents")
    table.add_column("started")
    table.add_column("first message")

    for run_id, msgs in list(by_run.items())[:limit]:
        msgs.sort(key=lambda m: m.sequence)
        first = msgs[0]
        agents = sorted({m.agent_display_name or m.agent_role for m in msgs})
        preview = first.content.replace("\n", " ")[:60]
        table.add_row(
            run_id[:10],
            first.crew,
            str(len(msgs)),
            ", ".join(agents)[:40],
            first.created_at.strftime("%m-%d %H:%M"),
            preview + ("…" if len(first.content) > 60 else ""),
        )
    console.print(table)


# =============================================================================
# decisions subcommands
# =============================================================================


@decisions_app.command("list")
def list_decisions(
    status: str = typer.Option("pending", help="pending|approved|rejected|all"),
) -> None:
    """List Decision Records by status."""
    store = _store()
    if status == "all":
        records = store.list_all()
    else:
        from minions.models.decision import DecisionStatus

        try:
            records = store.list_by_status(DecisionStatus(status))
        except ValueError as e:
            rprint(f"[red]invalid status '{status}'[/red]")
            raise typer.Exit(1) from e

    if not records:
        rprint(f"[dim]no decisions matching status={status}[/dim]")
        return

    table = Table(title=f"Decisions (status={status})")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Project")
    table.add_column("Type")
    table.add_column("Risk")
    table.add_column("Pri", no_wrap=True)
    table.add_column("Status", style="bold")
    table.add_column("Summary")
    for d in records:
        pri = d.priority + ("!" if d.expedited else "")
        table.add_row(
            str(d.id)[:8] + "…",
            d.project,
            d.type.value,
            d.risk,
            pri,
            d.status.value,
            d.summary[:60],
        )
    console.print(table)


@decisions_app.command("show")
def show_decision(decision_id: str = typer.Argument(...)) -> None:
    """Show full details of a Decision Record (id prefix is OK)."""
    decision = _find_by_prefix(decision_id)
    if decision is None:
        raise typer.Exit(1)
    rprint(f"[bold]ID:[/bold]        {decision.id}")
    rprint(f"[bold]Project:[/bold]   {decision.project}")
    rprint(f"[bold]Type:[/bold]      {decision.type.value}")
    rprint(f"[bold]Status:[/bold]    {decision.status.value}")
    rprint(f"[bold]Risk:[/bold]      {decision.risk}")
    pri_line = decision.priority + ("  [yellow]expedited[/yellow]" if decision.expedited else "")
    if decision.requested_by_role:
        pri_line += f"  requested_by={decision.requested_by_role}"
    rprint(f"[bold]Priority:[/bold]  {pri_line}")
    rprint(f"[bold]Proposer:[/bold]  {decision.proposer_agent_id} ({decision.proposer_role})")
    rprint(f"[bold]Created:[/bold]   {decision.created_at}")
    if decision.resolved_at is not None:
        rprint(
            f"[bold]Resolved:[/bold]  {decision.resolved_at} ({decision.resolved_reason or '—'})"
        )
    rprint(f"\n[bold]Rationale:[/bold]\n{decision.rationale}")
    rprint(f"\n[bold]Plan:[/bold]\n{decision.diff_or_plan or '(none)'}")


@decisions_app.command("approve")
def approve_decision(
    decision_id: str = typer.Argument(...),
    reason: str | None = typer.Option(None, "--reason", "-r"),
) -> None:
    """Approve a pending Decision Record (id prefix is OK)."""
    decision = _find_by_prefix(decision_id)
    if decision is None:
        raise typer.Exit(1)
    resolved = resolve(
        decision.id, store=_store(), notifier=_notifier(), action="approve", reason=reason
    )
    rprint(f"[green]✓ approved[/green] {resolved.id}")


@decisions_app.command("approve-all")
def approve_all_decisions(
    project: str | None = typer.Option(None, "--project", "-p", help="Limit to one project."),
    risk: str | None = typer.Option(
        None, "--risk", help="Limit to one risk level (low|medium|high)."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    reason: str | None = typer.Option("operator: bulk approve", "--reason", "-r"),
) -> None:
    """Approve every PENDING Decision (filtered by --project / --risk)."""
    store = _store()
    pending = store.list_by_status(DecisionStatus.PENDING)
    if project is not None:
        pending = [d for d in pending if d.project == project]
    if risk is not None:
        pending = [d for d in pending if d.risk == risk]

    if not pending:
        rprint("[dim]No matching pending decisions.[/dim]")
        return

    rprint(f"[bold]{len(pending)} pending decision(s):[/bold]")
    for d in pending:
        rprint(f"  · {str(d.id)[:8]} — {d.project} — [{d.risk}] {d.title[:60]}")

    if not yes:
        confirm = typer.confirm(f"\nApprove all {len(pending)}?", default=False)
        if not confirm:
            rprint("[yellow]Aborted.[/yellow]")
            raise typer.Exit(1)

    notifier = _notifier()
    approved = 0
    for d in pending:
        try:
            resolve(d.id, store=store, notifier=notifier, action="approve", reason=reason)
            approved += 1
        except Exception as e:  # noqa: BLE001
            rprint(f"  [red]✗[/red] {str(d.id)[:8]} — {e}")
    rprint(f"\n[green]✓ approved {approved}/{len(pending)}[/green]")


@decisions_app.command("sweep")
def sweep_decisions(
    timeout_hours: float = typer.Option(72.0, "--ttl-hours"),
) -> None:
    """Auto-reject pending decisions older than --ttl-hours (default 72)."""
    from minions.approval.service import sweep_timeouts as _sweep

    timed_out = _sweep(store=_store(), notifier=_notifier(), ttl_hours=timeout_hours)
    if not timed_out:
        rprint(f"[green]No pending decisions older than {timeout_hours:g}h.[/green]")
        return
    rprint(f"[yellow]Auto-rejected {len(timed_out)} stale decision(s):[/yellow]")
    for d in timed_out:
        rprint(f"  [red]✗[/red] {str(d.id)[:8]} — {d.project} — {d.summary[:60]}")


@decisions_app.command("reject")
def reject_decision(
    decision_id: str = typer.Argument(...),
    reason: str | None = typer.Option(None, "--reason", "-r"),
) -> None:
    """Reject a pending Decision Record (id prefix is OK)."""
    from minions.tasks.store_factory import make_task_store

    decision = _find_by_prefix(decision_id)
    if decision is None:
        raise typer.Exit(1)
    resolved = resolve(
        decision.id,
        store=_store(),
        notifier=_notifier(),
        action="reject",
        reason=reason,
        task_store=make_task_store(TASKS_PATH),
    )
    rprint(f"[red]✗ rejected[/red] {resolved.id}")


@decisions_app.command("reject-dry-runs")
def reject_dry_runs(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    reason: str | None = typer.Option(
        "operator: dry-run noise — no real plan to execute",
        "--reason",
        "-r",
    ),
) -> None:
    """Reject every APPROVED Decision whose summary contains ``[DRY RUN]``.

    These rows are seeded by ``minions plan --dry-run`` and are permanently
    filtered out by ``execute-approved`` (see ``_is_dry_run_decision``). Left
    APPROVED, they clog the operator's sprint board forever.
    """
    store = _store()
    approved = store.list_by_status(DecisionStatus.APPROVED)
    targets = [d for d in approved if "[DRY RUN]" in (d.summary or "")]

    if not targets:
        rprint("[green]No dry-run noise in APPROVED.[/green]")
        return

    rprint(f"[bold]{len(targets)} dry-run Decision(s) to reject:[/bold]")
    for d in targets:
        rprint(f"  · {str(d.id)[:8]} — {d.project} — {d.summary[:70]}")

    if not yes:
        confirm = typer.confirm(f"\nReject all {len(targets)}?", default=False)
        if not confirm:
            rprint("[yellow]Aborted.[/yellow]")
            raise typer.Exit(1)

    notifier = _notifier()
    rejected = 0
    for d in targets:
        try:
            resolve(d.id, store=store, notifier=notifier, action="reject", reason=reason)
            rejected += 1
        except Exception as e:  # noqa: BLE001
            rprint(f"  [red]✗[/red] {str(d.id)[:8]} — {e}")
    rprint(f"\n[red]✗ rejected {rejected}/{len(targets)}[/red]")


@decisions_app.command("priority")
def set_decision_priority(
    decision_id: str = typer.Argument(..., help="Decision id or prefix."),
    level: str = typer.Argument("p1", help="p1 | p2 | p3"),
    expedited: bool = typer.Option(
        True,
        "--expedited/--no-expedited",
        help="Mark as expedited to jump ahead of non-expedited work at the same priority.",
    ),
    by: str | None = typer.Option(
        None,
        "--by",
        help="Role requesting the bump (e.g. cto, ceo, operator).",
    ),
) -> None:
    """Stamp priority/expedited/requested-by on a Decision.

    Operator escape hatch for the case where the originating UI/agent path
    did not stamp the fields. ``execute-approved`` orders by these fields,
    so a p1-expedited Decision jumps ahead of older backlog on the next
    sweep (or immediately on a ``--only-expedited`` run).
    """
    if level not in {"p1", "p2", "p3"}:
        rprint(f"[red]invalid priority '{level}' — expected p1|p2|p3[/red]")
        raise typer.Exit(1)
    store = _store()
    decision = _find_by_prefix(decision_id)
    if decision is None:
        raise typer.Exit(1)
    decision.priority = level  # type: ignore[assignment]
    decision.expedited = expedited
    if by is not None:
        decision.requested_by_role = by
    store.save(decision)
    rprint(
        f"[green]✓[/green] {str(decision.id)[:8]} → priority={level} "
        f"expedited={expedited} requested_by={decision.requested_by_role or '—'}"
    )


@app.command("anthropic")
def anthropic_diag() -> None:
    """Verify Anthropic API key — cheap auth check, no token cost.

    Also detects shell-vs-.env conflicts (a common gotcha: edit .env, but a
    stale shell-exported var keeps winning).
    """
    try:
        api_key = get_anthropic_api_key()
    except SecretNotFound as e:
        rprint(f"[red]✗ no API key found:[/red] {e}")
        raise typer.Exit(1) from e

    # Detect shell vs .env mismatch — most common cause of "I just rotated
    # the key but it's still failing".
    dotenv_value = _parse_dotenv().get("ANTHROPIC_API_KEY")
    if dotenv_value and dotenv_value != api_key:
        rprint(
            "[yellow]⚠ shell and .env disagree on ANTHROPIC_API_KEY.[/yellow]\n"
            f"  shell : ...{api_key[-4:]} (length {len(api_key)})  ← currently used\n"
            f"  .env  : ...{dotenv_value[-4:]} (length {len(dotenv_value)})\n"
            "  Fix: [bold]unset ANTHROPIC_API_KEY[/bold] in this shell, "
            "or run with [bold]MINIONS_DOTENV_OVERRIDE=1[/bold] to flip precedence."
        )

    rprint(f"[bold]Resolved key:[/bold] {api_key[:10]}…{api_key[-4:]} (length {len(api_key)})")
    ok, message = anthropic_check.auth_check(api_key)
    if ok:
        rprint(f"[green]✓ {message}[/green]")
    else:
        rprint(f"[red]✗ {message}[/red]")
        raise typer.Exit(1)


@app.command("langfuse")
def langfuse_check() -> None:
    """Verify Langfuse observability — credentials, auth, host."""
    if not langfuse_has_credentials():
        rprint("[yellow]Langfuse disabled[/yellow]")
        rprint(
            "  [dim]Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (in .env or shell). "
            "See README → 'Langfuse'.[/dim]"
        )
        return
    rprint(f"[bold]Host:[/bold] {langfuse_host_url()}")
    ok, message = auth_check()
    if ok:
        rprint(f"[green]✓ {message}[/green]")
        rprint(
            f"[dim]LiteLLM callbacks active — every CrewAI LLM call will trace to "
            f"{langfuse_host_url()}/traces[/dim]"
        )
    else:
        rprint(f"[red]✗ auth check failed:[/red] {message}")
        raise typer.Exit(1)


@app.command("notify-test")
def notify_test() -> None:
    """Send a test approval notification — verifies notifier setup end-to-end.

    Picks the notifier per ``MINIONS_NOTIFIER``. With Gmail, you should see a
    real email arrive at the operator address within seconds.
    """
    notifier = _notifier()
    test_decision = Decision(
        project="test",
        type=DecisionType.OTHER,
        summary="Notifier test — please ignore",
        rationale="Verifying that the configured notifier can deliver an approval request.",
        diff_or_plan="(no plan; this is a synthetic test message)",
        proposer_role="manager",
        proposer_agent_id="manager@test",
        proposer_display_name="Test Bot",
    )
    rprint(f"[bold]Notifier:[/bold] {notifier.__class__.__name__}")
    try:
        notifier.notify_approval_request(test_decision)
    except Exception as e:
        rprint(f"[red]Notifier failed:[/red] {type(e).__name__}: {e}")
        raise typer.Exit(1) from e
    rprint("[green]✓ Test notification dispatched.[/green]")


# =============================================================================
# secrets subcommands
# =============================================================================


@secrets_app.command("backends")
def secrets_backends() -> None:
    """List the active secret backends in resolution order."""
    labels = secrets_module.list_backends()
    rprint("[bold]Active backends (in order):[/bold]")
    for i, label in enumerate(labels, 1):
        rprint(f"  {i}. {label}")


@secrets_app.command("check")
def secrets_check(
    name: str = typer.Argument(..., help="Secret name (e.g. 'anthropic-api-key')"),
) -> None:
    """Try to retrieve a secret. Reports success without printing the value."""
    try:
        value = secrets_module.get_secret(name)
    except SecretNotFound as e:
        rprint(f"[red]✗ {name} not found[/red]\n   {e}")
        raise typer.Exit(1) from e
    masked = value[:4] + "…" + value[-2:] if len(value) > 8 else "***"
    rprint(f"[green]✓ {name} resolved[/green] (masked: {masked}, length: {len(value)})")


# =============================================================================
# github subcommands (read-only diagnostics)
# =============================================================================


def _open_github_client(manifest: Manifest) -> GitHubClient | None:
    if manifest.source.kind != "github":
        rprint(
            f"[yellow]Project {manifest.name} is local-only "
            f"(source.kind={manifest.source.kind}); no GitHub repo to check.[/yellow]"
        )
        return None
    repo = (manifest.source.repo or "").strip()
    if not repo or repo.upper() == "TBD" or "/" not in repo:
        rprint(
            f"[yellow]Project {manifest.name} has source.kind=github but repo is not "
            f"yet configured (repo={manifest.source.repo!r}). Skipping GitHub enrichment; "
            f"local-only signals will be used.[/yellow]"
        )
        return None
    try:
        token = get_github_token()
    except SecretNotFound as e:
        rprint(f"[red]Missing GitHub token:[/red] {e}")
        rprint("[dim]Set GITHUB_TOKEN env or create AWS secret minions/github-token.[/dim]")
        raise typer.Exit(1) from e
    return GitHubClient(token=token, repo=repo)


@github_app.command("check")
def github_check(project: str = typer.Argument(..., help="Project name (e.g., demo)")) -> None:
    """Verify GitHub connectivity for a project — fetches repo metadata + a few open issues."""
    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(project, manifests)
    client = _open_github_client(manifest)
    if client is None:
        return
    with client:
        try:
            repo = client.get_repo()
        except GitHubError as e:
            rprint(f"[red]GitHub API error:[/red] {e}")
            raise typer.Exit(1) from e
        rprint(
            f"[green]✓[/green] [bold]{repo.full_name}[/bold] "
            f"(default: {repo.default_branch}, private: {repo.private})"
        )
        try:
            issues = client.list_open_issues(per_page=10)
        except GitHubError as e:
            rprint(f"[yellow]could not list issues:[/yellow] {e}")
            return
        rprint(f"  open issues: {len(issues)}")
        for issue in issues[:5]:
            labels = f" [dim]({', '.join(issue.labels)})[/dim]" if issue.labels else ""
            rprint(f"    #{issue.number} — {issue.title[:70]}{labels}")


@github_app.command("issues")
def github_issues(
    project: str = typer.Argument(...),
    label: str | None = typer.Option(None, "--label", "-l", help="Filter by label"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """List open issues for a project (read-only)."""
    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(project, manifests)
    client = _open_github_client(manifest)
    if client is None:
        return
    with client:
        try:
            issues = client.list_open_issues(label=label, per_page=limit)
        except GitHubError as e:
            rprint(f"[red]GitHub API error:[/red] {e}")
            raise typer.Exit(1) from e
        if not issues:
            rprint(f"[dim]no open issues{f' with label {label!r}' if label else ''}[/dim]")
            return
        for issue in issues:
            labels = f" [dim]({', '.join(issue.labels)})[/dim]" if issue.labels else ""
            rprint(f"  #{issue.number} — {issue.title[:80]}{labels}")
            rprint(f"      [dim]{issue.html_url}[/dim]")


def _find_by_prefix(decision_id: str) -> Decision | None:  # noqa: F821
    store = _store()
    # Allow exact uuid or prefix match (>= 4 chars to avoid collisions).
    if len(decision_id) >= 32:
        d = store.get(decision_id)
        if d is None:
            rprint(f"[red]no decision with id {decision_id}[/red]")
        return d
    matches = [d for d in store.list_all() if str(d.id).startswith(decision_id)]
    if not matches:
        rprint(f"[red]no decision with id prefix '{decision_id}'[/red]")
        return None
    if len(matches) > 1:
        rprint(f"[red]ambiguous prefix '{decision_id}' — matches:[/red]")
        for d in matches:
            rprint(f"  {d.id}")
        return None
    return matches[0]


@cron_app.command("weekly")
def cron_weekly(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Dry run skips LLM calls."),
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Limit planning scan to one project.",
    ),
) -> None:
    """Manually trigger the Monday weekly planning sweep."""
    from minions.agents.memory_store_factory import make_agent_memory_store
    from minions.agile.store_factory import make_agile_store
    from minions.dossiers.store_factory import make_dossier_store
    from minions.scheduled import run_weekly_planning

    api_key: str | None = None
    if not dry_run:
        try:
            api_key = get_anthropic_api_key()
        except SecretNotFound as e:
            rprint(f"[red]Missing API key:[/red] {e}")
            raise typer.Exit(1) from e

    from minions.config.portfolio import load_portfolio_config

    report = run_weekly_planning(
        projects_dir=PROJECTS_DIR,
        store=_store(),
        notifier=_notifier(),
        api_key=api_key,
        dry_run=dry_run,
        open_github_client=_open_github_client,
        cost_log_path=COST_LOG_PATH,
        budget_notifications_path=BUDGET_NOTIFICATIONS_PATH,
        portfolio=load_portfolio_config(CONFIG_PATH),
        projects=[project] if project else None,
        agile_store=make_agile_store(AGILE_PATH),
        sprints_path=SPRINTS_PATH,
        memory_store=make_agent_memory_store(AGENT_MEMORY_PATH),
        dossier_store=make_dossier_store(DOSSIER_DRAFTS_PATH),
    )
    rprint(
        f"\n[bold]Weekly planning sweep[/bold] — "
        f"submitted {report.submitted}, throttled {report.throttled}, errored {report.errored}"
    )
    for o in report.outcomes:
        if o.status == "submitted":
            extra = f" [dim]({o.profile_summary})[/dim]" if o.profile_summary else ""
            rprint(f"  [green]✓[/green] {o.project} — decision {str(o.decision_id)[:8]}{extra}")
        elif o.status == "throttled":
            rprint(f"  [yellow]⊘[/yellow] {o.project} — {o.error}")
        else:
            rprint(f"  [red]✗[/red] {o.project} — {o.error}")


@cron_app.command("daily")
def cron_daily(
    sweep_timeouts: bool = typer.Option(
        True,
        "--sweep-timeouts/--no-sweep-timeouts",
        help="Auto-reject pending decisions older than 72h (default on).",
    ),
    timeout_hours: float = typer.Option(
        72.0, "--timeout-hours", help="TTL for pending decisions before auto-reject."
    ),
) -> None:
    """Manually trigger the daily monitoring sweep (read-only profile + timeout sweep + PR sync + audit)."""
    from minions.audit.store_factory import make_audit_findings_store
    from minions.config.portfolio import load_portfolio_config
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.scheduled import run_daily_monitor

    api_key: str | None = None
    portfolio = None
    try:
        api_key = get_anthropic_api_key()
        portfolio = load_portfolio_config(CONFIG_PATH)
    except SecretNotFound:
        api_key = None  # PR sync still runs; only the auditor needs the key

    report = run_daily_monitor(
        projects_dir=PROJECTS_DIR,
        open_github_client=_open_github_client,
        store=_store() if sweep_timeouts else None,
        notifier=_notifier() if sweep_timeouts else None,
        timeout_hours=timeout_hours,
        engineer_runs_store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
        audit_findings_store=make_audit_findings_store(AUDIT_FINDINGS_PATH) if api_key else None,
        api_key=api_key,
        portfolio=portfolio,
    )
    rprint(report.to_markdown())


@cron_app.command("discovery")
def cron_discovery(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run skips LLM calls. Use --no-dry-run for real discovery runs.",
    ),
    force: bool = typer.Option(
        False, "--force", help="Run discovery for every project regardless of freshness."
    ),
    project: str | None = typer.Option(
        None, "--project", "-p", help="Limit the sweep to a single project (case-insensitive)."
    ),
) -> None:
    """Weekly discovery sweep — runs the discoverer across active projects."""
    from minions.dossiers.store_factory import make_dossier_store
    from minions.scheduled import run_discovery_sweep

    api_key: str | None = None
    if not dry_run:
        try:
            api_key = get_anthropic_api_key()
        except SecretNotFound as e:
            rprint(f"[red]Missing API key:[/red] {e}")
            raise typer.Exit(1) from e

    store = make_dossier_store(DOSSIER_DRAFTS_PATH)
    report = run_discovery_sweep(
        projects_dir=PROJECTS_DIR,
        dossier_store=store,
        api_key=api_key,
        dry_run=dry_run,
        force=force,
        cost_log_path=COST_LOG_PATH,
        projects=[project] if project else None,
        decision_store=_store(),
        notifier=_notifier(),
    )
    rprint(
        f"\n[bold]Discovery sweep[/bold] — "
        f"submitted {report.submitted}, skipped {report.skipped}, errored {report.errored}"
    )
    for o in report.outcomes:
        if o.status == "submitted":
            rprint(
                f"  [green]✓[/green] {o.project} — draft {(o.draft_id or '')[:8]} "
                f"at {(o.commit_sha or '')[:8]}"
            )
        elif o.status in ("skipped_fresh", "skipped_target_missing", "throttled"):
            rprint(f"  [yellow]⊘[/yellow] {o.project} — {o.reason}")
        else:
            rprint(f"  [red]✗[/red] {o.project} — {o.reason}")


@cron_app.command("crew-heartbeat")
def cron_crew_heartbeat() -> None:
    """Record availability check-ins for every configured crew role."""
    from minions.scheduled import run_crew_heartbeat

    report = run_crew_heartbeat(projects_dir=PROJECTS_DIR)
    rprint(
        f"\n[bold]Crew heartbeat[/bold] — checked_in {report.checked_in}, errored {report.errored}"
    )
    for o in report.outcomes:
        tag = "[green]✓[/green]" if o.status == "checked_in" else "[red]✗[/red]"
        label = o.project or o.scope
        line = f"  {tag} {label} — {len(o.roles)} role(s)"
        if o.error:
            line += f" — {o.error[:80]}"
        rprint(line)


@cron_app.command("scrum")
def cron_scrum(
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--no-dry-run",
        help="Dry run prints what would be recorded without writing Agile artifacts.",
    ),
) -> None:
    """Run the two-day Agile scrum ritual for every active project."""
    from minions.agile.store_factory import make_agile_store
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.questions.store_factory import make_question_store
    from minions.scheduled import run_scrum

    report = run_scrum(
        projects_dir=PROJECTS_DIR,
        store=_store(),
        engineer_runs_store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
        agile_store=make_agile_store(AGILE_PATH),
        questions_store=make_question_store(QUESTIONS_PATH),
        dry_run=dry_run,
    )
    rprint(f"\n[bold]Scrum ritual[/bold] — recorded {report.recorded}, errored {report.errored}")
    for o in report.outcomes:
        tag = "[green]✓[/green]" if o.status == "recorded" else "[red]✗[/red]"
        line = f"  {tag} {o.project}"
        if o.ritual_id:
            line += f" ritual={o.ritual_id[:8]}"
        if o.blockers:
            line += f" blockers={len(o.blockers)}"
        if o.error:
            line += f" — {o.error[:80]}"
        rprint(line)


@cron_app.command("friday")
def cron_friday(
    send: bool = typer.Option(
        True, "--send/--no-send", help="Push the digest through the notifier."
    ),
    days: int = typer.Option(7, "--days", help="Window in days for the digest summary."),
) -> None:
    """Manually trigger the Friday weekly digest."""
    from minions.scheduled import run_friday_digest

    report = run_friday_digest(
        projects_dir=PROJECTS_DIR,
        store=_store(),
        notifier=_notifier(),
        week_window_days=days,
        open_github_client=_open_github_client,
        send=send,
    )
    rprint(
        f"\n[dim]Pending {report.pending} · approved {report.approved} · "
        f"rejected {report.rejected} · executed {report.executed}[/dim]"
    )
    rprint(report.body)


@cron_app.command("monthly")
def cron_monthly(
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run", help="Dry run skips LLM calls."),
) -> None:
    """Manually trigger the monthly executive portfolio review."""
    from minions.agile.store_factory import make_agile_store
    from minions.audit.store_factory import make_audit_findings_store
    from minions.config.portfolio import load_portfolio_config
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.questions.store_factory import make_question_store
    from minions.scheduled import run_monthly_portfolio_review

    api_key: str | None = None
    if not dry_run:
        try:
            api_key = get_anthropic_api_key()
        except SecretNotFound as e:
            rprint(f"[red]Missing API key:[/red] {e}")
            raise typer.Exit(1) from e

    portfolio = load_portfolio_config(CONFIG_PATH)
    report = run_monthly_portfolio_review(
        projects_dir=PROJECTS_DIR,
        store=_store(),
        engineer_runs_store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
        notifier=_notifier(),
        audit_findings_store=make_audit_findings_store(AUDIT_FINDINGS_PATH),
        questions_store=make_question_store(QUESTIONS_PATH),
        api_key=api_key,
        dry_run=dry_run,
        cost_log_path=COST_LOG_PATH,
        portfolio=portfolio,
        agile_store=make_agile_store(AGILE_PATH),
    )
    if report.status == "submitted":
        rprint(
            f"\n[bold]Monthly portfolio review[/bold] — submitted decision "
            f"{str(report.decision_id)[:8]} for {report.period} "
            f"({report.projects_count} project(s))"
        )
    else:
        rprint(f"\n[red]Monthly portfolio review failed:[/red] {report.error}")
        raise typer.Exit(1)


@cron_app.command("post-deploy-verify")
def cron_post_deploy_verify(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run inspects state but never probes URLs or files revert decisions.",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Limit the sweep to a single project (case-insensitive).",
    ),
) -> None:
    """Probe each project's deployed URL after merge; revert-Decision on failure.

    Walks every active project with deploy.target != "none", looks up
    the merged sha, runs deterministic HTTP probes against
    deploy.production_url + each deploy.health_checks path + the first
    N <img src> URLs on the home page. On any failure files a
    risk=high revert Decision (deduped per sha).
    """
    from minions.deployments.store_factory import make_deployment_store
    from minions.scheduled.post_deploy_verify import run_post_deploy_verify

    report = run_post_deploy_verify(
        projects_dir=PROJECTS_DIR,
        deployment_store=make_deployment_store(DEPLOYMENTS_PATH),
        decision_store=_store(),
        notifier=_notifier(),
        open_github_client=_open_github_client,
        dry_run=dry_run,
        projects=[project] if project else None,
    )
    rprint(
        f"\n[bold]Post-deploy verify[/bold] — "
        f"healthy {report.healthy}, unhealthy {report.unhealthy}, "
        f"errored {sum(1 for o in report.outcomes if o.status == 'error')}"
    )
    for o in report.outcomes:
        icon = {
            "healthy": "[green]✓[/green]",
            "unhealthy": "[red]✗[/red]",
            "failed": "[red]✗[/red]",
            "abandoned": "[yellow]⊘[/yellow]",
            "skipped": "[dim]·[/dim]",
            "error": "[red]✗[/red]",
        }.get(o.status, "?")
        details = f"{o.failed_probes}/{o.total_probes} probes failed" if o.total_probes else ""
        revert = f"  revert={o.revert_decision_id[:8]}" if o.revert_decision_id else ""
        rprint(
            f"  {icon} {o.project} sha={(o.merge_sha or '?')[:8]} "
            f"{details}{revert}  ({o.reason or o.status})"
        )


@cron_app.command("site-sentry")
def cron_site_sentry(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run probes the sites but never writes samples to Postgres.",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Limit the sweep to a single project (case-insensitive).",
    ),
    scope: str = typer.Option(
        "all",
        "--scope",
        help="'health' (uptime + cert probes), 'renewals' (license/rotation "
        "radar), or 'all'. Lets the two halves run on separate cadences.",
    ),
) -> None:
    """Probe each project's production URL and persist a health sample.

    Continuous synthetic monitoring: walks every active project with a
    deploy.production_url, runs the same deterministic HTTP probes as
    post-deploy verify, and appends one site_health_samples row per probe.
    The operator console's Sentry page reads the latest sample per
    (project, check_path) plus 24h uptime/latency rollups. No LLM, no
    GitHub writes, no Decisions. Use --scope to split health probes (run
    often) from the renewal radar (run daily).
    """
    from minions.scheduled.site_sentry import run_site_sentry

    if scope not in ("all", "health", "renewals"):
        rprint(f"[red]Invalid --scope {scope!r}[/red] (expected all|health|renewals)")
        raise typer.Exit(2)

    report = run_site_sentry(
        projects_dir=PROJECTS_DIR,
        dry_run=dry_run,
        projects=[project] if project else None,
        scope=scope,
    )
    persisted = "persisted" if report.persisted else "[dim]not persisted (dry-run/no-db)[/dim]"
    rprint(
        f"\n[bold]Site Sentry[/bold] — probed {report.projects_probed} project(s), "
        f"{report.samples_written} sample(s), {report.renewals_due_soon} renewal(s) due soon, "
        f"{persisted} [dim](tenant {report.tenant_id})[/dim]"
    )
    for o in report.outcomes:
        icon = {
            "probed": "[green]✓[/green]" if o.unhealthy == 0 else "[red]✗[/red]",
            "skipped": "[dim]·[/dim]",
            "error": "[red]✗[/red]",
        }.get(o.status, "?")
        detail = (
            f"{o.healthy}/{len(o.samples)} checks healthy"
            if o.status == "probed"
            else (o.reason or o.status)
        )
        rprint(f"  {icon} {o.project} — {detail}")
    for r in report.renewals:
        if r.severity == "ok":
            continue
        color = {"amber": "yellow", "red": "red", "overdue": "red"}.get(r.severity, "yellow")
        when = f"{r.days_until}d" if r.days_until >= 0 else f"{-r.days_until}d overdue"
        rprint(f"  [{color}]⧗[/{color}] {r.project} — {r.kind} '{r.name}' due {r.due} ({when})")


@cron_app.command("pr-owner-sweep")
def cron_pr_owner_sweep(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run inspects state + reports what would happen but never "
        "dispatches the engineer crew, never writes Question Records.",
    ),
    max_dispatches: int = typer.Option(
        5,
        "--max-dispatches",
        help="Max number of engineer-crew dispatches per sweep tick.",
    ),
) -> None:
    """Re-dispatch the original owner agent against any actionable open PR.

    Replaces the old fix-Decision loop: no new Decisions are filed.
    After ``flow_control.max_retries_per_pr`` retries, files ONE Question
    Record per PR and stops dispatching that PR until the operator
    answers.
    """
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.questions.store_factory import make_question_store
    from minions.scheduled.pr_owner_sweep import run_pr_owner_sweep

    api_key: str | None = None
    if not dry_run:
        try:
            api_key = get_anthropic_api_key()
        except SecretNotFound as e:
            rprint(f"[red]Missing Anthropic API key:[/red] {e}")
            raise typer.Exit(1) from e

    report = run_pr_owner_sweep(
        projects_dir=PROJECTS_DIR,
        store=_store(),
        engineer_runs_store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
        questions_store=make_question_store(QUESTIONS_PATH),
        open_github_client=_open_github_client,
        notifier=_notifier(),
        api_key=api_key,
        dry_run=dry_run,
        cost_log_path=COST_LOG_PATH,
        max_dispatches_per_sweep=max_dispatches,
    )
    rprint(
        f"\n[bold]PR owner sweep[/bold] — retried {report.retried}, "
        f"escalated {report.escalated}, errored {report.errored}"
    )
    for o in report.outcomes:
        icon = {
            "retried": "[green]↻[/green]",
            "healthy": "[green]✓[/green]",
            "escalated": "[red]⚠[/red]",
            "skipped": "[yellow]⊘[/yellow]",
            "throttled": "[yellow]⊘[/yellow]",
            "error": "[red]✗[/red]",
        }.get(o.status, "·")
        owner = o.owner_agent_id or "?"
        rprint(
            f"  {icon} {o.project} pr={o.pr_url} owner={owner} "
            f"attempt={o.attempt} status={o.status} ({o.reason or ''})"
        )


@cron_app.command("execute-approved")
def cron_execute_approved(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run skips LLM calls and does NOT open PRs.",
    ),
    max_runs: int = typer.Option(
        5, "--max-runs", help="Hard cap on engineer-crew runs per invocation."
    ),
    only_expedited: bool = typer.Option(
        False,
        "--only-expedited",
        help="Fast lane: process only expedited approved Decisions (skip backlog). "
        "Use for out-of-cadence triggers (workflow_dispatch / manual) when a "
        "CTO investigation or PR-fix can't wait for the 6-hour cron.",
    ),
) -> None:
    """Run the engineer crew on approved Decisions, priority-ordered, capped.

    Ordering: p1 → p2 → p3, expedited-first within each tier, then FIFO.
    """
    from minions.agents.memory_store_factory import make_agent_memory_store
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.scheduled import run_execute_approved
    from minions.tasks.store_factory import make_task_store

    api_key: str | None = None
    if not dry_run:
        try:
            api_key = get_anthropic_api_key()
        except SecretNotFound as e:
            rprint(f"[red]Missing Anthropic API key:[/red] {e}")
            raise typer.Exit(1) from e
        ok, message = anthropic_check.auth_check(api_key)
        if not ok:
            rprint(f"[red]Anthropic preflight failed:[/red] {message}")
            raise typer.Exit(1)

    from minions.dossiers.store_factory import make_dossier_store

    report = run_execute_approved(
        projects_dir=PROJECTS_DIR,
        store=_store(),
        engineer_runs_store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
        open_github_client=_open_github_client,
        api_key=api_key,
        dry_run=dry_run,
        cost_log_path=COST_LOG_PATH,
        max_runs=max_runs,
        only_expedited=only_expedited,
        task_store=make_task_store(TASKS_PATH),
        memory_store=make_agent_memory_store(AGENT_MEMORY_PATH),
        dossier_store=make_dossier_store(DOSSIER_DRAFTS_PATH),
    )

    rprint(
        f"\n[bold]Execute-approved sweep[/bold] — "
        f"executed {report.executed}, skipped {report.skipped}, "
        f"throttled {report.throttled}, errored {report.errored}"
        + ("  [yellow](capped)[/yellow]" if report.capped else "")
    )
    for o in report.outcomes:
        tag = {
            "executed": "[green]✓[/green]",
            "skipped": "[dim]⊘[/dim]",
            "throttled": "[yellow]⊘[/yellow]",
            "error": "[red]✗[/red]",
        }[o.status]
        line = f"  {tag} {o.project} ({o.decision_id[:8]})"
        if o.pr_url:
            line += f" → {o.pr_url}"
        elif o.reason:
            line += f" — {o.reason[:80]}"
        rprint(line)


@cron_app.command("agent-memory-demote")
def cron_agent_memory_demote() -> None:
    """Demote old hot agent memories to cold storage."""
    from minions.agents.memory_store_factory import make_agent_memory_store
    from minions.scheduled import run_agent_memory_demote
    from minions.sprints.store_factory import make_sprint_counter_store

    report = run_agent_memory_demote(
        memory_store=make_agent_memory_store(AGENT_MEMORY_PATH),
        sprint_counter_store=make_sprint_counter_store(SPRINTS_PATH),
    )
    rprint(
        f"\n[bold]Agent memory demotion[/bold] — demoted {report.demoted} "
        f"record(s) across {report.projects_seen} project(s)"
    )


@questions_app.command("list")
def list_questions(
    status: str = typer.Option(
        "open", "--status", help="open / answered / escalated / cancelled / all"
    ),
) -> None:
    """List Question Records by status."""
    from minions.models.question import QuestionStatus
    from minions.questions import make_question_store

    store = make_question_store(QUESTIONS_PATH)
    qs = (
        store.list_all()
        if status.lower() == "all"
        else store.list_by_status(QuestionStatus(status.lower()))
    )
    if not qs:
        rprint(f"[dim]no questions matching status={status}[/dim]")
        return

    table = Table(title=f"Questions ({status})")
    table.add_column("ID")
    table.add_column("Project")
    table.add_column("Asker")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Question")
    for q in qs:
        table.add_row(
            str(q.id)[:8] + "…",
            q.project,
            q.asker_role,
            q.target_role,
            q.status.value,
            q.question[:60],
        )
    console.print(table)


@questions_app.command("show")
def show_question(question_id: str = typer.Argument(...)) -> None:
    """Show full details for a Question Record (id prefix is OK)."""
    from minions.questions import make_question_store

    store = make_question_store(QUESTIONS_PATH)
    matches = [q for q in store.list_all() if str(q.id).startswith(question_id)]
    if not matches:
        rprint(f"[red]no question matching prefix {question_id!r}[/red]")
        raise typer.Exit(1)
    q = matches[0]
    rprint(f"[bold]ID:[/bold]      {q.id}")
    rprint(f"[bold]Project:[/bold] {q.project}")
    rprint(f"[bold]Status:[/bold]  {q.status.value}")
    rprint(f"[bold]Asker:[/bold]   {q.asker_role} ({q.asker_agent_id})")
    rprint(f"[bold]Target:[/bold]  {q.target_role}")
    rprint(f"\n[bold]Question:[/bold]\n{q.question}")
    if q.context:
        rprint(f"\n[bold]Context:[/bold]\n{q.context}")
    if q.related_pr_url:
        rprint(f"\n[bold]Related PR:[/bold] {q.related_pr_url}")
    if q.answer:
        rprint(f"\n[bold]Answer ({q.answered_by}):[/bold]\n{q.answer}")
    if q.escalated_at:
        rprint(
            f"\n[yellow]Escalated[/yellow] at {q.escalated_at}: {q.escalation_reason or '(no reason given)'}"
        )


@questions_app.command("answer")
def answer_question_cmd(
    question_id: str = typer.Argument(..., help="Question id prefix"),
    answer: str = typer.Option(..., "--answer", "-a", help="The answer text"),
    by: str = typer.Option("operator", "--by", help="Who is answering (role or 'operator')"),
) -> None:
    """Answer an OPEN question."""
    from minions.questions import answer_question, make_question_store

    store = make_question_store(QUESTIONS_PATH)
    matches = [q for q in store.list_all() if str(q.id).startswith(question_id)]
    if not matches:
        rprint(f"[red]no question matching prefix {question_id!r}[/red]")
        raise typer.Exit(1)
    answered = answer_question(matches[0].id, store=store, answer=answer, answered_by=by)
    rprint(f"[green]✓ answered[/green] {answered.id} by {by}")


@questions_app.command("escalate")
def escalate_question_cmd(
    question_id: str = typer.Argument(..., help="Question id prefix"),
    reason: str = typer.Option(None, "--reason", help="Why this is bumping to the operator"),
) -> None:
    """Escalate a question to the operator (notifies via the configured notifier)."""
    from minions.questions import escalate_question, make_question_store

    store = make_question_store(QUESTIONS_PATH)
    matches = [q for q in store.list_all() if str(q.id).startswith(question_id)]
    if not matches:
        rprint(f"[red]no question matching prefix {question_id!r}[/red]")
        raise typer.Exit(1)
    escalated = escalate_question(matches[0].id, store=store, notifier=_notifier(), reason=reason)
    rprint(f"[yellow]↑ escalated[/yellow] {escalated.id} to operator")


@app.command("ask-pm")
def ask_pm(
    project: str = typer.Argument(..., help="Project name"),
    question: str = typer.Argument(..., help="Question for the Product Manager"),
) -> None:
    """Ask the project's Product Manager spokesperson a grounded question."""
    from minions.agile import answer_pm_question
    from minions.agile.store_factory import make_agile_store
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store

    manifests = load_active_manifests(PROJECTS_DIR)
    manifest = _resolve_project(project, manifests)
    record = answer_pm_question(
        manifest=manifest,
        question=question,
        decision_store=_store(),
        engineer_runs_store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
        agile_store=make_agile_store(AGILE_PATH),
    )
    rprint(f"\n[bold]{manifest.name} PM[/bold] answered ({str(record.id)[:8]})")
    rprint(record.answer)
    if record.citations:
        rprint("\n[dim]Citations: " + ", ".join(record.citations[:6]) + "[/dim]")
    if record.escalated_to:
        rprint(f"[yellow]Escalated action owner:[/yellow] {record.escalated_to}")


sprints_app = typer.Typer(
    no_args_is_help=True, help="Per-project sprint counter inspection + backfill."
)
app.add_typer(sprints_app, name="sprints")
tasks_app = typer.Typer(no_args_is_help=True, help="Inspect refined sprint Tasks.")
app.add_typer(tasks_app, name="tasks")
agents_app = typer.Typer(no_args_is_help=True, help="Agent naming registry.")
app.add_typer(agents_app, name="agents")


@tasks_app.command("list")
def tasks_list(
    project: str | None = typer.Option(None, "--project", "-p"),
    sprint: int | None = typer.Option(None, "--sprint", "-s"),
    owner: str | None = typer.Option(
        None, "--owner", help="Filter by owner agent_id, e.g. engineer@Demo"
    ),
    status: str | None = typer.Option(
        None, "--status", help="queued|in_progress|review|done|blocked|cancelled"
    ),
) -> None:
    """Show Tasks with optional filters."""
    from minions.tasks.store_factory import make_task_store

    store = make_task_store(TASKS_PATH)
    if owner is not None:
        rows = store.list_by_owner(owner)
    elif project is not None:
        rows = store.list_by_project(project, sprint_number=sprint)
    else:
        rows = store.list_all()
    if status is not None:
        rows = [t for t in rows if t.status == status]
    if not rows:
        rprint("[dim]No tasks match.[/dim]")
        return
    table = Table(title="Tasks")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Project")
    table.add_column("Sprint", justify="right")
    table.add_column("Cat")
    table.add_column("Title")
    table.add_column("Owner")
    table.add_column("Status", style="bold")
    table.add_column("Eff")
    for t in rows:
        owner_label = t.owner_display_name or t.owner_agent_id
        table.add_row(
            str(t.id)[:8] + "…",
            t.project,
            str(t.sprint_number) if t.sprint_number is not None else "—",
            t.category,
            t.title[:48],
            owner_label,
            t.status,
            t.estimated_effort,
        )
    console.print(table)


@tasks_app.command("show")
def tasks_show(task_id: str = typer.Argument(...)) -> None:
    """Full detail for a Task (id prefix is OK)."""
    from minions.tasks.store_factory import make_task_store

    store = make_task_store(TASKS_PATH)
    rows = [t for t in store.list_all() if str(t.id).startswith(task_id)]
    if not rows:
        rprint(f"[red]no task with id prefix '{task_id}'[/red]")
        raise typer.Exit(1)
    t = rows[0]
    rprint(f"[bold]ID:[/bold]        {t.id}")
    rprint(f"[bold]Project:[/bold]   {t.project}  ·  Sprint {t.sprint_number}")
    rprint(
        f"[bold]Category:[/bold]  {t.category}  ·  effort {t.estimated_effort}  ·  status {t.status}"
    )
    rprint(f"[bold]Owner:[/bold]     {t.owner_display_name or '—'}  ({t.owner_agent_id})")
    rprint(f"[bold]Decision:[/bold]  {t.decision_id}")
    rprint(f"\n[bold]Title:[/bold]\n{t.title}")
    rprint(f"\n[bold]Description:[/bold]\n{t.description}")
    if t.acceptance_criteria:
        rprint(f"\n[bold]Acceptance:[/bold]\n{t.acceptance_criteria}")
    if t.pr_url:
        rprint(f"\n[bold]PR:[/bold] {t.pr_url}")


@agents_app.command("list")
def agents_list() -> None:
    """List every agent and their display name."""
    from minions.agents.naming import list_all

    names = list_all()
    if not names:
        rprint("[dim]No names registered. See config/agent_names.yaml.[/dim]")
        return
    table = Table(title="Agent names")
    table.add_column("Agent ID", style="cyan")
    table.add_column("Display name")
    for agent_id in sorted(names):
        table.add_row(agent_id, names[agent_id])
    console.print(table)


@agents_app.command("name")
def agents_name(
    agent_id: str = typer.Argument(..., help="e.g. engineer@Demo"),
    display_name: str = typer.Argument(..., help='e.g. "Sasha"'),
) -> None:
    """Rename an agent. Already-stamped Decisions / Tasks keep their old name."""
    from minions.agents.naming import set_display_name

    try:
        set_display_name(agent_id, display_name)
    except ValueError as e:
        rprint(f"[red]{e}[/red]")
        raise typer.Exit(1) from e
    rprint(f'[green]✓[/green] {agent_id} → "{display_name}"')


@sprints_app.command("status")
def sprints_status(
    project: str | None = typer.Argument(None, help="Project name (omit for all)."),
) -> None:
    """Show the current sprint number per project."""
    from minions.sprints.store_factory import make_sprint_counter_store

    counter = make_sprint_counter_store(SPRINTS_PATH)
    rows = counter.list_all()
    if project is not None:
        rows = [r for r in rows if r.project.lower() == project.lower()]
    if not rows:
        rprint(f"[dim]No sprint counters yet{' for ' + project if project else ''}.[/dim]")
        return
    table = Table(title="Sprint counters")
    table.add_column("Project", style="cyan")
    table.add_column("Current sprint", justify="right")
    table.add_column("Updated")
    for r in rows:
        table.add_row(r.project, str(r.current_sprint_number), r.updated_at.isoformat())
    console.print(table)


@sprints_app.command("backfill")
def sprints_backfill(
    project: str = typer.Argument(..., help="Project name to backfill."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Stamp sprint numbers onto existing Decisions chronologically.

    Walks every Sprint Proposal Decision for the project in created_at order
    and assigns Sprint 0, 1, 2, … Also resets the counter to match the
    highest assigned number. Skips Decisions that already have a
    ``sprint_number`` set.
    """
    from minions.sprints.store_factory import make_sprint_counter_store

    store = _store()
    all_decisions = store.list_all()
    candidates = [
        d
        for d in all_decisions
        if d.project.lower() == project.lower()
        and d.type.value == "feature"
        and "sprint proposal" in (d.summary or "").lower()
        and "[dry run]" not in (d.summary or "").lower()
    ]
    candidates.sort(key=lambda d: d.created_at)
    pending = [d for d in candidates if d.sprint_number is None]
    if not pending:
        rprint(f"[dim]No unnumbered Sprint Proposals to backfill for {project}.[/dim]")
        return

    last_assigned = max(
        (d.sprint_number for d in candidates if d.sprint_number is not None),
        default=-1,
    )
    start = last_assigned + 1
    rprint(f"[bold]{len(pending)} Decision(s) to stamp[/bold] starting at Sprint {start}:")
    for i, d in enumerate(pending):
        rprint(f"  · {str(d.id)[:8]} → Sprint {start + i} ({d.summary[:60]})")
    if not yes and not typer.confirm("\nProceed?", default=False):
        rprint("[yellow]Aborted.[/yellow]")
        raise typer.Exit(1)

    for i, d in enumerate(pending):
        d.sprint_number = start + i
        store.save(d)
    # Resync the counter so the next planning cycle continues from here.
    counter = make_sprint_counter_store(SPRINTS_PATH)
    final_number = start + len(pending) - 1
    # Bump until the counter matches; idempotent enough for one-off use.
    while (counter.current(project) or -1) < final_number:
        counter.bump(project)
    rprint(
        f"[green]✓ stamped {len(pending)} Decision(s); counter now at {counter.current(project)}.[/green]"
    )


@app.command("spokesperson-backfill")
def spokesperson_backfill(
    decision_id: str = typer.Argument(..., help="SPIKE Decision id or prefix"),
) -> None:
    """Retroactively relay an answer for a SPIKE that already executed.

    For SPIKEs whose engineer crew already opened a PR but whose answer
    never made it back into the Leadership Room thread (e.g., the relay
    hook landed after the SPIKE was processed). Reconstructs the answer
    from the persisted engineer run + Decision payload and posts it as a
    spokesperson message in the original thread.
    """
    from minions.crews.engineer import EngineerResult
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.spokesperson.interview_relay import relay_spike_answer

    decision = _find_by_prefix(decision_id)
    if decision is None:
        raise typer.Exit(1)

    runs = make_engineer_runs_store(ENGINEER_RUNS_PATH)
    record = runs.get(str(decision.id))
    if record is None:
        rprint(f"[red]No engineer run found for {str(decision.id)[:8]}.[/red]")
        raise typer.Exit(1)

    # Synthesize an EngineerResult from the persisted record so the relay
    # helper sees the same shape it would in a live run.
    result = EngineerResult(
        decision_id=record.decision_id,
        pr_url=record.pr_url,
        pr_number=record.pr_number,
        branch_name=record.branch_name,
        files_changed=list(record.files_changed),
        files_rejected=list(record.files_rejected),
        operator_comment_posted=record.operator_comment_posted,
        skipped=record.skipped,
        skip_reason=record.skip_reason,
        dry_run=record.dry_run,
    )

    message_id = relay_spike_answer(
        decision_id=str(decision.id),
        project=decision.project,
        engineer_result=result,
    )
    if message_id is None:
        rprint(
            "[yellow]Relay returned None.[/yellow] Likely cause: this Decision is "
            "not a spokesperson SPIKE, the payload has no thread_id, or the DB is "
            "unreachable. Check logs for the specific reason."
        )
        raise typer.Exit(1)
    rprint(
        f"[green]✓ relayed[/green] message {message_id[:8]} "
        f"into the Leadership Room thread for decision {str(decision.id)[:8]}."
    )


@app.command("ask")
def ask_executive(
    role: str = typer.Argument(
        ...,
        help="Executive role to ask: cto / md / ceo / portfolio_owner / "
        "security_champion / product_manager. Aliases accepted.",
    ),
    question: str = typer.Argument(..., help="What you want to ask."),
    project: str | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Scope to one project. Omit for portfolio-level questions.",
    ),
    thread_id: str | None = typer.Option(
        None,
        "--thread",
        "-t",
        help="Continue an existing thread (multi-turn conversation).",
    ),
) -> None:
    """Ask a specific executive a question — multi-turn thread supported.

    Routes through the spokesperson framework: the named role gets the
    question, may consult other roles for grounding, returns a
    structured answer with citations. Continue the thread by passing
    ``--thread <id>`` from a previous answer.

    Examples:
      minions ask cto "Should we migrate demo_five to next-forge?"
      minions ask md "What's our cost trajectory this month?" --project demo_five
      minions ask cto "Follow-up on that" --thread <prior-thread-id>
    """
    from minions.agile.store_factory import make_agile_store
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.spokesperson.routing import SPOKESPERSON_ROLES, normalize_role
    from minions.spokesperson.service import ask_spokesperson
    from minions.spokesperson.store_factory import make_interview_store

    normalized = normalize_role(role)
    if normalized not in SPOKESPERSON_ROLES:
        rprint(f"[red]Unknown role:[/red] {role!r}. Allowed: {', '.join(SPOKESPERSON_ROLES)}.")
        raise typer.Exit(1)

    manifests = load_active_manifests(PROJECTS_DIR)
    if project is not None:
        _resolve_project(project, manifests)

    result = ask_spokesperson(
        spokesperson_role=normalized,
        question=question,
        project=project,
        thread_id=thread_id,
        interview_store=make_interview_store(INTERVIEWS_PATH),
        decision_store=_store(),
        engineer_runs_store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
        agile_store=make_agile_store(AGILE_PATH),
        manifests=manifests,
        activity_log_path=ACTIVITY_LOG_PATH,
        cost_log_path=COST_LOG_PATH,
    )
    answer = result.answer_message
    rprint(
        f"\n[bold cyan]{answer.agent_role}[/bold cyan] "
        f"[dim](msg {str(answer.id)[:8]}, confidence={answer.confidence})[/dim]\n"
    )
    rprint(answer.content)
    if answer.consulted_roles:
        rprint("\n[dim]Consulted: " + ", ".join(answer.consulted_roles) + "[/dim]")
    if answer.citations:
        labels = [c.label for c in answer.citations[:8]]
        rprint("[dim]Citations: " + ", ".join(labels) + "[/dim]")
    if result.task:
        rprint(f"\n[yellow]Follow-up proposal:[/yellow] {result.task.title}")
    rprint(
        f'\n[dim]Continue this thread: `minions ask {role} "..." --thread {result.thread.id}`[/dim]'
    )


@app.command("ask-spokesperson")
def ask_spokesperson_cmd(
    question: str = typer.Argument(..., help="Question for the spokesperson"),
    role: str = typer.Option("cto", "--role", "-r", help="Spokesperson role"),
    project: str | None = typer.Option(None, "--project", "-p", help="Project name"),
    thread_id: str | None = typer.Option(None, "--thread-id", help="Continue an existing thread"),
) -> None:
    """Ask a selected spokesperson and persist the interview trace."""
    from minions.agile.store_factory import make_agile_store
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.spokesperson.service import ask_spokesperson
    from minions.spokesperson.store_factory import make_interview_store

    manifests = load_active_manifests(PROJECTS_DIR)
    if project is not None:
        _resolve_project(project, manifests)
    result = ask_spokesperson(
        spokesperson_role=role,
        question=question,
        project=project,
        thread_id=thread_id,
        interview_store=make_interview_store(INTERVIEWS_PATH),
        decision_store=_store(),
        engineer_runs_store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
        agile_store=make_agile_store(AGILE_PATH),
        manifests=manifests,
        activity_log_path=ACTIVITY_LOG_PATH,
        cost_log_path=COST_LOG_PATH,
    )
    answer = result.answer_message
    rprint(
        f"\n[bold]{answer.agent_role}[/bold] answered "
        f"({str(answer.id)[:8]}, confidence={answer.confidence})"
    )
    rprint(answer.content)
    if answer.consulted_roles:
        rprint("\n[dim]Consulted: " + ", ".join(answer.consulted_roles) + "[/dim]")
    if answer.citations:
        labels = [c.label for c in answer.citations[:8]]
        rprint("[dim]Citations: " + ", ".join(labels) + "[/dim]")
    if result.task:
        rprint(f"[yellow]Follow-up proposal:[/yellow] {result.task.title}")


@cron_app.command("refine-approved")
def cron_refine_approved() -> None:
    """Break newly-approved Sprint Proposals into Tasks (Phase 3 of sprint-tasks-memory).

    Idempotent — a Decision that already has Tasks is left alone. No LLM call
    in the common path (suggested_owner_role + naming registry handles routing).
    """
    from minions.scheduled.refine_approved import run_refine_approved
    from minions.tasks.store_factory import make_task_store

    report = run_refine_approved(
        store=_store(),
        task_store=make_task_store(TASKS_PATH),
    )
    rprint(
        f"\n[bold]Refine sweep[/bold] — "
        f"refined {report.refined}, skipped {report.skipped}, errored {report.errored}"
    )
    for o in report.outcomes:
        tag = {
            "refined": "[green]✓[/green]",
            "skipped": "[dim]·[/dim]",
            "error": "[red]✗[/red]",
        }[o.status]
        line = f"  {tag} {o.project} ({o.decision_id[:8]})"
        if o.task_count:
            line += f" → {o.task_count} task(s)"
        if o.reason:
            line += f" — {o.reason}"
        rprint(line)


@cron_app.command("assign-backlog-tasks")
def cron_assign_backlog_tasks() -> None:
    """Sweep ``unassigned`` Tasks and assign them when an agent's WIP drops.

    Cheap — no-op when there's no backlog. See openspec/changes/
    enriched-sprint-planning Phase D for the design.
    """
    from minions.scheduled import run_assign_backlog_tasks
    from minions.tasks.store_factory import make_task_store

    report = run_assign_backlog_tasks(task_store=make_task_store(TASKS_PATH))
    rprint(
        f"\n[bold]Backlog assignment[/bold] — "
        f"assigned {report.assigned}, kept_unassigned {report.kept_unassigned}"
    )
    for o in report.outcomes:
        tag = "[green]✓[/green]" if o.status == "assigned" else "[dim]·[/dim]"
        line = f"  {tag} {o.project} ({o.task_id[:8]})"
        if o.new_owner_agent_id:
            line += f" → {o.new_owner_agent_id}"
        rprint(line)


@cron_app.command("branch-sweep")
def cron_branch_sweep(
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--no-dry-run",
        help="Dry run reports 'would_delete' without touching branches.",
    ),
    min_age_minutes: int = typer.Option(
        30,
        "--min-age-minutes",
        help="Skip branches whose tip commit is younger than this (protects "
        "active runs that have not opened their PR yet).",
    ),
) -> None:
    """Garbage-collect stranded ``minions/eng/*`` branches.

    Safety guards: deletes only branches whose every commit carries the
    ``Minions-Run-Id`` trailer AND whose run_id is known to the engineer-runs
    store AND have no open PR AND are older than ``--min-age-minutes``. Any
    branch touched by the operator (commit without trailer) is kept forever.
    """
    from minions.scheduled.branch_sweep import run_branch_sweep

    report = run_branch_sweep(
        projects_dir=PROJECTS_DIR,
        open_github_client=_open_github_client,
        dry_run=dry_run,
        min_age_minutes=min_age_minutes,
    )

    suffix = " (dry-run)" if dry_run else ""
    rprint(
        f"\n[bold]Branch sweep{suffix}[/bold] — "
        f"deleted {report.deleted}, would_delete {report.would_delete}, "
        f"kept {report.kept}, errored {report.errored}"
    )
    for o in report.outcomes:
        tag = {
            "deleted": "[red]✗ deleted[/red]",
            "would_delete": "[yellow]⚠ would delete[/yellow]",
            "kept_no_trailer": "[dim]· kept (no trailer)[/dim]",
            "kept_unknown_run_id": "[dim]· kept (unknown run)[/dim]",
            "kept_too_young": "[dim]· kept (too young)[/dim]",
            "kept_open_pr": "[dim]· kept (open PR)[/dim]",
            "kept_outside_namespace": "[dim]· kept (outside namespace)[/dim]",
            "error": "[red]✗ error[/red]",
        }.get(o.status, o.status)
        line = f"  {tag} {o.repo}  {o.branch}"
        if o.reason:
            line += f" — {o.reason[:80]}"
        rprint(line)


@cron_app.command("pr-followup")
def cron_pr_followup(
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--no-dry-run",
        help="Dry run skips submitting fix Decisions and posting PR comments.",
    ),
    max_attempts: int = typer.Option(
        3, "--max-attempts", help="Cap on auto-fix attempts per original PR."
    ),
) -> None:
    """Watch minions PRs; on CI failure, queue a fix Decision automatically."""
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.scheduled import run_pr_followup

    api_key: str | None = None
    try:
        api_key = get_anthropic_api_key()
    except SecretNotFound:
        api_key = None  # QA review will skip; CI-failure handling still works

    report = run_pr_followup(
        projects_dir=PROJECTS_DIR,
        store=_store(),
        engineer_runs_store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
        notifier=_notifier(),
        open_github_client=_open_github_client,
        max_attempts=max_attempts,
        dry_run=dry_run,
        api_key=api_key,
    )

    rprint(
        f"\n[bold]PR follow-up sweep[/bold] — "
        f"queued_fixes {report.queued_fixes}, errored {report.errored}, "
        f"total_checked {len(report.outcomes)}"
    )
    for o in report.outcomes:
        tag = {
            "ok": "[green]✓[/green]",
            "queued_fix": "[yellow]↻[/yellow]",
            "skipped": "[dim]⊘[/dim]",
            "error": "[red]✗[/red]",
        }[o.status]
        line = f"  {tag} {o.project} — {o.pr_url or o.decision_id[:8]}"
        if o.ci_conclusion:
            line += f" [ci={o.ci_conclusion}]"
        if o.fix_decision_id:
            line += f" → fix decision {o.fix_decision_id[:8]}"
        if o.reason:
            line += f" — {o.reason[:80]}"
        rprint(line)


@cron_app.command("pr-review-loop")
def cron_pr_review_loop(
    dry_run: bool = typer.Option(
        False,
        "--dry-run/--no-dry-run",
        help="Dry run skips PR comments and engineer-run updates.",
    ),
) -> None:
    """Assign internal reviewers and post structured PR review comments."""
    from contextlib import suppress

    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.questions.store_factory import make_question_store
    from minions.scheduled import run_pr_review_loop

    # When the Anthropic key is available, reviewers run LLM-driven and
    # actually read the diff + prior comments. Without it, the legacy
    # deterministic stub kicks in — kept so dry-runs and credentialless
    # cron firings still complete without crashing.
    api_key: str | None = None
    with suppress(SecretNotFound):
        api_key = get_anthropic_api_key()

    report = run_pr_review_loop(
        projects_dir=PROJECTS_DIR,
        store=_store(),
        engineer_runs_store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
        open_github_client=_open_github_client,
        dry_run=dry_run,
        api_key=api_key,
        questions_store=make_question_store(QUESTIONS_PATH),
    )

    rprint(
        f"\n[bold]PR review-loop sweep[/bold] — "
        f"assigned {report.assigned}, reviewed {report.reviewed}, "
        f"creator_responded {report.creator_responded}, merged {report.merged}, "
        f"handoff {report.handoff}, errored {report.errored}, "
        f"total_checked {len(report.outcomes)}"
    )
    for o in report.outcomes:
        tag = {
            "assigned": "[cyan]+[/cyan]",
            "reviewed": "[green]✓[/green]",
            "creator_responded": "[yellow]↻[/yellow]",
            "conflict_queued": "[yellow]⚠[/yellow]",
            "superseded": "[cyan]↦[/cyan]",
            "merged": "[green]✓[/green]",
            "handoff": "[yellow]→[/yellow]",
            "skipped": "[dim]⊘[/dim]",
            "error": "[red]✗[/red]",
        }[o.status]
        line = f"  {tag} {o.project} — {o.pr_url or o.decision_id[:8]}"
        if o.review_status:
            line += f" [{o.review_status}]"
        if o.comments_posted:
            line += f" comments={o.comments_posted}"
        if o.fix_decision_id:
            line += f" fix={o.fix_decision_id[:8]}"
        if o.assigned_reviewers:
            line += " reviewers=" + ",".join(o.assigned_reviewers)
        if o.reason:
            line += f" — {o.reason[:80]}"
        rprint(line)


@cost_app.command("weekly")
def cost_weekly() -> None:
    """Show this-week-to-date LLM cost per project."""
    from minions.cost import week_to_date_cost

    manifests = load_active_manifests(PROJECTS_DIR)
    table = Table(title="Cost — week to date (UTC)")
    table.add_column("Project")
    table.add_column("Cost (USD)", justify="right")
    table.add_column("Weekly cap", justify="right")
    table.add_column("% of cap", justify="right")
    total = 0.0
    for name, m in sorted(manifests.items()):
        wtd = week_to_date_cost(name, path=COST_LOG_PATH)
        total += wtd
        pct = (wtd / m.weekly_budget_usd * 100) if m.weekly_budget_usd else 0
        color = "red" if pct >= 100 else ("yellow" if pct >= 80 else "green")
        table.add_row(
            name,
            f"${wtd:.4f}",
            f"${m.weekly_budget_usd:.2f}",
            f"[{color}]{pct:.1f}%[/{color}]",
        )
    table.add_row("[bold]TOTAL[/bold]", f"[bold]${total:.4f}[/bold]", "", "")
    console.print(table)


@cost_app.command("monthly")
def cost_monthly() -> None:
    """Show this-month-to-date LLM cost per project."""
    from minions.cost import month_to_date_cost

    manifests = load_active_manifests(PROJECTS_DIR)
    table = Table(title="Cost — month to date (UTC)")
    table.add_column("Project")
    table.add_column("Cost (USD)", justify="right")
    table.add_column("Monthly cap", justify="right")
    table.add_column("% of cap", justify="right")
    total = 0.0
    for name, m in sorted(manifests.items()):
        mtd = month_to_date_cost(name, path=COST_LOG_PATH)
        total += mtd
        pct = (mtd / m.monthly_budget_usd * 100) if m.monthly_budget_usd else 0
        color = "red" if pct >= 100 else ("yellow" if pct >= 80 else "green")
        table.add_row(
            name,
            f"${mtd:.4f}",
            f"${m.monthly_budget_usd:.2f}",
            f"[{color}]{pct:.1f}%[/{color}]",
        )
    table.add_row("[bold]TOTAL[/bold]", f"[bold]${total:.4f}[/bold]", "", "")
    console.print(table)


@app.command()
def sync(
    project: str = typer.Option(
        None, "--project", "-p", help="Limit to one project. Default: all."
    ),
) -> None:
    """Sync open PR state from GitHub → engineer runs store. Updates the sprint board."""
    from minions.crews.engineer_runs_store_factory import make_engineer_runs_store
    from minions.sync import sync_pr_status

    manifests = load_active_manifests(PROJECTS_DIR)
    if project is not None:
        manifest = _resolve_project(project, manifests)
        manifests = {manifest.name: manifest}

    store = make_engineer_runs_store(ENGINEER_RUNS_PATH)
    report = sync_pr_status(
        store=store,
        open_github_client=_open_github_client,
        manifests=manifests,
        decision_store=_store(),
    )

    if not report.outcomes:
        rprint("[dim]no engineer runs with open PRs to sync[/dim]")
        return

    rprint(
        f"\n[bold]PR sync[/bold] — {report.changed} changed, "
        f"{report.merged} merged, {report.errors} errored\n"
    )
    for o in report.outcomes:
        if o.error:
            rprint(f"  [red]✗[/red] {o.project} · {o.decision_id[:8]} — {o.error}")
        elif o.changed:
            arrow = f"{o.before or 'unknown'} → [bold]{o.after}[/bold]"
            color = "green" if o.after == "merged" else "yellow"
            rprint(f"  [{color}]↻[/{color}] {o.project} · {o.decision_id[:8]} — {arrow}")
        else:
            rprint(f"  [dim]·[/dim] {o.project} · {o.decision_id[:8]} — unchanged ({o.after})")


@app.command()
def dashboard(
    port: int = typer.Option(8501, "--port", help="Port to bind Streamlit to."),
    headless: bool = typer.Option(
        False, "--headless/--no-headless", help="Run without opening a browser tab."
    ),
) -> None:
    """Launch the Streamlit operator dashboard (agents grid, decisions, sprint board)."""
    import subprocess
    import sys

    app_path = Path(__file__).parent / "dashboard" / "app.py"
    if not app_path.exists():
        rprint(f"[red]Dashboard app not found at {app_path}[/red]")
        raise typer.Exit(1)

    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app_path),
        "--server.port",
        str(port),
    ]
    if headless:
        cmd += ["--server.headless", "true"]

    rprint(f"[bold]Launching dashboard[/bold] on http://localhost:{port}")
    rprint("[dim]Press Ctrl-C to stop.[/dim]\n")
    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        pass


@audit_app.command("list")
def audit_list(
    severity: str = typer.Option("all", help="all|advisory|medium|high"),
    project: str = typer.Option(None, "--project", "-p"),
    open_only: bool = typer.Option(True, "--open/--all-statuses"),
) -> None:
    """List audit findings (default: open only)."""
    from minions.audit.store_factory import make_audit_findings_store

    store = make_audit_findings_store(AUDIT_FINDINGS_PATH)
    findings = store.list_open() if open_only else store.list_all()
    if severity != "all":
        findings = [f for f in findings if f.severity == severity]
    if project is not None:
        findings = [f for f in findings if f.source_project == project]
    if not findings:
        rprint("[dim]no findings match[/dim]")
        return
    table = Table(title=f"Audit findings ({len(findings)})")
    table.add_column("ID")
    table.add_column("When (UTC)")
    table.add_column("Project")
    table.add_column("Severity")
    table.add_column("Auditor")
    table.add_column("Summary")
    for f in sorted(findings, key=lambda x: x.created_at, reverse=True):
        sev_color = {"high": "red", "medium": "yellow", "advisory": "cyan"}.get(f.severity, "white")
        table.add_row(
            str(f.id)[:8],
            f.created_at.strftime("%m-%d %H:%M"),
            f.source_project or "—",
            f"[{sev_color}]{f.severity}[/{sev_color}]",
            f.auditor_role,
            f.summary[:60],
        )
    console.print(table)


@audit_app.command("show")
def audit_show(finding_id: str = typer.Argument(...)) -> None:
    """Show full detail of one finding."""
    from minions.audit.store_factory import make_audit_findings_store

    store = make_audit_findings_store(AUDIT_FINDINGS_PATH)
    matches = [f for f in store.list_all() if str(f.id).startswith(finding_id)]
    if not matches:
        rprint(f"[red]no finding with id prefix '{finding_id}'[/red]")
        raise typer.Exit(1)
    if len(matches) > 1:
        rprint("[red]ambiguous prefix — matches:[/red]")
        for m in matches:
            rprint(f"  {m.id}")
        raise typer.Exit(1)
    f = matches[0]
    sev_color = {"high": "red", "medium": "yellow", "advisory": "cyan"}.get(f.severity, "white")
    rprint(f"\n[bold]{f.summary}[/bold]")
    rprint(
        f"[dim]id:[/dim]      {f.id}\n"
        f"[dim]severity:[/dim] [{sev_color}]{f.severity}[/{sev_color}]\n"
        f"[dim]category:[/dim] {f.category.value}\n"
        f"[dim]project:[/dim]  {f.source_project or '—'}\n"
        f"[dim]decision:[/dim] {f.source_decision_id or '—'}\n"
        f"[dim]PR:[/dim]       {f.source_pr_url or '—'}\n"
        f"[dim]auditor:[/dim]  {f.auditor_role} ({f.auditor_agent_id})\n"
        f"[dim]status:[/dim]   {f.status.value}\n"
        f"[dim]created:[/dim]  {f.created_at}\n"
    )
    rprint("[bold]Evidence[/bold]")
    rprint(f.evidence)
    rprint("\n[bold]Recommendation[/bold]")
    rprint(f.recommendation)


@cost_app.command("tail")
def cost_tail(n: int = typer.Option(20, help="Show the last N entries.")) -> None:
    """Show the last N entries from the cost log."""
    from minions.cost import read_log

    entries = read_log(COST_LOG_PATH)
    if not entries:
        rprint("[dim]no cost entries yet — run a non-dry-run command first[/dim]")
        return
    table = Table(title=f"Cost log — last {min(n, len(entries))}")
    table.add_column("When (UTC)")
    table.add_column("Project")
    table.add_column("Role")
    table.add_column("Model")
    table.add_column("In", justify="right")
    table.add_column("Out", justify="right")
    table.add_column("USD", justify="right")
    for e in entries[-n:]:
        table.add_row(
            e.timestamp.strftime("%m-%d %H:%M:%S"),
            e.project or "[dim]—[/dim]",
            e.role or "[dim]—[/dim]",
            e.model.split("/")[-1][:24],
            str(e.input_tokens),
            str(e.output_tokens),
            f"${e.cost_usd:.4f}",
        )
    console.print(table)


@db_app.command("status")
def db_status() -> None:
    """Show whether a Postgres URL resolves and which migrations are applied."""
    from minions.db.connection import connect, get_database_url, has_database_url
    from minions.db.migrate import _migration_files, applied_migrations

    if not has_database_url():
        rprint("[yellow]No database URL configured.[/yellow]")
        rprint(
            "[dim]Set MINIONS_DATABASE_URL or DATABASE_URL, or store secret 'database-url'.[/dim]"
        )
        raise typer.Exit(1)

    url = get_database_url()
    # Hide password component when echoing.
    masked = url
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        creds, host = rest.split("@", 1)
        if ":" in creds:
            user = creds.split(":", 1)[0]
            masked = f"{scheme}://{user}:***@{host}"
    rprint(f"[bold]Database:[/bold] {masked}")

    try:
        with connect() as conn:
            applied = applied_migrations(conn)
    except Exception as e:  # noqa: BLE001
        rprint(f"[red]Connection failed:[/red] {e}")
        raise typer.Exit(1) from None

    rprint("\n[bold]Migrations:[/bold]")
    for filename, _ in _migration_files():
        mark = "[green]✓[/green]" if filename in applied else "[yellow]·[/yellow]"
        rprint(f"  {mark} {filename}")


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply any pending SQL migrations forward-only."""
    from minions.db.migrate import apply_migrations

    try:
        applied = apply_migrations()
    except Exception as e:  # noqa: BLE001
        rprint(f"[red]Migration failed:[/red] {e}")
        raise typer.Exit(1) from None

    if not applied:
        rprint("[dim]No migrations to apply — schema is up to date.[/dim]")
        return
    for name in applied:
        rprint(f"[green]✓[/green] applied {name}")


@db_app.command("backfill")
def db_backfill() -> None:
    """Copy decisions / audit findings / engineer runs from JSON into Postgres (idempotent)."""
    from minions.approval.store_postgres import PostgresDecisionStore
    from minions.audit.store import AuditFindingStore
    from minions.audit.store_postgres import PostgresAuditFindingStore
    from minions.crews.engineer_runs_store import EngineerRunStore
    from minions.crews.engineer_runs_store_postgres import PostgresEngineerRunStore

    moved = 0

    if DECISION_STORE_PATH.exists():
        src = DecisionStore(DECISION_STORE_PATH)
        dst = PostgresDecisionStore()
        items = src.list_all()
        for d in items:
            dst.save(d)
        if items:
            rprint(f"[green]✓[/green] decisions: {len(items)} rows")
            moved += len(items)

    if AUDIT_FINDINGS_PATH.exists():
        a_src = AuditFindingStore(AUDIT_FINDINGS_PATH)
        a_dst = PostgresAuditFindingStore()
        items_a = a_src.list_all()
        for f in items_a:
            a_dst.save(f)
        if items_a:
            rprint(f"[green]✓[/green] audit_findings: {len(items_a)} rows")
            moved += len(items_a)

    if ENGINEER_RUNS_PATH.exists():
        e_src = EngineerRunStore(ENGINEER_RUNS_PATH)
        e_dst = PostgresEngineerRunStore()
        items_e = e_src.list_all()
        for r in items_e:
            e_dst.update(r)
        if items_e:
            rprint(f"[green]✓[/green] engineer_runs: {len(items_e)} rows")
            moved += len(items_e)

    # Append-only ledgers.
    cost_path = REPO_ROOT / "data" / "local" / "cost_log.jsonl"
    if cost_path.exists():
        from minions import cost as cost_module

        # Force JSONL read by passing the path explicitly, then bulk-write to PG.
        entries = cost_module.read_log(cost_path)
        if entries:
            for entry in entries:
                cost_module._pg_append(entry)
            rprint(f"[green]✓[/green] cost_log: {len(entries)} rows")
            moved += len(entries)

    activity_path = REPO_ROOT / "data" / "local" / "activity.jsonl"
    if activity_path.exists():
        from minions import activity as activity_module

        events = activity_module.read_log(activity_path)
        if events:
            for ev in events:
                activity_module._pg_append(ev)
            rprint(f"[green]✓[/green] activity_log: {len(events)} events")
            moved += len(events)

    if moved == 0:
        rprint("[dim]Nothing to backfill — all JSON stores empty or absent.[/dim]")
    else:
        rprint(f"\n[bold]Total:[/bold] {moved} rows backfilled to Postgres.")


if __name__ == "__main__":
    app()
