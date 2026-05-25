"""Question classification and role routing for spokesperson interviews."""

from __future__ import annotations

from typing import Literal

QuestionKind = Literal[
    "technical",
    "functional",
    "deployment",
    "security",
    "cost",
    "portfolio",
    "generic",
]

SPOKESPERSON_ROLES = [
    "product_manager",
    "cto",
    "managing_director",
    "portfolio_owner",
    "security_champion",
]


def classify_question(question: str) -> QuestionKind:
    q = question.lower()
    if any(
        w in q
        for w in [
            "deploy",
            "deployment",
            "hosting",
            "hosted",
            "runtime",
            "infra",
            "server",
            "cloud",
        ]
    ):
        return "deployment"
    if any(
        w in q
        for w in [
            "secret",
            "password",
            "token",
            "api key",
            "api_key",
            "rotate",
            "rotation",
            "vulnerability",
            "security",
        ]
    ):
        return "security"
    if any(w in q for w in ["cost", "spend", "budget", "burn", "expensive", "usage"]):
        return "cost"
    if any(
        w in q
        for w in [
            "architecture",
            "code",
            "stack",
            "database",
            "api",
            "framework",
            "library",
            "technical",
        ]
    ):
        return "technical"
    if any(
        w in q
        for w in [
            "roadmap",
            "feature",
            "user",
            "workflow",
            "sprint",
            "demo",
            "status",
            "requirement",
        ]
    ):
        return "functional"
    if any(w in q for w in ["portfolio", "investor", "strategy", "priority", "staffing", "team"]):
        return "portfolio"
    return "generic"


def route_roles(kind: QuestionKind, *, spokesperson_role: str) -> list[str]:
    routes: dict[QuestionKind, list[str]] = {
        "functional": ["product_manager", "manager"],
        "technical": ["principal_engineer", "team_architect"],
        "deployment": ["cloud_devops", "principal_engineer"],
        "security": ["security_champion", "devsecops"],
        "cost": ["cost_auditor", "cto", "managing_director"],
        "portfolio": ["cto", "managing_director", "portfolio_owner"],
        "generic": ["product_manager", "manager"],
    }
    ordered = [spokesperson_role, *routes[kind]]
    out: list[str] = []
    for role in ordered:
        normalized = normalize_role(role)
        if normalized not in out:
            out.append(normalized)
    return out


def normalize_role(role: str) -> str:
    return role.strip().lower().replace(" ", "_").replace("-", "_")
