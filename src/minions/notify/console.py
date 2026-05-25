"""ConsoleNotifier — prints the email body to stdout. Used in v0 demos.

Swap with GmailNotifier once OAuth is wired up.
"""

from __future__ import annotations

from rich import print as rprint
from rich.panel import Panel

from minions.approval.tokens import sign
from minions.models.decision import Decision


class ConsoleNotifier:
    """Renders the would-be email to stdout instead of sending it.

    Intentionally implements the same surface as GmailNotifier so swapping
    is a one-line change.
    """

    def notify_approval_request(self, decision: Decision) -> None:
        approve_token = sign(decision.id, "approve")
        reject_token = sign(decision.id, "reject")

        critique = ""
        if decision.critique is not None:
            crit = decision.critique
            critique = (
                "\n[bold yellow]Devil's Advocate critique:[/bold yellow]\n"
                f"  Counter-argument: {crit.counter_argument}\n"
                f"  Failure modes: {', '.join(crit.failure_modes)}\n"
                + (f"  Alternative considered: {crit.alternative_considered}\n" if crit.alternative_considered else "")
            )

        proposer_label = decision.proposer_display_name or decision.proposer_agent_id
        body = (
            f"[bold]Subject:[/bold] [minions/{decision.project}/{decision.type.value}] "
            f"{decision.summary}\n\n"
            f"[bold]Project:[/bold]   {decision.project}\n"
            f"[bold]Type:[/bold]      {decision.type.value}\n"
            f"[bold]Risk:[/bold]      {decision.risk}\n"
            f"[bold]Proposer:[/bold]  {proposer_label} ({decision.proposer_role})\n"
            f"[bold]ID:[/bold]        {decision.id}\n\n"
            f"[bold]Rationale:[/bold]\n{decision.rationale}\n\n"
            f"[bold]Plan:[/bold]\n{decision.diff_or_plan or '(none)'}\n"
            f"{critique}\n"
            f"[green]Approve:[/green]  minions decisions approve {decision.id}\n"
            f"[red]Reject:[/red]   minions decisions reject  {decision.id}\n\n"
            f"[dim]In production, signed magic-link URLs replace the CLI commands above:[/dim]\n"
            f"[dim]  approve_token: {approve_token[:32]}...[/dim]\n"
            f"[dim]  reject_token:  {reject_token[:32]}...[/dim]"
        )

        rprint(Panel(body, title="Approval request (ConsoleNotifier)", border_style="cyan"))

    def notify_decision_resolved(self, decision: Decision) -> None:
        color = {"approved": "green", "rejected": "red"}.get(decision.status.value, "white")
        rprint(
            f"[{color}]Decision {decision.id} → {decision.status.value}[/{color}]"
            + (f" — {decision.resolved_reason}" if decision.resolved_reason else "")
        )

    def notify_text(self, *, subject: str, body: str) -> None:
        rprint(Panel(body, title=subject, border_style="cyan"))
