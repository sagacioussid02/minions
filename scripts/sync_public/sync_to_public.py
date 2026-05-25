"""Sync minions-org → minions-public with scrubbing.

Reads ``scrub_rules.yaml`` next to this file. Walks the source tree's
``include_roots`` + ``include_files``, applies the rules, and writes
to a target tree (typically the ``minions-public`` checkout).

Dry-run by default. Use ``--apply`` to actually mutate the target.

Usage::

    python scripts/sync_public/sync_to_public.py \\
        --source /Users/siddharthshankar/workspace/AI/CLAUDE/minions \\
        --target /Users/siddharthshankar/workspace/AI/CLAUDE/minions-public

    python scripts/sync_public/sync_to_public.py --apply ...

Safety:

* Never touches anything outside the configured ``include_roots`` +
  ``include_files``.
* Never overwrites paths in ``preserve_in_target``.
* Never copies paths in ``drop_paths``.
* All path comparisons are POSIX-style relative paths.
"""

from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Rule + plan modeling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextRule:
    pattern: re.Pattern[str]
    replacement: str
    raw_find: str

    def apply(self, text: str) -> tuple[str, int]:
        new_text, n = self.pattern.subn(self.replacement, text)
        return new_text, n


@dataclass(frozen=True)
class StructuralPatch:
    find: str
    replace: str
    requires_import: str | None = None


@dataclass
class Rules:
    text_rules: list[TextRule]
    scrub_files: set[str]
    drop_paths: set[str]
    preserve_in_target: set[str]
    include_roots: list[str]
    include_files: list[str]
    structural_patches: dict[str, list[StructuralPatch]]


@dataclass
class FileAction:
    rel_path: str  # POSIX-style
    kind: str  # "copy", "scrub", "skip-drop", "skip-preserve", "patch+scrub"
    bytes_in: int = 0
    bytes_out: int = 0
    text_subs: dict[str, int] | None = None  # rule-name → count
    structural_subs: int = 0
    note: str | None = None


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------


def load_rules(rules_path: Path) -> Rules:
    raw: dict[str, Any] = yaml.safe_load(rules_path.read_text())

    text_rules: list[TextRule] = []
    for entry in raw.get("text_replacements", []):
        find = entry["find"]
        flags = re.IGNORECASE if entry.get("case_insensitive") else 0
        pattern = re.compile(re.escape(find), flags=flags)
        text_rules.append(
            TextRule(
                pattern=pattern,
                replacement=entry["replace"],
                raw_find=find,
            )
        )

    patches: dict[str, list[StructuralPatch]] = {}
    for path, entries in (raw.get("structural_patches") or {}).items():
        patches[path] = [
            StructuralPatch(
                find=e["find"],
                replace=e["replace"],
                requires_import=e.get("requires_import"),
            )
            for e in entries
        ]

    return Rules(
        text_rules=text_rules,
        scrub_files=set(raw.get("scrub_files") or []),
        drop_paths=set(raw.get("drop_paths") or []),
        preserve_in_target=set(raw.get("preserve_in_target") or []),
        include_roots=list(raw.get("include_roots") or []),
        include_files=list(raw.get("include_files") or []),
        structural_patches=patches,
    )


# ---------------------------------------------------------------------------
# Planning — walk source tree, decide per-file action
# ---------------------------------------------------------------------------


_PYCACHE_PARTS = frozenset(
    {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".venv", "node_modules", ".next"}
)


def _is_build_artefact(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in _PYCACHE_PARTS for p in parts) or rel.endswith(".pyc")


def _matches_prefix(rel: str, prefixes: set[str]) -> bool:
    """True if rel == p or rel starts with p + '/'."""
    return any(rel == p or rel.startswith(p + "/") for p in prefixes)


def walk_sources(source: Path, rules: Rules) -> list[Path]:
    """Return every file under ``source`` covered by include_roots/files."""
    files: list[Path] = []
    for root in rules.include_roots:
        base = source / root
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file():
                files.append(p)
    for rel in rules.include_files:
        p = source / rel
        if p.is_file():
            files.append(p)
    # Stable order for reproducible plans
    return sorted(set(files))


def plan_file(src: Path, source_root: Path, rules: Rules) -> FileAction:
    rel = src.relative_to(source_root).as_posix()
    if _is_build_artefact(rel):
        return FileAction(rel_path=rel, kind="skip-build-artefact")
    if _matches_prefix(rel, rules.drop_paths):
        return FileAction(rel_path=rel, kind="skip-drop")
    if _matches_prefix(rel, rules.preserve_in_target):
        return FileAction(rel_path=rel, kind="skip-preserve")
    if rel in rules.structural_patches:
        return FileAction(rel_path=rel, kind="patch+scrub")
    # Scrub every text file under the include roots. The text-replacement
    # rules are narrow enough (operator email + 5 unique project names)
    # that false positives don't realistically occur — verified by grep
    # 2026-05-25. The explicit ``scrub_files`` list is no longer used as a
    # gate; kept in the YAML for documentation.
    return FileAction(rel_path=rel, kind="scrub")


# ---------------------------------------------------------------------------
# Execution — scrub bytes, write to target
# ---------------------------------------------------------------------------


def apply_text_rules(text: str, rules: Rules) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    for rule in rules.text_rules:
        text, n = rule.apply(text)
        if n:
            counts[rule.raw_find] = counts.get(rule.raw_find, 0) + n
    return text, counts


def _insert_import(text: str, module: str) -> str:
    """Insert ``import <module>`` after the module docstring and any
    ``from __future__`` imports, without disturbing them.

    Idempotent: returns ``text`` unchanged if the import is already present.
    """
    imp_line = f"import {module}"
    # Already imported?
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == imp_line or stripped.startswith(f"{imp_line} "):
            return text

    lines = text.splitlines(keepends=True)
    i = 0
    # Skip an optional module docstring (single- or triple-quoted, possibly
    # spanning multiple lines).
    if i < len(lines):
        first = lines[i].lstrip()
        for quote in ('"""', "'''"):
            if first.startswith(quote):
                # Single-line docstring on this line?
                rest = first[3:]
                if quote in rest:
                    i += 1
                else:
                    i += 1
                    while i < len(lines) and quote not in lines[i]:
                        i += 1
                    if i < len(lines):
                        i += 1
                break
    # Skip blank lines after the docstring.
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    # Skip ``from __future__`` imports (and blanks between them).
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("from __future__"):
            i += 1
            while i < len(lines) and lines[i].strip() == "":
                i += 1
            continue
        break
    lines.insert(i, f"{imp_line}\n")
    # Ensure a blank line after our insertion if the next line is code.
    if i + 1 < len(lines) and lines[i + 1].strip() != "":
        lines.insert(i + 1, "\n")
    return "".join(lines)


def apply_structural_patches(
    text: str,
    patches: list[StructuralPatch],
) -> tuple[str, int]:
    n_total = 0
    for patch in patches:
        if patch.find in text:
            text = text.replace(patch.find, patch.replace)
            n_total += 1
            if patch.requires_import:
                text = _insert_import(text, patch.requires_import)
    return text, n_total


def _read_text(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None  # binary file — caller falls back to byte copy


def execute_action(
    src: Path,
    source_root: Path,
    target_root: Path,
    action: FileAction,
    rules: Rules,
    *,
    apply: bool,
) -> None:
    if action.kind.startswith("skip"):
        return
    dst = target_root / action.rel_path

    if action.kind == "copy":
        action.bytes_in = src.stat().st_size
        action.bytes_out = action.bytes_in
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return

    text = _read_text(src)
    if text is None:
        # Binary file flagged for scrub — fall back to copy with a warning.
        action.note = "binary file flagged for scrub; copied verbatim"
        action.bytes_in = src.stat().st_size
        action.bytes_out = action.bytes_in
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return

    action.bytes_in = len(text.encode("utf-8"))
    new_text, counts = apply_text_rules(text, rules)
    action.text_subs = counts or None

    if action.kind == "patch+scrub":
        patches = rules.structural_patches.get(action.rel_path, [])
        new_text, n_struct = apply_structural_patches(new_text, patches)
        action.structural_subs = n_struct

    action.bytes_out = len(new_text.encode("utf-8"))

    if apply:
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def summarize(actions: list[FileAction]) -> str:
    by_kind: dict[str, int] = {}
    total_subs: dict[str, int] = {}
    for a in actions:
        by_kind[a.kind] = by_kind.get(a.kind, 0) + 1
        for rule, n in (a.text_subs or {}).items():
            total_subs[rule] = total_subs.get(rule, 0) + n
    lines = ["", "=== summary ==="]
    for kind, n in sorted(by_kind.items()):
        lines.append(f"  {kind:25s} {n}")
    if total_subs:
        lines.append("")
        lines.append("=== text substitutions (by rule) ===")
        for rule, n in sorted(total_subs.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {rule:30s} {n}")
    return "\n".join(lines)


def per_file_report(actions: list[FileAction], *, show_copies: bool) -> str:
    lines: list[str] = []
    for a in actions:
        if a.kind == "copy" and not show_copies:
            continue
        if a.kind.startswith("skip"):
            lines.append(f"  {a.kind:25s} {a.rel_path}")
            continue
        marks: list[str] = []
        if a.text_subs:
            n = sum(a.text_subs.values())
            marks.append(f"text x{n}")
        if a.structural_subs:
            marks.append(f"struct x{a.structural_subs}")
        if a.note:
            marks.append(a.note)
        suffix = f"  [{'; '.join(marks)}]" if marks else ""
        lines.append(f"  {a.kind:25s} {a.rel_path}{suffix}")
    return "\n".join(lines)


def diff_preview(
    src_root: Path,
    dst_root: Path,
    action: FileAction,
    context: int = 2,
) -> str:
    """Unified diff of the scrubbed output vs. the current target file."""
    src = src_root / action.rel_path
    dst = dst_root / action.rel_path
    src_text = _read_text(src) or ""
    dst_text = _read_text(dst) if dst.exists() else ""

    # Replay the scrub in-memory to get the new content without writing.
    rules_path = Path(__file__).parent / "scrub_rules.yaml"
    rules = load_rules(rules_path)
    new_text, _ = apply_text_rules(src_text, rules)
    if action.kind == "patch+scrub":
        patches = rules.structural_patches.get(action.rel_path, [])
        new_text, _ = apply_structural_patches(new_text, patches)

    diff = difflib.unified_diff(
        (dst_text or "").splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=f"a/{action.rel_path}",
        tofile=f"b/{action.rel_path}",
        n=context,
    )
    return "".join(diff)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--source", type=Path, required=True, help="Source tree (minions-org checkout)"
    )
    parser.add_argument(
        "--target", type=Path, required=True, help="Target tree (minions-public checkout)"
    )
    parser.add_argument(
        "--rules",
        type=Path,
        default=Path(__file__).parent / "scrub_rules.yaml",
        help="Rules YAML; defaults to the file next to this script",
    )
    parser.add_argument("--apply", action="store_true", help="Mutate the target. Omit to dry-run.")
    parser.add_argument(
        "--show-copies",
        action="store_true",
        help="Include verbatim copy actions in per-file output",
    )
    parser.add_argument(
        "--diff", action="store_true", help="Print unified diffs for scrub/patch actions"
    )
    args = parser.parse_args(argv)

    if not args.source.is_dir():
        parser.error(f"--source not a directory: {args.source}")
    if not args.target.is_dir():
        parser.error(f"--target not a directory: {args.target}")

    rules = load_rules(args.rules)
    sources = walk_sources(args.source, rules)

    actions: list[FileAction] = []
    for src in sources:
        action = plan_file(src, args.source, rules)
        execute_action(
            src,
            args.source,
            args.target,
            action,
            rules,
            apply=args.apply,
        )
        actions.append(action)

    print(per_file_report(actions, show_copies=args.show_copies))

    if args.apply:
        # Structural patches insert imports that may not match the project's
        # existing import-sort layout (e.g. dashboard/app.py grows an
        # `import os` that ruff's I001 wants grouped differently). Run
        # ruff --fix + ruff format on the target to settle layout so the
        # target's CI is clean immediately after sync.
        import subprocess

        targets = [str(args.target / r) for r in rules.include_roots if (args.target / r).exists()]
        if targets:
            subprocess.run(
                ["ruff", "check", "--fix", "-q", *targets],
                check=False,
                cwd=args.target,
            )
            subprocess.run(
                ["ruff", "format", "-q", *targets],
                check=False,
                cwd=args.target,
            )

    if args.diff:
        print("\n=== diffs (scrub/patch only) ===")
        for a in actions:
            if a.kind in {"scrub", "patch+scrub"}:
                d = diff_preview(args.source, args.target, a)
                if d:
                    print(d)

    print(summarize(actions))
    mode = "APPLIED" if args.apply else "DRY-RUN (no files written)"
    print(f"\nmode: {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
