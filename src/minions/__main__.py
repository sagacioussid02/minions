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
app.add_typer(decisions_app, name="decisions")
app.add_typer(secrets_app, name="secrets")
app.add_typer(github_app, name="github")
app.add_typer(cron_app, name="cron")
app.add_typer(cost_app, name="cost")
app.add_typer(audit_app, name="audit")
app.add_typer(db_app, name="db")
console = Console()


REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config" / "portfolio.yaml"
PROJECTS_DIR = REPO_ROOT / "projects"
DECISION_STORE_PATH = REPO_ROOT / "data" / "local" / "decisions.json"
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
init_cost_tracking(log_path=COST_LOG_PATH)

from minions.activity import set_log_path as _set_activity_log_path  # noqa: E402

_set_activity_log_path(ACTIVITY_LOG_PATH)


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
        gh = _open_github_client(manifest) if manifest.source.kind == "github" else None
        try:
            profile = build_profile(manifest, github_client=gh)
            rprint(
                f"[dim]Grounded with profile: {len(profile.languages)} langs, "
                f"{len(profile.package_files)} pkg files, "
                f"{profile.todo_count} TODOs, "
                f"{len(profile.open_issues)} open issues, "
                f"tasks.md remaining={profile.tasks_md.remaining if profile.tasks_md else 'n/a'}[/dim]"
            )
        except Exception as e:  # noqa: BLE001
            rprint(f"[yellow]Profile build failed, continuing ungrounded:[/yellow] {e}")
            profile = None

    decision = run_planning_crew(manifest, dry_run=dry_run, api_key=api_key, profile=profile)

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
    table.add_column("Status", style="bold")
    table.add_column("Summary")
    for d in records:
        table.add_row(
            str(d.id)[:8] + "…",
            d.project,
            d.type.value,
            d.risk,
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
    decision = _find_by_prefix(decision_id)
    if decision is None:
        raise typer.Exit(1)
    resolved = resolve(
        decision.id, store=_store(), notifier=_notifier(), action="reject", reason=reason
    )
    rprint(f"[red]✗ rejected[/red] {resolved.id}")


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
) -> None:
    """Manually trigger the Monday weekly planning sweep."""
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
