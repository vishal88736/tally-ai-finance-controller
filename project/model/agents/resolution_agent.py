"""
Resolution agent.

Deliberately NOT an LLM call. Once the investigation agent (or the rule
engine directly, for confidence tiers that never needed investigation) has
produced a classification, deciding WHICH action that maps to is a fixed,
auditable policy lookup -- there is no ambiguity left to reason about, so
adding a model call here would only add latency and a new failure mode.

This agent never touches an external system: every action is represented
inside this application only (see ResolutionAction.approved, which starts
False and is flipped by the human-approval endpoint).
"""

import uuid
from typing import List, Optional

from models.schemas import (
    ConfidenceTier,
    InvestigationResult,
    ReconciliationException,
    ResolutionAction,
    ResolutionActionType,
    Transaction,
)

_CLASSIFICATION_TO_ACTION = {
    "valid_fee_deduction": ResolutionActionType.MARK_AS_FEE_VARIANCE,
    "timing_difference": ResolutionActionType.MARK_AS_TIMING_DIFFERENCE,
    "tax_discrepancy": ResolutionActionType.MARK_AS_TAX_VARIANCE,
    "duplicate": ResolutionActionType.MERGE_DUPLICATE,
    "amount_mismatch": ResolutionActionType.REQUEST_HUMAN_REVIEW,
    "reference_mismatch": ResolutionActionType.AUTO_RECONCILE,
    "missing_record": ResolutionActionType.ESCALATE,
    "unknown": ResolutionActionType.ESCALATE,
}


def resolve_auto(txn: Transaction) -> ResolutionAction:
    """Path for AUTO_RECONCILE-tier transactions -- no exception, no
    investigation needed, but still fully audited."""
    return ResolutionAction(
        action_id=f"ACT-{uuid.uuid4().hex[:8]}",
        transaction_id=txn.transaction_id,
        exception_id=None,
        action_type=ResolutionActionType.AUTO_RECONCILE,
        reason="Invoice, settlement, and bank transaction agree within configured tolerance.",
        evidence=txn.evidence.notes,
        confidence=txn.confidence,
        policy_used=["reconciliation_policy"],
        approved=True,  # auto-reconcile at >=95% confidence does not require a human gate
    )


def resolve_from_investigation(
    txn: Transaction, exc: ReconciliationException, investigation: InvestigationResult
) -> ResolutionAction:
    if investigation.status == "ESCALATE":
        action_type = ResolutionActionType.ESCALATE
    else:
        action_type = _CLASSIFICATION_TO_ACTION.get(investigation.classification.value, ResolutionActionType.ESCALATE)

    # policy gate: even a "RESOLVE" classification only auto-applies if the
    # transaction's rule-based confidence was in the AI_INVESTIGATION band or
    # above; anything lower always requires a human, regardless of what the
    # model concluded, per reconciliation_policy.md's confidence tiers.
    needs_human = txn.confidence_tier in (ConfidenceTier.HUMAN_REVIEW, ConfidenceTier.UNRESOLVED)
    if needs_human and action_type != ResolutionActionType.ESCALATE:
        action_type = ResolutionActionType.REQUEST_HUMAN_REVIEW

    approved = action_type == ResolutionActionType.AUTO_RECONCILE and investigation.confidence >= 0.9

    return ResolutionAction(
        action_id=f"ACT-{uuid.uuid4().hex[:8]}",
        transaction_id=txn.transaction_id,
        exception_id=exc.exception_id,
        action_type=action_type,
        reason=investigation.reasoning,
        evidence=investigation.evidence_cited or exc.evidence,
        confidence=investigation.confidence,
        policy_used=investigation.policy_cited or ["exception_policy"],
        approved=approved,
    )


def resolve_without_investigation(txn: Transaction, exc: ReconciliationException) -> ResolutionAction:
    """Path for HUMAN_REVIEW / UNRESOLVED tiers that never reach the
    investigation agent at all (see graph/pipeline.py's confidence gate) --
    still gets a resolution record, just an honest one."""
    action_type = (
        ResolutionActionType.REQUEST_HUMAN_REVIEW
        if txn.confidence_tier == ConfidenceTier.HUMAN_REVIEW
        else ResolutionActionType.ESCALATE
    )
    return ResolutionAction(
        action_id=f"ACT-{uuid.uuid4().hex[:8]}",
        transaction_id=txn.transaction_id,
        exception_id=exc.exception_id,
        action_type=action_type,
        reason=f"Confidence {txn.confidence}% is below the AI-investigation threshold; requires human judgment.",
        evidence=exc.evidence,
        confidence=txn.confidence / 100.0,
        policy_used=["reconciliation_policy"],
        approved=False,
    )
