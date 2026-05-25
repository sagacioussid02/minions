"""Prompt formatting and write helpers for agent memory."""

from __future__ import annotations

from minions.models.agent_memory import AgentMemoryRecord


def recent_work_preamble(records: list[AgentMemoryRecord], *, char_cap: int = 5000) -> str:
    if not records:
        return ""
    lines = ["Your Recent Work:"]
    used = len(lines[0])
    for record in records:
        line = f"- [{record.event}] {record.summary}"
        if record.pr_url:
            line += f" ({record.pr_url})"
        if used + len(line) + 1 > char_cap:
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines) if len(lines) > 1 else ""
