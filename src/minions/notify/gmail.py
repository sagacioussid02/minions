"""Gmail notifier — sends approval emails via Gmail SMTP using an App Password.

## Operator setup (5 min)

1. Enable 2-Step Verification on the Google account that owns ``portfolio.yaml.owner``.
2. Generate a Gmail App Password at https://myaccount.google.com/apppasswords
   (label it "minions").
3. Store the 16-char password in either:
     - ``MINIONS_SECRET_GMAIL_APP_PASSWORD`` env var (local dev), or
     - AWS Secrets Manager: ``minions/gmail-app-password`` (production)
4. Switch the notifier on: ``export MINIONS_NOTIFIER=gmail``
5. Verify: ``minions notify-test`` — sends a test email to the owner.

App Passwords work even when 2FA is on, and they don't require a GCP
project / OAuth consent screen / token refresh dance. We can swap in OAuth
later by replacing this class — the ``Notifier`` Protocol surface stays
identical.

Magic-link approval URLs are documented in the email body but not yet
clickable — they'd need a webhook receiver (Phase 1.7 runtime). For now the
email shows the exact CLI command to copy/paste.
"""

from __future__ import annotations

import logging
import os
import smtplib
from collections.abc import Callable
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from urllib.parse import urlencode

from minions.approval.tokens import sign
from minions.models.decision import Decision

logger = logging.getLogger(__name__)


class GmailNotifier:
    """Sends approval and resolution emails via Gmail SMTP."""

    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 465  # SSL

    def __init__(
        self,
        *,
        smtp_user: str,
        smtp_password: str,
        recipient: str | None = None,
        smtp_send: Callable[[MIMEMultipart], None] | None = None,
    ) -> None:
        """``smtp_send`` is a test-only injection point that bypasses real SMTP."""
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.recipient = recipient or smtp_user
        self._smtp_send = smtp_send

    def _recipient_for(self, decision: Decision) -> str:
        """Tenant decisions notify the real customer; founder decisions keep
        the fixed ``self.recipient``. Never blocks a send — falls back on any
        lookup failure (missing CLERK_SECRET_KEY, Clerk API error, etc.)."""
        if decision.tenant_id is not None:
            from minions.notify.clerk_users import get_tenant_email

            email = get_tenant_email(decision.tenant_id)
            if email:
                return email
        return self.recipient

    # ---- Notifier Protocol ----

    def notify_approval_request(self, decision: Decision) -> None:
        approve_token = sign(decision.id, "approve")
        reject_token = sign(decision.id, "reject")
        msg = self._compose_approval(decision, approve_token, reject_token)
        self._send(msg)

    def notify_decision_resolved(self, decision: Decision) -> None:
        msg = self._compose_resolution(decision)
        self._send(msg)

    def notify_text(self, *, subject: str, body: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["From"] = f"minions <{self.smtp_user}>"
        msg["To"] = self.recipient
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        self._send(msg)

    # ---- Internals ----

    def _send(self, msg: MIMEMultipart) -> None:
        if self._smtp_send is not None:
            self._smtp_send(msg)
            return
        try:
            with smtplib.SMTP_SSL(self.SMTP_HOST, self.SMTP_PORT) as server:
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError as e:
            raise RuntimeError(
                "Gmail SMTP auth failed — check that the App Password is correct and "
                "2FA is enabled on the account. Generate a new one at "
                "https://myaccount.google.com/apppasswords."
            ) from e

    def _compose_approval(
        self, decision: Decision, approve_token: str, reject_token: str
    ) -> MIMEMultipart:
        proposer = decision.proposer_display_name or decision.proposer_agent_id
        subject = f"[minions/{decision.project}/{decision.type.value}] {decision.summary[:80]}"

        text_body = _render_approval_text(decision, proposer, approve_token, reject_token)
        html_body = _render_approval_html(decision, proposer, approve_token, reject_token)

        msg = MIMEMultipart("alternative")
        msg["From"] = f"minions <{self.smtp_user}>"
        msg["To"] = self._recipient_for(decision)
        msg["Subject"] = subject
        msg.attach(MIMEText(text_body, "plain"))
        msg.attach(MIMEText(html_body, "html"))
        return msg

    def _compose_resolution(self, decision: Decision) -> MIMEMultipart:
        verdict = decision.status.value
        subject = (
            f"[minions/{decision.project}/{decision.type.value}] "
            f"{verdict.upper()}: {decision.summary[:60]}"
        )
        text = (
            f"Decision {decision.id} was {verdict}.\n"
            f"Reason: {decision.resolved_reason or '(none)'}\n"
            f"PR: {decision.pr_url or '(none)'}\n"
            f"Resolved at: {decision.resolved_at}\n"
        )
        msg = MIMEMultipart("alternative")
        msg["From"] = f"minions <{self.smtp_user}>"
        msg["To"] = self._recipient_for(decision)
        msg["Subject"] = subject
        msg.attach(MIMEText(text, "plain"))
        return msg


# ---- Body rendering (module-level so they're easy to unit-test) ----


def _webhook_base_url() -> str | None:
    """Resolve the deployed webhook base URL (e.g. ``https://minions-webhook.fly.dev``).

    Returns ``None`` when unset, in which case the email falls back to
    showing CLI commands + raw tokens (the pre-webhook behavior).
    """
    url = os.environ.get("MINIONS_WEBHOOK_BASE_URL", "").strip()
    return url.rstrip("/") or None


def _magic_link(base_url: str, action: str, token: str) -> str:
    return f"{base_url}/{action}?{urlencode({'token': token})}"


def _render_approval_text(
    decision: Decision, proposer: str, approve_token: str, reject_token: str
) -> str:
    lines = [
        "A new Decision is awaiting your approval.",
        "",
        f"Project:   {decision.project}",
        f"Type:      {decision.type.value}",
        f"Risk:      {decision.risk}",
        f"Proposer:  {proposer} ({decision.proposer_role})",
        f"ID:        {decision.id}",
        "",
        "## Rationale",
        decision.rationale,
        "",
        "## Plan",
        decision.diff_or_plan or "(none)",
        "",
    ]
    if decision.critique is not None:
        lines.extend(
            [
                "## Devil's Advocate critique",
                f"Counter-argument: {decision.critique.counter_argument}",
                f"Failure modes: {', '.join(decision.critique.failure_modes)}",
                *(
                    [f"Alternative considered: {decision.critique.alternative_considered}"]
                    if decision.critique.alternative_considered
                    else []
                ),
                "",
            ]
        )
    base_url = _webhook_base_url()
    if base_url:
        lines.extend(
            [
                "## Approve or reject (one click)",
                f"  approve: {_magic_link(base_url, 'approve', approve_token)}",
                f"  reject:  {_magic_link(base_url, 'reject', reject_token)}",
                "",
                "Or from the CLI:",
                f"  minions decisions approve {decision.id}",
                f"  minions decisions reject  {decision.id}",
            ]
        )
    else:
        lines.extend(
            [
                "## Approve or reject",
                f"  minions decisions approve {decision.id}",
                f"  minions decisions reject  {decision.id}",
                "",
                "Magic-link tokens (set MINIONS_WEBHOOK_BASE_URL to make these clickable):",
                f"  approve: {approve_token}",
                f"  reject:  {reject_token}",
            ]
        )
    return "\n".join(lines)


def _render_approval_html(
    decision: Decision, proposer: str, approve_token: str, reject_token: str
) -> str:
    def e(s: str | None) -> str:
        return escape(s or "")

    critique_html = ""
    if decision.critique is not None:
        crit = decision.critique
        alt = (
            f"<p><strong>Alternative considered:</strong> {e(crit.alternative_considered)}</p>"
            if crit.alternative_considered
            else ""
        )
        critique_html = (
            "<h3>Devil's Advocate critique</h3>"
            f"<p><strong>Counter-argument:</strong> {e(crit.counter_argument)}</p>"
            f"<p><strong>Failure modes:</strong> {e(', '.join(crit.failure_modes))}</p>"
            f"{alt}"
        )

    base_url = _webhook_base_url()
    if base_url:
        approve_url = _magic_link(base_url, "approve", approve_token)
        reject_url = _magic_link(base_url, "reject", reject_token)
        buttons_html = (
            "<h3>Approve or reject</h3>"
            "<p style='margin:16px 0;'>"
            f"<a href='{e(approve_url)}' "
            "style='display:inline-block;padding:10px 20px;margin-right:8px;"
            "background:#1f883d;color:#fff;text-decoration:none;border-radius:6px;"
            "font-weight:600;'>Approve</a>"
            f"<a href='{e(reject_url)}' "
            "style='display:inline-block;padding:10px 20px;"
            "background:#cf222e;color:#fff;text-decoration:none;border-radius:6px;"
            "font-weight:600;'>Reject</a>"
            "</p>"
            "<p style='color:#888;font-size:12px;'>Links expire in 72 hours. "
            f"Or from the CLI: <code>minions decisions approve {e(str(decision.id))}</code></p>"
        )
    else:
        buttons_html = (
            "<h3>Approve or reject</h3>"
            '<pre style="background:#f6f8fa;padding:12px;border-radius:6px;font-size:13px;">'
            f"minions decisions approve {e(str(decision.id))}\n"
            f"minions decisions reject  {e(str(decision.id))}</pre>"
            '<p style="color:#888;font-size:12px;margin-top:24px;">'
            "Magic-link tokens (HMAC-signed, 72h TTL — set "
            "<code>MINIONS_WEBHOOK_BASE_URL</code> to make these clickable):<br>"
            f"approve: <code>{e(approve_token[:32])}…</code><br>"
            f"reject:&nbsp;&nbsp;<code>{e(reject_token[:32])}…</code></p>"
        )

    return f"""<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:720px;color:#222;">
<h2 style="margin-bottom:4px;">{e(decision.summary)}</h2>
<p style="color:#666;margin-top:0;">Project <strong>{e(decision.project)}</strong> · type <strong>{e(decision.type.value)}</strong> · risk <strong>{e(decision.risk)}</strong></p>
<table style="border-collapse:collapse;font-size:14px;">
  <tr><td style="padding:4px 12px 4px 0;color:#666;">Proposer</td><td>{e(proposer)} <span style="color:#999;">({e(decision.proposer_role)})</span></td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#666;">ID</td><td><code>{e(str(decision.id))}</code></td></tr>
</table>
<h3>Rationale</h3>
<p>{e(decision.rationale)}</p>
<h3>Plan</h3>
<pre style="background:#f6f8fa;padding:12px;border-radius:6px;overflow:auto;font-size:13px;">{e(decision.diff_or_plan or "(none)")}</pre>
{critique_html}
{buttons_html}
</body></html>"""
