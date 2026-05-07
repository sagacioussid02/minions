"""Audit & Challenge layer — independent review reporting to the operator.

Devil's Advocate (§9.3) lives in ``crews/devils_advocate.py`` because it's
invoked synchronously from the approval flow. The Code Auditor (§9.4),
Process Auditor (§9.5), and Cost Auditor (§9.5) sample completed work
asynchronously and write findings here.

The findings store is the single source of truth for the dashboard's audit
tile and the Friday digest's "open findings" section.
"""

from minions.audit.runner import AuditRunOutcome, AuditRunReport, audit_after_sync
from minions.audit.store import AuditFindingStore

__all__ = [
    "AuditFindingStore",
    "AuditRunOutcome",
    "AuditRunReport",
    "audit_after_sync",
]
