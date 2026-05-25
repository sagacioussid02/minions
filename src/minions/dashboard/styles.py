"""Global CSS for the Streamlit dashboard.

Streamlit lets us inject CSS via ``st.markdown(unsafe_allow_html=True)``.
We keep all of it in one place so the visual feel is consistent and easy
to tune. Called once at the top of ``main()`` in ``app.py``.

Design language:
  * Dark canvas (slate-950) with cyan-400 accents.
  * Status pills replace bare emoji — visible at a glance, color-coded.
  * Cards have subtle elevation (1px ring + soft shadow) and a 4px left
    rule whose color encodes status.
  * Tree rows use vertical guide lines so the hierarchy is obvious.
  * Tightened typography: 14px base, 1.4 line-height.
"""

from __future__ import annotations

import streamlit as st


_CSS = """
<style>
  /* ---------- Typography & global layout ---------- */
  html, body, [class*="st-"], .stMarkdown, .stText, .stMetric label {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", sans-serif;
    font-feature-settings: "ss01", "tnum";
  }
  .block-container { padding-top: 2rem; padding-bottom: 4rem; max-width: 1400px; }
  h1, h2, h3 { letter-spacing: -0.01em; }
  h1 { font-weight: 700; }
  h2 { font-weight: 650; }
  h3 { font-weight: 600; font-size: 1.1rem; }
  .stCaption, [data-testid="stCaptionContainer"] {
    color: #64748b !important;  /* slate-500 */
    font-size: 0.82rem;
  }

  /* ---------- Top header banner ---------- */
  .minions-banner {
    display: flex; align-items: baseline; justify-content: space-between;
    border-bottom: 1px solid #1e293b; padding-bottom: 12px; margin-bottom: 18px;
  }
  .minions-banner .brand { display: flex; align-items: center; gap: 10px; }
  .minions-banner .logo {
    font-size: 1.4rem; font-weight: 800;
    background: linear-gradient(180deg, #22d3ee 0%, #0e7490 100%);
    -webkit-background-clip: text; background-clip: text; color: transparent;
    letter-spacing: -0.02em;
  }
  .minions-banner .tagline { color: #64748b; font-size: 0.85rem; }
  .minions-banner .meta { color: #94a3b8; font-size: 0.78rem; font-family: ui-monospace, monospace; }

  /* ---------- Status pills (replace bare emoji) ---------- */
  .pill {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 1px 8px; border-radius: 999px; font-size: 0.7rem; font-weight: 600;
    line-height: 1.5; border: 1px solid transparent;
    text-transform: uppercase; letter-spacing: 0.04em;
  }
  .pill.active  { background: #064e3b; color: #34d399; border-color: #065f46; }
  .pill.idle    { background: #422006; color: #fbbf24; border-color: #713f12; }
  .pill.stale   { background: #1e293b; color: #94a3b8; border-color: #334155; }
  .pill.error   { background: #450a0a; color: #f87171; border-color: #7f1d1d; }
  .pill.running {
    background: #450a0a; color: #fca5a5; border-color: #b91c1c;
    animation: minions-pulse 1.6s ease-in-out infinite;
  }
  @keyframes minions-pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
  }

  /* ---------- Risk pills ---------- */
  .risk-pill {
    display: inline-block; padding: 0 6px; border-radius: 4px;
    font-size: 0.65rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em;
  }
  .risk-pill.low    { background: #064e3b; color: #34d399; }
  .risk-pill.medium { background: #422006; color: #fbbf24; }
  .risk-pill.high   { background: #450a0a; color: #f87171; }

  /* ---------- Cards ---------- */
  div[data-testid="stVerticalBlockBorderWrapper"]:has(> div) {
    border-radius: 10px;
    transition: box-shadow 120ms ease, border-color 120ms ease;
  }
  div[data-testid="stVerticalBlockBorderWrapper"]:has(> div):hover {
    box-shadow: 0 4px 14px rgba(34, 211, 238, 0.08);
    border-color: #1e293b !important;
  }

  /* ---------- Expanders (tree nodes) ---------- */
  details[data-testid="stExpander"] summary {
    font-weight: 500;
  }
  details[data-testid="stExpander"] summary:hover {
    background: #111c2e;
  }

  /* ---------- Tree rows: vertical guide ---------- */
  .tree-row {
    position: relative;
    padding: 6px 0 6px 28px;
    border-left: 1px dashed #1e293b;
    margin-left: 8px;
    line-height: 1.45;
  }
  .tree-row::before {
    content: "";
    position: absolute; left: 0; top: 14px;
    width: 18px; height: 0; border-top: 1px dashed #334155;
  }
  .tree-row .label    { font-weight: 600; color: #e2e8f0; }
  .tree-row .role     { color: #64748b; font-size: 0.85rem; margin-left: 4px; }
  .tree-row .meta     { color: #64748b; font-size: 0.78rem; padding-left: 28px; display: block; }
  .tree-row code      { font-size: 0.78rem; background: #1e293b; padding: 0 4px; border-radius: 3px; }

  /* ---------- Buttons ---------- */
  .stButton > button {
    border-radius: 6px; font-weight: 500;
    border: 1px solid #1e293b;
  }
  .stButton > button:hover {
    border-color: #22d3ee;
    color: #22d3ee;
  }

  /* ---------- Metric refinements ---------- */
  [data-testid="stMetricValue"] {
    font-size: 1.4rem; font-weight: 600; letter-spacing: -0.02em;
  }
  [data-testid="stMetricLabel"] {
    font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; color: #64748b;
  }

  /* ---------- Sidebar tightening ---------- */
  section[data-testid="stSidebar"] .block-container { padding-top: 2rem; }
</style>
"""


def inject() -> None:
    """Inject the global stylesheet into the current Streamlit page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def status_pill(status: str, *, running: bool = False) -> str:
    """HTML for a status pill. Use inside ``st.markdown(unsafe_allow_html=True)``."""
    if running:
        return '<span class="pill running">● Running</span>'
    label_map = {
        "active": "Active",
        "idle": "Idle",
        "stale": "Stale",
        "error": "Error",
    }
    cls = status if status in label_map else "stale"
    return f'<span class="pill {cls}">● {label_map.get(cls, "Stale")}</span>'


def risk_pill(risk: str) -> str:
    cls = risk if risk in {"low", "medium", "high"} else "low"
    return f'<span class="risk-pill {cls}">{risk}</span>'


def banner(timestamp_text: str) -> str:
    """The branded header at the top of every page."""
    return f"""
<div class="minions-banner">
  <div class="brand">
    <span class="logo">⌬ Minions</span>
    <span class="tagline">Autonomous engineering org</span>
  </div>
  <div class="meta">{timestamp_text}</div>
</div>
"""
