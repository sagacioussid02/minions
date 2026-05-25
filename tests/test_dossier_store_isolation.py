"""Guard: dossier stores are constructed only via the factory.

Per CLAUDE.md §7 and the project-dossier-and-grounded-planning spec, every
domain store has exactly one public construction surface: its factory module.
Direct instantiation of ``DossierDraftStore`` or ``PostgresDossierDraftStore``
from anywhere outside ``src/minions/dossiers/`` would defeat the env-driven
backend swap and is a structural regression worth failing the suite over.

Test imports / re-exports are exempt: importing the class for type-checking
or re-export in ``__init__`` does not bypass the factory.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "minions"
ALLOWED_DIR = SRC_ROOT / "dossiers"

CALL_PATTERNS = [
    re.compile(r"\bDossierDraftStore\s*\("),
    re.compile(r"\bPostgresDossierDraftStore\s*\("),
]


def test_no_direct_dossier_store_instantiation_outside_package() -> None:
    offenders: list[str] = []
    for path in SRC_ROOT.rglob("*.py"):
        if ALLOWED_DIR in path.parents or path == ALLOWED_DIR:
            continue
        text = path.read_text()
        for pat in CALL_PATTERNS:
            for match in pat.finditer(text):
                line_no = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(SRC_ROOT)}:{line_no}")
    assert not offenders, (
        "Direct dossier-store instantiation outside src/minions/dossiers/ "
        "bypasses make_dossier_store and breaks the env-driven backend swap. "
        f"Offenders: {offenders}"
    )
