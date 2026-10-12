import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "model"))

from models.schemas import (
    BankTransaction, ConfidenceTier, ExceptionCategory, ExceptionSeverity,
    Invoice, InvestigationResult, ReconciliationException, Transaction,
)
from agents.investigation_agent import investigate_exception
from agents.resolution_agent import resolve_auto, resolve_from_investigation, resolve_without_investigation
from services.audit import get_audit_history, log_decision
from services.store import ReconciliationRun


def _txn(tier=ConfidenceTier.AI_INVESTIGATION, confidence=85.0):
    return Transaction(
        transaction_id="TXN-1",
        invoice=Invoice(record_id="INV-1", amount=1000.0, date="2026-08-01"),
        confidence=confidence,
        confidence_tier=tier,
    )


def _exc(category=ExceptionCategory.AMOUNT_MISMATCH):
    return ReconciliationException(
        exception_id="EXC-1", transaction_id="TXN-1", category=category,
        severity=ExceptionSeverity.HIGH, evidence=["test evidence"],
    )


def test_investigation_escalates_without_llm_provider():
    """No API key configured (as in CI/sandboxed environments) must never
    crash and must never fabricate a resolution -- it must escalate."""
    txn, exc = _txn(), _exc()
    result = investigate_exception(txn, exc, {})
    assert isinstance(result, InvestigationResult)
    assert result.status == "ESCALATE"
    assert result.confidence == 0.0


def test_low_confidence_resolve_is_downgraded_to_escalate(monkeypatch):
    """Even if a (mocked) model claims RESOLVE, low stated confidence must
    be overridden to ESCALATE -- the agent must not trust a hedging answer."""
    import agents.investigation_agent as inv_mod

    class FakeProvider:
        def complete_json(self, system, user, max_tokens=512):
            return {"classification": "timing_difference", "status": "RESOLVE", "confidence": 0.4, "reasoning": "uncertain"}

    monkeypatch.setattr(inv_mod, "get_provider", lambda: FakeProvider())
    result = investigate_exception(_txn(), _exc(), {})
    assert result.status == "ESCALATE"
    assert "auto-downgraded" in result.reasoning


def test_resolution_agent_never_auto_approves_below_ai_investigation_tier():
    txn = _txn(tier=ConfidenceTier.HUMAN_REVIEW, confidence=60.0)
    exc = _exc()
    resolution = resolve_without_investigation(txn, exc)
    assert resolution.approved is False
    assert resolution.action_type.value in ("REQUEST_HUMAN_REVIEW", "ESCALATE")


def test_auto_reconcile_resolution_is_pre_approved():
    txn = _txn(tier=ConfidenceTier.AUTO_RECONCILE, confidence=97.0)
    resolution = resolve_auto(txn)
    assert resolution.approved is True
    assert resolution.action_type.value == "AUTO_RECONCILE"


def test_every_exception_has_a_taxonomy_category():
    for category in ExceptionCategory:
        exc = _exc(category=category)
        assert exc.category in ExceptionCategory


def test_audit_log_records_every_decision():
    run = ReconciliationRun(run_id="test-run")
    log_decision(run, "TXN-1", "AUTO_RECONCILE", 97.0, ["amount_match"], ["reconciliation_policy"], "clean match")
    log_decision(run, "TXN-2", "ESCALATE", 40.0, [], [], "insufficient evidence")

    all_entries = get_audit_history(run)
    assert len(all_entries) == 2

    txn1_entries = get_audit_history(run, transaction_id="TXN-1")
    assert len(txn1_entries) == 1
    assert txn1_entries[0].decision == "AUTO_RECONCILE"
