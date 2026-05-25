"""Per-project dossier storage and lifecycle.

The dossier is the agent-authored counterpart to the operator-authored
``projects/<name>.yaml`` manifest. See
``openspec/changes/project-dossier-and-grounded-planning/`` for the contract.
"""

from minions.dossiers.store import DossierDraftStore
from minions.dossiers.store_factory import DossierStoreLike, make_dossier_store

__all__ = ["DossierDraftStore", "DossierStoreLike", "make_dossier_store"]
