"""Read-only codebase scanning for spokesperson interviews."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from minions.github.client import GitHubClient, GitHubError
from minions.models.interview import InterviewCitation
from minions.models.manifest import Manifest
from minions.spokesperson.redaction import redact_secrets

SCAN_EXTENSIONS = {
    ".md",
    ".txt",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".env.example",
}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".next", "dist", "build"}


@dataclass
class CodeScanResult:
    summary: str
    files_inspected: list[str] = field(default_factory=list)
    citations: list[InterviewCitation] = field(default_factory=list)
    confidence: str = "unknown"


class ContentsClient(Protocol):
    def list_files(self, *, branch: str) -> list[str]: ...
    def get_text_file(self, *, path: str, branch: str) -> str | None: ...


def scan_codebase(
    *,
    manifest: Manifest,
    question: str,
    max_files: int = 14,
    github_client: ContentsClient | None = None,
) -> CodeScanResult:
    root = _local_root(manifest)
    if root is None:
        return _scan_github_contents(
            manifest=manifest,
            question=question,
            max_files=max_files,
            github_client=github_client,
        )

    keywords = _keywords(question)
    candidates = _candidate_files(root)
    scored = sorted(
        ((score_file(path, keywords), path) for path in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    inspected: list[str] = []
    citations: list[InterviewCitation] = []
    findings: list[str] = []

    for score, path in scored:
        if score <= 0 and inspected:
            continue
        if len(inspected) >= max_files:
            break
        rel = str(path.relative_to(root))
        text = _safe_read(path)
        if text is None:
            continue
        inspected.append(rel)
        snippet = _best_snippet(text, keywords) or text[:500]
        redacted = redact_secrets(snippet.strip())
        if redacted:
            findings.append(f"{rel}: {redacted[:220]}")
            citations.append(
                InterviewCitation(
                    source_type="code_scan",
                    label=rel,
                    reference=rel,
                    excerpt=redacted[:500],
                )
            )

    if not inspected:
        return CodeScanResult(
            summary=f"Scanned {root} but did not find readable project files relevant to the question.",
            confidence="low",
        )
    confidence = "medium" if citations else "low"
    return CodeScanResult(
        summary="Read-only scan inspected "
        + ", ".join(inspected[:6])
        + (f" and {len(inspected) - 6} more file(s)." if len(inspected) > 6 else ".")
        + (" Findings: " + " | ".join(findings[:3]) if findings else ""),
        files_inspected=inspected,
        citations=citations,
        confidence=confidence,
    )


def _scan_github_contents(
    *,
    manifest: Manifest,
    question: str,
    max_files: int,
    github_client: ContentsClient | None,
) -> CodeScanResult:
    if not manifest.source.repo:
        return CodeScanResult(
            summary="No local checkout or GitHub repository is configured for read-only scanning.",
            confidence="low",
        )

    client = github_client
    owns_client = False
    if client is None:
        token = os.environ.get("MINIONS_GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            return CodeScanResult(
                summary="GitHub repository is configured, but no GitHub token/client is available for fallback scanning.",
                confidence="low",
            )
        try:
            client = GitHubClient(token=token, repo=manifest.source.repo)
            owns_client = True
        except Exception:
            return CodeScanResult(
                summary="GitHub repository is configured, but no GitHub token/client is available for fallback scanning.",
                confidence="low",
            )

    try:
        branch = manifest.source.default_branch
        keywords = _keywords(question)
        files = [
            path
            for path in client.list_files(branch=branch)
            if _is_scannable_path(path) and not _is_skipped_path(path)
        ][:250]
        scored = sorted(
            ((score_github_path(path, keywords, client, branch), path) for path in files),
            key=lambda item: item[0],
            reverse=True,
        )

        inspected: list[str] = []
        citations: list[InterviewCitation] = []
        findings: list[str] = []
        for score, path in scored:
            if score <= 0 and inspected:
                continue
            if len(inspected) >= max_files:
                break
            text = client.get_text_file(path=path, branch=branch)
            if not text:
                continue
            inspected.append(path)
            snippet = _best_snippet(text, keywords) or text[:500]
            redacted = redact_secrets(snippet.strip())
            if redacted:
                findings.append(f"{path}: {redacted[:220]}")
                citations.append(
                    InterviewCitation(
                        source_type="code_scan",
                        label=path,
                        reference=f"github:{manifest.source.repo}:{path}",
                        excerpt=redacted[:500],
                    )
                )
    except GitHubError as e:
        return CodeScanResult(summary=f"GitHub fallback scan failed: {e}", confidence="low")
    finally:
        close = getattr(client, "close", None)
        if owns_client and callable(close):
            close()

    if not inspected:
        return CodeScanResult(
            summary="GitHub fallback scan did not find readable project files relevant to the question.",
            confidence="low",
        )
    return CodeScanResult(
        summary="GitHub fallback scan inspected "
        + ", ".join(inspected[:6])
        + (f" and {len(inspected) - 6} more file(s)." if len(inspected) > 6 else ".")
        + (" Findings: " + " | ".join(findings[:3]) if findings else ""),
        files_inspected=inspected,
        citations=citations,
        confidence="medium" if citations else "low",
    )


def _local_root(manifest: Manifest) -> Path | None:
    if not manifest.source.path:
        return None
    root = Path(manifest.source.path).expanduser()
    return root if root.is_dir() else None


def _keywords(question: str) -> set[str]:
    base = {
        w.strip(".,?:;()[]{}\"'").lower()
        for w in question.split()
        if len(w.strip(".,?:;()[]{}\"'")) >= 4
    }
    q = question.lower()
    if any(w in q for w in ["deploy", "host", "runtime"]):
        base.update({"deploy", "deployment", "vercel", "fly", "render", "railway", "docker", "compose", "kubernetes", "infra", "host"})
    if any(w in q for w in ["secret", "password", "token", "api key", "rotation"]):
        base.update({"secret", "token", "password", "env", "rotation", "clerk", "auth"})
    if any(w in q for w in ["stack", "tech", "framework"]):
        base.update({"dependencies", "framework", "next", "react", "fastapi", "package", "pyproject"})
    return base


def _candidate_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.name in {"Dockerfile", "docker-compose.yml", "README.md", "package.json", "pyproject.toml"}:
            out.append(path)
        elif path.suffix.lower() in SCAN_EXTENSIONS:
            out.append(path)
        if len(out) >= 250:
            break
    return out


def _is_skipped_path(path: str) -> bool:
    return any(part in SKIP_DIRS for part in Path(path).parts)


def _is_scannable_path(path: str) -> bool:
    p = Path(path)
    return p.name in {"Dockerfile", "docker-compose.yml", "README.md", "package.json", "pyproject.toml"} or p.suffix.lower() in SCAN_EXTENSIONS


def score_file(path: Path, keywords: set[str]) -> int:
    name = str(path).lower()
    score = sum(3 for k in keywords if k in name)
    if path.name.lower() in {"readme.md", "package.json", "pyproject.toml", "dockerfile", "docker-compose.yml"}:
        score += 4
    text = _safe_read(path, limit=3000) or ""
    lower = text.lower()
    score += sum(1 for k in keywords if k in lower)
    return score


def score_github_path(path: str, keywords: set[str], client: ContentsClient, branch: str) -> int:
    name = path.lower()
    score = sum(3 for k in keywords if k in name)
    if Path(path).name.lower() in {"readme.md", "package.json", "pyproject.toml", "dockerfile", "docker-compose.yml"}:
        score += 4
    text = (client.get_text_file(path=path, branch=branch) or "")[:3000].lower()
    score += sum(1 for k in keywords if k in text)
    return score


def _safe_read(path: Path, *, limit: int = 20_000) -> str | None:
    try:
        if path.stat().st_size > 250_000:
            return None
        return path.read_text(errors="ignore")[:limit]
    except OSError:
        return None


def _best_snippet(text: str, keywords: set[str]) -> str | None:
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        lower = line.lower()
        if any(k in lower for k in keywords):
            start = max(0, idx - 2)
            end = min(len(lines), idx + 5)
            return "\n".join(lines[start:end])
    return None
