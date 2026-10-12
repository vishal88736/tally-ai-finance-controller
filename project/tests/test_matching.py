"""
Core matching engine tests. Run with: pytest tests/ -v
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "model"))

from models.schemas import Invoice, Settlement, BankTransaction, ConfidenceTier, ExceptionCategory
from matching.engine import run_reconciliation


def _inv(record_id, ref, amount, date="2026-08-01", merchant="M1", customer="C1"):
    return Invoice(record_id=record_id, reference=ref, amount=amount, date=date, merchant_id=merchant, customer_id=customer)


def _settle(record_id, ref, gross, fee=None, tax=None, net=None, date="2026-08-02", merchant="M1"):
    fee = fee if fee is not None else round(gross * 0.02, 2)
    tax = tax if tax is not None else round(fee * 0.18, 2)
    net = net if net is not None else round(gross - fee - tax, 2)
    return Settlement(record_id=record_id, reference=ref, payment_reference=ref, gross_amount=gross,
                       fee=fee, tax=tax, net_amount=net, settlement_date=date, date=date, merchant_id=merchant)


def _bank(record_id, ref, amount, date="2026-08-02"):
    return BankTransaction(record_id=record_id, reference=ref, amount=amount, date=date,
                            description=f"NEFT/{ref}/Test Merchant")


def test_exact_match_auto_reconciles():
    inv = _inv("INV-1", "ORD1", 10000.0)
    settle = _settle("SET-1", "ORD1", 10000.0)
    bank = _bank("BANK-1", "ORD1", settle.net_amount)
    result = run_reconciliation([inv], [settle], [bank])
    txn = result["transactions"][0]
    assert txn.is_fully_reconciled
    assert txn.confidence_tier == ConfidenceTier.AUTO_RECONCILE
    assert len(result["exceptions"]) == 0


def test_fuzzy_reference_still_matches():
    """A slightly re-keyed reference (one dropped character) should still
    be found via amount+date, landing in a lower confidence tier rather
    than being lost entirely."""
    inv = _inv("INV-1", "ORD12345", 5000.0)
    settle = _settle("SET-1", "ORD1235", 5000.0)  # one digit dropped
    bank = _bank("BANK-1", "ORD1235", settle.net_amount)
    result = run_reconciliation([inv], [settle], [bank])
    txn = result["transactions"][0]
    assert txn.bank_transaction is not None, "fuzzy reference match should still find the candidate"
    assert txn.evidence.reference_score < 100


def test_amount_mismatch_flagged():
    inv = _inv("INV-1", "ORD1", 10000.0)
    settle = _settle("SET-1", "ORD1", 10000.0)
    bank = _bank("BANK-1", "ORD1", settle.net_amount - 500)  # unexplained shortfall
    result = run_reconciliation([inv], [settle], [bank])
    assert len(result["exceptions"]) == 1
    assert result["exceptions"][0].category == ExceptionCategory.AMOUNT_MISMATCH


def test_date_mismatch_flagged():
    inv = _inv("INV-1", "ORD1", 10000.0, date="2026-08-01")
    settle = _settle("SET-1", "ORD1", 10000.0, date="2026-08-01")
    bank = _bank("BANK-1", "ORD1", settle.net_amount, date="2026-08-15")  # 14 days later
    result = run_reconciliation([inv], [settle], [bank])
    assert len(result["exceptions"]) == 1
    assert result["exceptions"][0].category == ExceptionCategory.DATE_MISMATCH


def test_missing_bank_transaction_flagged():
    inv = _inv("INV-1", "ORD1", 10000.0)
    settle = _settle("SET-1", "ORD1", 10000.0)
    result = run_reconciliation([inv], [settle], [])
    assert len(result["exceptions"]) == 1
    assert result["exceptions"][0].category == ExceptionCategory.MISSING_BANK_TRANSACTION


def test_missing_settlement_but_bank_present():
    inv = _inv("INV-1", "ORD1", 10000.0)
    bank = _bank("BANK-1", "ORD1", 10000.0)
    result = run_reconciliation([inv], [], [bank])
    txn = result["transactions"][0]
    assert txn.bank_transaction is not None
    # direct invoice->bank match with no settlement in between


def test_fee_discrepancy_flagged():
    inv = _inv("INV-1", "ORD1", 10000.0)
    settle = _settle("SET-1", "ORD1", 10000.0, fee=450.0, tax=81.0, net=9469.0)  # fee way above 2% policy
    bank = _bank("BANK-1", "ORD1", 9469.0)
    result = run_reconciliation([inv], [settle], [bank])
    assert len(result["exceptions"]) == 1
    assert result["exceptions"][0].category == ExceptionCategory.FEE_DISCREPANCY


def test_duplicate_transaction_detected():
    """Two invoices with the SAME reference and amount => a true duplicate,
    not just a coincidental amount match."""
    inv_a = _inv("INV-A", "ORD1", 10000.0)
    inv_b = _inv("INV-B", "ORD1", 10000.0)  # same reference+amount = duplicate
    settle = _settle("SET-1", "ORD1", 10000.0)
    bank = _bank("BANK-1", "ORD1", settle.net_amount)
    result = run_reconciliation([inv_a, inv_b], [settle], [bank])
    categories = {e.category for e in result["exceptions"]}
    assert ExceptionCategory.DUPLICATE_TRANSACTION in categories


def test_ambiguous_match_when_reference_unrelated():
    inv = _inv("INV-1", "ORD1", 10000.0)
    bank = _bank("BANK-1", "ORDUNRELATED", 10000.0)  # amount coincidence only
    result = run_reconciliation([inv], [], [bank])
    # should not be blindly auto-reconciled purely on amount coincidence
    txn = result["transactions"][0]
    assert txn.confidence_tier != ConfidenceTier.AUTO_RECONCILE


# ------------------------------------------------------------------
# THE regression test explicitly required by the brief.
# ------------------------------------------------------------------
def test_one_to_one_constraint_prevents_double_reconciliation():
    """
    Invoice A = 10,000
    Invoice B = 10,000
    Bank A = 10,000
    The system must NOT reconcile both invoices to the same bank transaction.
    """
    inv_a = _inv("INV-A", "ORD-AAA", 10000.0)
    inv_b = _inv("INV-B", "ORD-BBB", 10000.0)  # different reference, same amount -- NOT a duplicate
    bank_a = _bank("BANK-A", "ORD-AAA", 10000.0)

    result = run_reconciliation([inv_a, inv_b], [], [bank_a])

    matched_banks = [t.bank_transaction.record_id for t in result["transactions"] if t.bank_transaction]
    assert len(matched_banks) == 1, f"Bank transaction was claimed by {len(matched_banks)} invoices, expected exactly 1"

    # exactly one invoice should be unresolved as a result
    unresolved = [t for t in result["transactions"] if t.bank_transaction is None]
    assert len(unresolved) == 1


def test_duplicate_reconciliation_prevention_at_scale():
    """Same regression, but with 3 invoices all claiming interest in 1 bank
    transaction via varying signal strength -- still only one winner."""
    inv_a = _inv("INV-A", "ORD-AAA", 10000.0, customer="C1")
    inv_b = _inv("INV-B", "ORD-BBB", 10000.0, customer="C2")
    inv_c = _inv("INV-C", "ORD-CCC", 10000.0, customer="C3")
    bank_a = _bank("BANK-A", "ORD-AAA", 10000.0)  # only A has a real reference match

    result = run_reconciliation([inv_a, inv_b, inv_c], [], [bank_a])
    matched_banks = [t.bank_transaction.record_id for t in result["transactions"] if t.bank_transaction]
    assert len(matched_banks) == 1
    # the winner should be the one with the actual reference match
    winner = next(t for t in result["transactions"] if t.bank_transaction is not None)
    assert winner.invoice.record_id == "INV-A"
