"""
Audit trail. Every automated decision -- auto-reconcile, AI-investigated
resolution, or escalation -- gets one AuditEntry, append-only, so a
controller can always answer "why did the system decide that?" after the
fact.
"""

from typing import List

from models.schemas import AuditEntry
from services.store import ReconciliationRun


def log_decision(
    run: ReconciliationRun,
    transaction_id: str,
    decision: str,
    confidence: float,
    evidence: List[str],
    policy_used: List[str],
    reason: str,
) -> AuditEntry:
    entry = AuditEntry(
        transaction_id=transaction_id,
        decision=decision,
        confidence=confidence,
        evidence=evidence,
        policy_used=policy_used,
        reason=reason,
    )
    run.audit_log.append(entry)
    return entry


def get_audit_history(run: ReconciliationRun, transaction_id: str = None) -> List[AuditEntry]:
    if transaction_id is None:
        return run.audit_log
    return [e for e in run.audit_log if e.transaction_id == transaction_id]
