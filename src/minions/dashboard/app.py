"""Streamlit operator dashboard — entrypoint.

Run via ``minions dashboard`` (which spawns ``streamlit run`` against this
file). Three pages in the sidebar:

  🤖 Agents       — grid of every (project, role) bucket with status + activity
  📋 Decisions    — counters + filterable queue with drill-down
  📊 Sprint Board — per-project kanban (pending / approved / PR open / done)

All data is read fresh from cost log + decision store on every render. The
``@st.cache_data(ttl=5)`` decorator on the loader keeps the UI snappy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from minions.dashboard.data import (
    AgentSummary,
    DashboardData,
    SprintBoard,
    build_dashboard_data,
)
from minions.dashboard.styles import banner, risk_pill, status_pill
from minions.dashboard.styles import inject as inject_css
from minions.models.decision import Decision, DecisionStatus

REPO_ROOT = Path(__file__).resolve().parents[3]
PROJECTS_DIR = REPO_ROOT / "projects"
PORTFOLIO_PATH = REPO_ROOT / "config" / "portfolio.yaml"
DECISION_STORE_PATH = REPO_ROOT / "data" / "local" / "decisions.json"
COST_LOG_PATH = REPO_ROOT / "data" / "local" / "cost_log.jsonl"
ACTIVITY_LOG_PATH = REPO_ROOT / "data" / "local" / "activity.jsonl"
ENGINEER_RUNS_PATH = REPO_ROOT / "data" / "local" / "engineer_runs.json"
AUDIT_FINDINGS_PATH = REPO_ROOT / "data" / "local" / "audit_findings.json"


STATUS_BADGE = {
    "active": "🟢 Active",
    "idle": "🟡 Idle",
    "stale": "⚪ Stale",
    "error": "🔴 Error",
}

STATUS_ORDER = {"active": 0, "idle": 1, "error": 2, "stale": 3}


@st.cache_data(ttl=60, show_spinner="Loading from Postgres…")
def _load() -> DashboardData:
    return build_dashboard_data(
        projects_dir=PROJECTS_DIR,
        portfolio_config_path=PORTFOLIO_PATH,
        decision_store_path=DECISION_STORE_PATH,
        cost_log_path=COST_LOG_PATH,
        engineer_runs_store_path=ENGINEER_RUNS_PATH,
        activity_log_path=ACTIVITY_LOG_PATH,
        audit_findings_store_path=AUDIT_FINDINGS_PATH,
    )


def _format_age(ts: datetime | None) -> str:
    if ts is None:
        return "never"
    delta = datetime.now(tz=UTC) - ts
    s = int(delta.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


# ---------------------------------------------------------------------------
# Page: Agents
# ---------------------------------------------------------------------------


def render_agents(data: DashboardData) -> None:
    st.header("🤖 Agents")
    st.caption(
        f"{len(data.agents)} agent buckets across "
        f"{len({a.project for a in data.agents if a.project})} projects + shared layers · "
        f"{data.cost_log_entries} cost log entries"
    )

    # Cost-over-time chart (last 14 days, one series per project).
    _render_portfolio_cost_chart()

    # Layout toggle (Tree default · Grid alt). Using st.radio rather than
    # segmented_control because the latter's default+key interaction can
    # silently fall through to None on stale session state.
    layout = st.radio(
        "Layout",
        ["🌳 Tree", "▦ Grid"],
        index=0,  # default to Tree
        horizontal=True,
        key="agents_layout",
        label_visibility="collapsed",
    )

    # Filters
    col1, col2, col3 = st.columns(3)
    projects = sorted({a.project or "(shared)" for a in data.agents})
    with col1:
        project_filter = st.multiselect("Project", projects, default=projects)
    statuses = ["active", "idle", "stale", "error"]
    with col2:
        status_filter = st.multiselect("Status", statuses, default=["active", "idle", "stale"])
    with col3:
        sort_by = st.selectbox("Sort by", ["status", "last activity", "cost (7d)", "calls (total)"])

    visible = [
        a
        for a in data.agents
        if (a.project or "(shared)") in project_filter and a.status in status_filter
    ]

    def sort_key(a: AgentSummary) -> tuple[float, ...]:
        if sort_by == "status":
            return (
                STATUS_ORDER[a.status],
                -(a.last_activity.timestamp() if a.last_activity else 0),
            )
        if sort_by == "last activity":
            return (-(a.last_activity.timestamp() if a.last_activity else 0),)
        if sort_by == "cost (7d)":
            return (-a.cost_7d_usd,)
        return (float(-a.calls_total),)

    visible.sort(key=sort_key)

    if not visible:
        st.info("No agents match the current filters.")
        return

    if layout != "▦ Grid":  # default + tree → tree
        _render_tree(visible)
    else:
        cols = st.columns(3)
        for idx, agent in enumerate(visible):
            with cols[idx % 3]:
                _render_agent_card(agent)


# ---- Hierarchy / tree view ------------------------------------------------


_SHARED_LAYER_ROLES = {
    "Executive": {"ceo", "cto", "managing_director", "org_owner"},
    "Specialist": {"cloud_devops", "devsecops", "team_architect"},
    "Audit": {
        "chief_auditor",
        "process_auditor",
        "code_auditor",
        "cost_auditor",
        "devils_advocate",
    },
}


def _render_tree(agents: list[AgentSummary]) -> None:
    """Render agents as a nested tree:
    Operator → Layer (shared) / Project (project) → Role → Seat
    """
    project_agents: dict[str, list[AgentSummary]] = {}
    shared_layer_agents: dict[str, list[AgentSummary]] = {n: [] for n in _SHARED_LAYER_ROLES}
    other_shared: list[AgentSummary] = []

    for a in agents:
        if a.scope == "project" and a.project:
            project_agents.setdefault(a.project, []).append(a)
        else:
            placed = False
            for layer_name, role_set in _SHARED_LAYER_ROLES.items():
                if a.role in role_set:
                    shared_layer_agents[layer_name].append(a)
                    placed = True
                    break
            if not placed:
                other_shared.append(a)

    st.markdown("### 👑 Owner")
    st.caption("portfolio owner — sole human-in-the-loop approver")

    # Sidebar-style toggle: collapse all by default, or expand. Default expanded
    # because the whole point of the tree view is to *see* the hierarchy.
    prev_expand_all = st.session_state.get("agents_tree_expand", True)
    expand_all = st.toggle(
        "Expand all",
        value=True,
        key="agents_tree_expand",
        help="Toggle off to collapse every layer",
    )

    # When the toggle changes, force-update every expander's session-state key
    # so the change takes effect even after the user has manually opened/closed one.
    _tree_expander_keys = [
        "tree_exp_executive",
        "tree_exp_specialist",
        "tree_exp_audit",
        "tree_exp_other_shared",
    ]
    # Build project keys dynamically
    project_keys = [f"tree_exp_proj_{p}" for p in sorted(project_agents)]
    _tree_expander_keys.extend(project_keys)

    if expand_all != prev_expand_all:
        for k in _tree_expander_keys:
            st.session_state[k] = expand_all

    # Shared layers
    for idx, layer_name in enumerate(["Executive", "Specialist", "Audit"]):
        members = shared_layer_agents[layer_name]
        if not members:
            continue
        running = sum(1 for m in members if m.running_now)
        active_count = sum(1 for m in members if m.status == "active")
        stale_count = sum(1 for m in members if m.status == "stale")
        live = f" · 🔴 {running} running" if running else ""
        with st.expander(
            f"**{layer_name}** — {len(members)} agents · "
            f"🟢 {active_count} active · ⚪ {stale_count} stale{live}",
            expanded=expand_all or running > 0,
            key=_tree_expander_keys[idx],
        ):
            for a in members:
                _render_tree_row(a)

    if other_shared:
        with st.expander(
            f"**Other shared** — {len(other_shared)} agents",
            expanded=expand_all,
            key=_tree_expander_keys[3],
        ):
            for a in other_shared:
                _render_tree_row(a)

    st.divider()

    # Per-project crews
    for idx, project in enumerate(sorted(project_agents)):
        members = project_agents[project]
        running = sum(1 for m in members if m.running_now)
        active_count = sum(1 for m in members if m.status == "active")
        stale_count = sum(1 for m in members if m.status == "stale")
        cost_7d = sum(m.cost_7d_usd for m in members)
        live = f" · 🔴 {running} running" if running else ""
        with st.expander(
            f"📁 **{project}** — {len(members)} agents · "
            f"🟢 {active_count} active · ⚪ {stale_count} stale · "
            f"7d ${cost_7d:.4f}{live}",
            expanded=expand_all or running > 0,
            key=project_keys[idx],
        ):
            for a in sorted(members, key=lambda x: (x.role, x.primary_label)):
                _render_tree_row(a)


def _render_tree_row(a: AgentSummary) -> None:
    """One row inside a tree node — pill + name + role on line 1, meta on line 2."""
    role_pretty = a.role.replace("_", " ").title()
    primary = a.primary_label
    seat_note = f' <span style="color:#64748b;">({a.seats} seats)</span>' if a.seats > 1 else ""
    cost_note = f" · 7d <code>${a.cost_7d_usd:.4f}</code>" if a.cost_7d_usd > 0 else ""
    last_seen = _format_age(a.last_activity)
    next_desc = a.next_run.description if a.next_run else "unknown"

    cols = st.columns([8, 1.2])
    with cols[0]:
        st.markdown(
            f'<div class="tree-row">'
            f"{status_pill(a.status, running=a.running_now)} "
            f'<span class="label">{primary}</span>'
            f'<span class="role">· {role_pretty}{seat_note}</span>'
            f'<span class="meta">last seen {last_seen}{cost_note} · next: {next_desc}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )
    if cols[1].button("Details", key=f"tree-{a.scope}-{a.project}-{a.role}"):
        st.session_state["agent_detail"] = (a.scope, a.project, a.role)


def _render_portfolio_cost_chart() -> None:
    """Stacked area: daily cost per project over the last 14 days."""
    from minions.dashboard.data import daily_cost_series

    series = daily_cost_series(cost_log_path=COST_LOG_PATH, days=14)
    if not series:
        st.caption("_No cost history yet — runs will populate this chart._")
        return
    # Convert to a wide DataFrame: rows = days, columns = projects.
    import pandas as pd

    days = sorted({d for project_series in series.values() for d, _ in project_series})
    df = pd.DataFrame(index=pd.Index(days, name="day"))
    for project, points in series.items():
        df[project] = [v for _, v in sorted(points, key=lambda kv: kv[0])]
    df = df.fillna(0.0)
    if df.values.sum() == 0:
        st.caption("_No cost in the last 14 days._")
        return
    st.markdown("##### Cost over time (last 14 days, USD)")
    st.area_chart(df, height=180, use_container_width=True)


def _render_sparkline(project: str, role: str) -> None:
    """Tiny 14-day cost sparkline rendered inside an agent card."""
    from minions.dashboard.data import cost_series_for

    points = cost_series_for(project, role, cost_log_path=COST_LOG_PATH, days=14)
    if all(v == 0 for _, v in points):
        return  # don't bother rendering an empty sparkline
    import pandas as pd

    df = pd.DataFrame({"day": [p[0] for p in points], "cost": [p[1] for p in points]}).set_index(
        "day"
    )
    st.line_chart(df, height=60, use_container_width=True)


def _render_agent_card(a: AgentSummary) -> None:
    primary = a.primary_label
    scope = f"<span style='color:#64748b;'>· {a.scope_label}</span>"
    seat_note = f" <span style='color:#64748b;'>· {a.seats} seats</span>" if a.seats > 1 else ""
    border_color = {
        "active": "#22d3ee",
        "idle": "#ca8a04",
        "stale": "#475569",
        "error": "#dc2626",
    }[a.status]

    with st.container(border=True):
        st.markdown(
            f"<div style='border-left: 3px solid {border_color}; padding: 2px 0 2px 10px;'>"
            f"<div style='display:flex;gap:6px;align-items:center;flex-wrap:wrap;'>"
            f"{status_pill(a.status, running=a.running_now)}"
            f"<span style='font-weight:600;color:#e2e8f0;'>{primary}</span>"
            f"<span style='color:#94a3b8;font-size:0.85rem;'>{a.role.replace('_', ' ').title()}</span>"
            f"{scope}{seat_note}"
            f"</div>"
            f"<div style='color:#64748b;font-size:0.78rem;margin-top:2px;'>"
            f"{_format_age(a.last_activity)} · tier <code>{a.tier}</code>"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2)
        c1.metric("7d cost", f"${a.cost_7d_usd:.4f}")
        c2.metric("7d calls", a.calls_7d)
        if a.project and a.cost_7d_usd > 0:
            _render_sparkline(a.project, a.role)
        if a.next_run:
            st.caption(f"Next run: {a.next_run.description}")
        if a.calls_total:
            st.caption(
                f"All-time: {a.calls_total} calls · ${a.cost_total_usd:.4f}"
                + (f" · last decision `{a.last_decision_id[:8]}`" if a.last_decision_id else "")
            )
        if a.seats > 1:
            with st.expander(f"Seats ({a.seats})"):
                for label in a.seat_labels:
                    st.write(f"- {label}")
        if st.button("View activity", key=f"grid-{a.scope}-{a.project}-{a.role}"):
            st.session_state["agent_detail"] = (a.scope, a.project, a.role)


def _render_agent_detail(data: DashboardData) -> None:
    """Modal-ish detail panel triggered by 'View activity' / 'Details'.

    Streamlit doesn't have a true modal in stable; we render a prominent
    bordered container at the top of the Agents page when an agent is
    selected, with a Close button.
    """
    sel = st.session_state.get("agent_detail")
    if not sel:
        return
    scope, project, role = sel
    agent = next(
        (a for a in data.agents if a.scope == scope and a.project == project and a.role == role),
        None,
    )
    if agent is None:
        st.session_state.pop("agent_detail", None)
        return

    with st.container(border=True):
        c1, c2 = st.columns([6, 1])
        c1.subheader(
            f"🔎 {agent.primary_label} · "
            f"{agent.role.replace('_', ' ').title()} · "
            f"{agent.scope_label}"
        )
        if c2.button("✕ Close"):
            st.session_state.pop("agent_detail", None)
            st.rerun()

        st.markdown(
            f"**Status:** {STATUS_BADGE[agent.status]}"
            + (" · 🔴 RUNNING" if agent.running_now else "")
            + f" · **Tier:** `{agent.tier}` · **Seats:** {agent.seats}"
        )
        if agent.next_run:
            st.markdown(f"**Next run:** {agent.next_run.description}")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Last activity", _format_age(agent.last_activity))
        m2.metric("7d cost", f"${agent.cost_7d_usd:.4f}")
        m3.metric("7d calls", agent.calls_7d)
        m4.metric("All-time cost", f"${agent.cost_total_usd:.4f}")

        # Recent activity log
        if agent.project:
            from minions.activity import history_for_role

            history = history_for_role(agent.project, agent.role, limit=20, path=ACTIVITY_LOG_PATH)
            with st.expander(f"Recent crew activity ({len(history)})", expanded=True):
                if not history:
                    st.caption("No activity events recorded yet.")
                else:
                    for h in history:
                        emoji = {
                            "crew_started": "🟢",
                            "crew_finished": "✅",
                            "crew_failed": "❌",
                        }.get(h.event, "•")
                        st.write(
                            f"{emoji} `{h.timestamp.strftime('%m-%d %H:%M:%S UTC')}` · "
                            f"**{h.event}** · crew=`{h.crew}` · "
                            + (f"decision=`{h.decision_id[:8]}`" if h.decision_id else "")
                            + (f" · error: _{h.error}_" if h.error else "")
                        )

        # Recent cost entries (the LLM calls themselves)
        if agent.project:
            from minions.cost import read_log

            entries = [
                e
                for e in read_log(COST_LOG_PATH)
                if e.project == agent.project and e.role == agent.role
            ]
            entries.sort(key=lambda e: e.timestamp, reverse=True)
            if entries:
                rows = [
                    {
                        "when": e.timestamp.strftime("%m-%d %H:%M:%S"),
                        "model": e.model.split("/")[-1][:24],
                        "in": e.input_tokens,
                        "out": e.output_tokens,
                        "cost": f"${e.cost_usd:.4f}",
                        "decision": e.decision_id[:8] if e.decision_id else "—",
                    }
                    for e in entries[:20]
                ]
                with st.expander(f"Last {min(20, len(entries))} LLM calls", expanded=False):
                    st.dataframe(rows, use_container_width=True, hide_index=True)

        if agent.seats > 1:
            with st.expander(f"Seats ({agent.seats})"):
                for label in agent.seat_labels:
                    st.write(f"- {label}")


# ---------------------------------------------------------------------------
# Page: Decisions
# ---------------------------------------------------------------------------


def render_decisions(data: DashboardData) -> None:
    st.header("📋 Decisions queue")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Pending", data.pending_count, delta_color="inverse")
    c2.metric("Approved", data.approved_count)
    c3.metric("Rejected", data.rejected_count)
    c4.metric("Executed (PR open)", data.executed_count)

    if data.pending_count > 0:
        st.warning(f"⚠️ {data.pending_count} decision(s) waiting for your review")

    # Filters
    col1, col2, col3 = st.columns(3)
    projects = sorted({d.project for d in data.decisions})
    with col1:
        project_filter = st.multiselect("Project", projects, default=projects)
    with col2:
        status_options = [s.value for s in DecisionStatus]
        status_filter = st.multiselect(
            "Status",
            status_options,
            default=["pending", "approved"],
        )
    with col3:
        risk_filter = st.multiselect(
            "Risk", ["low", "medium", "high"], default=["low", "medium", "high"]
        )

    visible = sorted(
        (
            d
            for d in data.decisions
            if d.project in project_filter
            and d.status.value in status_filter
            and d.risk in risk_filter
        ),
        key=lambda d: d.created_at,
        reverse=True,
    )

    if not visible:
        st.info("No decisions match the current filters.")
        return

    rows = [
        {
            "id": str(d.id)[:8],
            "project": d.project,
            "status": d.status.value,
            "type": d.type.value,
            "risk": d.risk,
            "summary": d.summary[:80],
            "proposer": d.proposer_display_name or d.proposer_agent_id,
            "age": _format_age(d.created_at),
        }
        for d in visible
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Decision detail")
    selected = st.selectbox(
        "Inspect",
        options=[f"{str(d.id)[:8]} — {d.project} · {d.summary[:60]}" for d in visible],
    )
    if selected:
        prefix = selected.split(" — ")[0]
        chosen = next((d for d in visible if str(d.id).startswith(prefix)), None)
        if chosen is not None:
            _render_decision_detail(chosen)


def _render_decision_detail(d: Decision) -> None:
    proposer = d.proposer_display_name or d.proposer_agent_id
    st.markdown(
        f"**{d.summary}**\n\n"
        f"Project `{d.project}` · type `{d.type.value}` · risk `{d.risk}` · "
        f"status `{d.status.value}` · proposer `{proposer}` ({d.proposer_role})"
    )
    st.caption(f"id: `{d.id}` · created {_format_age(d.created_at)}")
    with st.expander("Rationale", expanded=False):
        st.write(d.rationale)
    with st.expander("Plan / diff", expanded=True):
        st.markdown(d.diff_or_plan or "_(none)_")
    if d.resolved_reason:
        st.info(f"Resolved reason: {d.resolved_reason}")
    if d.pr_url:
        st.success(f"PR: {d.pr_url}")
    if d.status is DecisionStatus.PENDING:
        st.markdown("#### Resolve")
        reason = st.text_input(
            "Reason (optional)",
            key=f"reason-{d.id}",
            placeholder="Why approve / reject? Goes into the audit log.",
        )
        c_app, c_rej, _ = st.columns([1, 1, 4])
        if c_app.button("✅ Approve", key=f"approve-{d.id}", type="primary"):
            _resolve_in_dashboard(d, action="approve", reason=reason or None)
        if c_rej.button("❌ Reject", key=f"reject-{d.id}"):
            # No native confirm dialog; we use session_state to two-step.
            confirm_key = f"confirm-reject-{d.id}"
            if st.session_state.get(confirm_key):
                _resolve_in_dashboard(d, action="reject", reason=reason or None)
                st.session_state.pop(confirm_key, None)
            else:
                st.session_state[confirm_key] = True
                st.warning("Click Reject again to confirm.")
                st.rerun()


def _resolve_in_dashboard(d: Decision, *, action: str, reason: str | None) -> None:
    """Call the same `resolve()` the CLI does — in-process, no webhook."""
    from minions.approval.service import resolve
    from minions.approval.store_factory import make_decision_store
    from minions.notify.console import ConsoleNotifier

    try:
        resolve(
            d.id,
            store=make_decision_store(DECISION_STORE_PATH),
            notifier=ConsoleNotifier(),
            action=action,
            reason=reason,
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Failed to {action}: {e}")
        return
    _load.clear()
    st.success(f"Decision `{str(d.id)[:8]}` {action}d.")
    st.rerun()


# ---------------------------------------------------------------------------
# Page: Sprint board
# ---------------------------------------------------------------------------


def render_sprint_board(data: DashboardData) -> None:
    cols = st.columns([6, 2])
    cols[0].header("📊 Sprint board")
    if cols[1].button(
        "🔄 Sync PR status", help="Pull merge/close state from GitHub", use_container_width=True
    ):
        _sync_in_dashboard()

    if not data.sprint_boards:
        st.info("No active projects.")
        return

    project_tabs = st.tabs(list(data.sprint_boards.keys()))
    for tab, (project, board) in zip(project_tabs, data.sprint_boards.items(), strict=False):
        with tab:
            _render_board(board)


def _sync_in_dashboard() -> None:
    """Run the PR-state sync in-process. Reuses the CLI's GitHub client factory."""
    try:
        from minions.github.auth import get_github_token
        from minions.github.client import GitHubClient
        from minions.models.manifest import load_active_manifests
        from minions.sync import sync_pr_status

        manifests = load_active_manifests(REPO_ROOT / "projects")

        def _open_client(manifest):  # type: ignore[no-untyped-def]
            repo = (manifest.source.repo or "").strip()
            if not repo or repo.upper() == "TBD" or "/" not in repo:
                return None
            try:
                token = get_github_token()
            except Exception:
                return None
            return GitHubClient(token=token, repo=repo)

        from minions.approval.store_factory import make_decision_store
        from minions.crews.engineer_runs_store_factory import make_engineer_runs_store

        report = sync_pr_status(
            store=make_engineer_runs_store(ENGINEER_RUNS_PATH),
            open_github_client=_open_client,
            manifests=manifests,
            decision_store=make_decision_store(DECISION_STORE_PATH),
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Sync failed: {e}")
        return

    _load.clear()
    if report.merged:
        st.success(f"✅ {report.merged} PR(s) marked merged. {report.changed} total changes.")
    elif report.changed:
        st.info(f"↻ {report.changed} PR(s) updated.")
    else:
        st.caption("No changes — all open PRs are still open.")
    st.rerun()


def _render_board(b: SprintBoard) -> None:
    st.caption(
        f"{b.total} decision(s) · "
        f"{len(b.pending)} pending · {len(b.approved)} approved · "
        f"{len(b.pr_open)} PR open · {len(b.done)} done"
    )
    if b.total == 0:
        st.info(
            f"No decisions yet for {b.project}. Run `minions plan {b.project} --no-dry-run` to seed one."
        )
        return

    cols = st.columns(5)
    headers = ["📥 Pending", "✅ Approved", "🔧 In progress", "🔍 PR open", "📦 Done / ✗ Rejected"]
    columns = [b.pending, b.approved, b.in_progress, b.pr_open, b.done]
    for col, header, items in zip(cols, headers, columns, strict=True):
        with col:
            st.markdown(f"**{header}** ({len(items)})")
            if header.startswith("🔧") and not items:
                st.caption("_Phase B — needs EngineerResult persistence_")
            for d in items:
                _render_kanban_card(d)


def _render_kanban_card(d: Decision) -> None:
    risk_color = {"low": "#16a34a", "medium": "#ca8a04", "high": "#dc2626"}.get(d.risk, "#94a3b8")
    proposer = d.proposer_display_name or d.proposer_agent_id
    with st.container(border=True):
        st.markdown(
            f"<div style='border-left: 3px solid {risk_color}; padding: 4px 0 4px 8px;'>"
            f"<div style='display:flex;gap:6px;align-items:center;font-size:0.72rem;color:#64748b;'>"
            f"<code>{str(d.id)[:8]}</code> · {d.type.value} · {risk_pill(d.risk)}"
            f"</div>"
            f"<div style='font-weight:600;color:#e2e8f0;margin-top:2px;'>{d.summary[:80]}</div>"
            f"<div style='font-size:0.75rem;color:#64748b;margin-top:2px;'>"
            f"{proposer} · {_format_age(d.created_at)}"
            f"</div>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# Page: Audit
# ---------------------------------------------------------------------------


def render_audit(data: DashboardData) -> None:
    from minions.models.audit import FindingStatus

    st.header("🛡️ Audit & Challenge")
    findings = data.audit_findings

    open_count = sum(1 for f in findings if f.status == FindingStatus.OPEN)
    high = sum(1 for f in findings if f.severity == "high" and f.status == FindingStatus.OPEN)
    medium = sum(1 for f in findings if f.severity == "medium" and f.status == FindingStatus.OPEN)
    advisory = sum(
        1 for f in findings if f.severity == "advisory" and f.status == FindingStatus.OPEN
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Open", open_count)
    c2.metric("🔴 High", high)
    c3.metric("🟡 Medium", medium)
    c4.metric("🔵 Advisory", advisory)

    if not findings:
        st.info(
            "No findings yet. Auditors fire after a merged PR transitions through "
            "`minions sync` (or the daily cron). High-risk decisions are 100% sampled, "
            "medium 50%, low 25%."
        )
        return

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        sev_filter = st.multiselect(
            "Severity", ["high", "medium", "advisory"], default=["high", "medium", "advisory"]
        )
    with col2:
        status_filter = st.multiselect(
            "Status",
            [s.value for s in FindingStatus],
            default=[FindingStatus.OPEN.value],
        )
    with col3:
        projects = sorted({f.source_project for f in findings if f.source_project})
        project_filter = st.multiselect("Project", projects, default=projects)

    visible = sorted(
        (
            f
            for f in findings
            if f.severity in sev_filter
            and f.status.value in status_filter
            and (f.source_project or "") in project_filter
        ),
        key=lambda f: f.created_at,
        reverse=True,
    )

    if not visible:
        st.info("No findings match the current filters.")
        return

    rows = [
        {
            "id": str(f.id)[:8],
            "when": f.created_at.strftime("%m-%d %H:%M"),
            "project": f.source_project or "—",
            "severity": f.severity,
            "auditor": f.auditor_role,
            "summary": f.summary[:80],
            "PR": f.source_pr_url[-20:] if f.source_pr_url else "—",
            "status": f.status.value,
        }
        for f in visible
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Finding detail")
    selected = st.selectbox(
        "Inspect",
        options=[f"{str(f.id)[:8]} — {f.severity} · {f.summary[:60]}" for f in visible],
    )
    if selected:
        prefix = selected.split(" — ")[0]
        chosen = next((f for f in visible if str(f.id).startswith(prefix)), None)
        if chosen is not None:
            _render_finding_detail(chosen)


def _render_finding_detail(f) -> None:  # type: ignore[no-untyped-def]
    sev_color = {"high": "#dc2626", "medium": "#ca8a04", "advisory": "#22d3ee"}.get(
        f.severity, "#94a3b8"
    )
    st.markdown(
        f"<div style='border-left: 4px solid {sev_color}; padding-left: 12px;'>"
        f"<strong>{f.summary}</strong><br>"
        f"<small style='color:#64748b;'>"
        f"Project <code>{f.source_project or '—'}</code> · "
        f"category <code>{f.category.value}</code> · "
        f"severity <code>{f.severity}</code> · "
        f"auditor <code>{f.auditor_role}</code> · "
        f"status <code>{f.status.value}</code>"
        f"</small>"
        f"</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"id: `{f.id}` · created {f.created_at}")
    if f.source_pr_url:
        st.markdown(f"**PR:** {f.source_pr_url}")
    if f.source_decision_id:
        st.markdown(f"**Decision:** `{f.source_decision_id}`")
    st.markdown("**Evidence**")
    st.write(f.evidence)
    st.markdown("**Recommendation**")
    st.write(f.recommendation)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(page_title="Minions", page_icon="⌬", layout="wide")
    inject_css()

    with st.sidebar:
        st.markdown(
            "<div style='font-size:1.4rem;font-weight:800;"
            "background:linear-gradient(180deg,#22d3ee,#0e7490);"
            "-webkit-background-clip:text;background-clip:text;color:transparent;'>"
            "⌬ Minions"
            "</div>",
            unsafe_allow_html=True,
        )
        st.caption("Autonomous engineering org")
        page = st.radio(
            "Page",
            ["🤖 Agents", "📋 Decisions", "📊 Sprint Board", "🛡️ Audit"],
        )
        st.divider()
        if st.button("🔄 Refresh now", use_container_width=True):
            _load.clear()
        st.caption("Auto-refresh on interaction (5s cache)")

    data = _load()
    st.markdown(
        banner(f"snapshot {data.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}"),
        unsafe_allow_html=True,
    )

    if page == "🤖 Agents":
        _render_agent_detail(data)
        render_agents(data)
    elif page == "📋 Decisions":
        render_decisions(data)
    elif page == "📊 Sprint Board":
        render_sprint_board(data)
    elif page == "🛡️ Audit":
        render_audit(data)


if __name__ == "__main__":
    main()
