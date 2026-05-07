"""Operator dashboard — Streamlit UI for agents, decisions, and sprint board.

Phase A (current): read-only views over existing data sources (cost log,
decision store, manifests, roster). No new persistence required.

Run via: ``minions dashboard`` (which spawns ``streamlit run`` under the hood).
"""

from minions.dashboard.data import (
    AgentSummary,
    DashboardData,
    SprintBoard,
    build_agent_summaries,
    build_dashboard_data,
    build_sprint_board,
)

__all__ = [
    "AgentSummary",
    "DashboardData",
    "SprintBoard",
    "build_agent_summaries",
    "build_dashboard_data",
    "build_sprint_board",
]
