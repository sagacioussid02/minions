"""FastAPI receiver for HMAC-signed approval magic links.

Flow:
1. Operator clicks ``/approve?token=…`` or ``/reject?token=…`` in their email.
2. We verify the token (HMAC + 72h TTL) via :func:`approval.tokens.verify`.
3. We load the Decision through :func:`approval.store_factory.make_decision_store`
   (Postgres in prod, JSON locally).
4. If still PENDING, call :func:`approval.service.resolve`. If already resolved
   we render the confirmation page idempotently with a note — clicking the same
   link twice should not error.
5. Render a small HTML confirmation page.

The app is intentionally tiny and stateless — Fly's ``auto_stop_machines``
keeps it at ~$0/mo. The store and notifier are dependency-injected via
``create_app(...)`` so tests don't need a real DB or SMTP connection.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from html import escape
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

from minions.approval.service import resolve
from minions.approval.store_factory import DecisionStoreLike, make_decision_store
from minions.approval.tokens import TokenError, verify
from minions.models.decision import Decision, DecisionStatus
from minions.notify.base import Notifier
from minions.notify.console import ConsoleNotifier

logger = logging.getLogger(__name__)


def _default_store_factory() -> DecisionStoreLike:
    # Same JSON fallback path as the CLI; in production MINIONS_DATABASE_URL
    # forces the Postgres backend so this path is never touched.
    repo_root = Path(__file__).resolve().parents[3]
    return make_decision_store(repo_root / "data" / "local" / "decisions.json")


def create_app(
    *,
    store_factory: Callable[[], DecisionStoreLike] | None = None,
    notifier: Notifier | None = None,
) -> FastAPI:
    """Build the FastAPI app. Injection points exist purely for tests."""

    app = FastAPI(title="minions approval webhook", docs_url=None, redoc_url=None)
    _store_factory = store_factory or _default_store_factory
    _notifier = notifier or ConsoleNotifier()

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    @app.get("/approve", response_class=HTMLResponse)
    def approve(token: str = Query(...)) -> HTMLResponse:
        return _handle(token, expected_action="approve")

    @app.get("/reject", response_class=HTMLResponse)
    def reject(token: str = Query(...)) -> HTMLResponse:
        return _handle(token, expected_action="reject")

    def _handle(token: str, *, expected_action: str) -> HTMLResponse:
        try:
            payload = verify(token)
        except TokenError as e:
            return _page(
                title="Link invalid or expired",
                body=(
                    f"<p>This approval link could not be verified: "
                    f"<code>{escape(str(e))}</code>.</p>"
                )
                + "<p>Tokens expire 72 hours after the email was sent. "
                "If this decision is still pending, run "
                "<code>minions decisions list</code> to resolve it from the CLI.</p>",
                status_code=400,
            )

        action = payload.get("action")
        if action != expected_action:
            return _page(
                title="Link mismatch",
                body=f"<p>Token action <code>{escape(str(action))}</code> does not match endpoint "
                f"<code>{escape(expected_action)}</code>.</p>",
                status_code=400,
            )

        decision_id = str(payload["id"])
        store = _store_factory()
        decision = store.get(decision_id)
        if decision is None:
            return _page(
                title="Decision not found",
                body=f"<p>No decision with id <code>{escape(decision_id)}</code>. "
                "It may have been deleted.</p>",
                status_code=404,
            )

        if decision.status != DecisionStatus.PENDING:
            return _already_resolved_page(decision)

        try:
            resolved = resolve(decision_id, store=store, notifier=_notifier, action=action)
        except Exception:
            logger.exception("failed to resolve decision %s via webhook", decision_id)
            return _page(
                title="Server error",
                body="<p>Something went wrong recording your response. "
                "Please retry from the CLI.</p>",
                status_code=500,
            )

        return _resolved_page(resolved, fresh=True)

    return app


# --- HTML rendering ---------------------------------------------------------


_BASE_STYLE = (
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "max-width:560px;margin:48px auto;padding:0 16px;color:#222;line-height:1.5;"
)


def _page(*, title: str, body: str, status_code: int = 200) -> HTMLResponse:
    html = (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{escape(title)}</title>"
        f'</head><body style="{_BASE_STYLE}">'
        f"<h2>{escape(title)}</h2>{body}"
        "<p style='color:#888;font-size:12px;margin-top:32px;'>minions approval webhook</p>"
        "</body></html>"
    )
    return HTMLResponse(html, status_code=status_code)


def _resolved_page(decision: Decision, *, fresh: bool) -> HTMLResponse:
    verdict = decision.status.value.upper()
    color = "#1f883d" if decision.status == DecisionStatus.APPROVED else "#cf222e"
    note = (
        "Recorded just now."
        if fresh
        else f"Already resolved at {escape(str(decision.resolved_at))}."
    )
    body = (
        f"<p>Decision <code>{escape(str(decision.id))}</code> is now "
        f"<strong style='color:{color};'>{escape(verdict)}</strong>.</p>"
        f"<p style='color:#666;'>{note}</p>"
        f"<p><strong>Project:</strong> {escape(decision.project)}<br>"
        f"<strong>Type:</strong> {escape(decision.type.value)}<br>"
        f"<strong>Summary:</strong> {escape(decision.summary)}</p>"
    )
    return _page(title=f"Decision {verdict.lower()}", body=body)


def _already_resolved_page(decision: Decision) -> HTMLResponse:
    return _resolved_page(decision, fresh=False)


# Module-level instance for ``uvicorn minions.webhook.app:app``.
# create_app does no I/O — the store is constructed lazily per request.
app = create_app()
