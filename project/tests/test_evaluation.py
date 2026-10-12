import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "model"))

from models.schemas import BankTransaction, ConfidenceTier, Invoice, MatchEvidence, Transaction
from evaluation.ground_truth import GroundTruthEntry, compare_against_ground_truth
from evaluation.metrics import compute_metrics


def _reconciled_txn(inv_id, bank_id):
    return Transaction(
        transaction_id=f"TXN-{inv_id}",
        invoice=Invoice(record_id=inv_id, amount=1000.0),
        bank_transaction=BankTransaction(record_id=bank_id, amount=1000.0),
        confidence=97.0,
        confidence_tier=ConfidenceTier.AUTO_RECONCILE,
        is_fully_reconciled=True,
    )


def _unreconciled_txn(inv_id):
    return Transaction(
        transaction_id=f"TXN-{inv_id}",
        invoice=Invoice(record_id=inv_id, amount=1000.0),
        confidence=0.0,
        confidence_tier=ConfidenceTier.UNRESOLVED,
        is_fully_reconciled=False,
    )


def test_true_positive_counted_correctly():
    gt = {"INV-1": GroundTruthEntry("INV-1", "BANK-1", None, "matched")}
    txns = [_reconciled_txn("INV-1", "BANK-1")]
    cmp = compare_against_ground_truth(txns, gt)
    assert cmp.true_positives == 1
    assert cmp.false_positives == 0


def test_wrong_link_counts_as_false_positive_not_true_positive():
    """Reconciling to the WRONG bank transaction must count against
    precision, even though 'something' got matched -- this is exactly the
    kind of error the constrained-assignment engine exists to prevent, and
    the metric must be able to detect it if it ever slips through."""
    gt = {"INV-1": GroundTruthEntry("INV-1", "BANK-CORRECT", None, "matched")}
    txns = [_reconciled_txn("INV-1", "BANK-WRONG")]
    cmp = compare_against_ground_truth(txns, gt)
    assert cmp.true_positives == 0
    assert cmp.false_positives == 1
    assert "INV-1" in cmp.mismatched_links


def test_false_negative_when_should_have_matched():
    gt = {"INV-1": GroundTruthEntry("INV-1", "BANK-1", None, "matched")}
    txns = [_unreconciled_txn("INV-1")]
    cmp = compare_against_ground_truth(txns, gt)
    assert cmp.false_negatives == 1


def test_true_negative_when_correctly_left_unresolved():
    gt = {"INV-1": GroundTruthEntry("INV-1", None, None, "missing_bank_transaction")}
    txns = [_unreconciled_txn("INV-1")]
    cmp = compare_against_ground_truth(txns, gt)
    assert cmp.true_negatives == 1


def test_precision_recall_f1_computed_correctly():
    gt = {
        "INV-1": GroundTruthEntry("INV-1", "BANK-1", None, "matched"),
        "INV-2": GroundTruthEntry("INV-2", "BANK-2", None, "matched"),
        "INV-3": GroundTruthEntry("INV-3", None, None, "missing_bank_transaction"),
    }
    txns = [
        _reconciled_txn("INV-1", "BANK-1"),   # TP
        _unreconciled_txn("INV-2"),            # FN (should have matched, didn't)
        _unreconciled_txn("INV-3"),            # TN (correctly unresolved)
    ]
    metrics = compute_metrics(txns, [], ground_truth=gt, total_records=3)
    assert metrics.precision == 1.0     # 1 TP / (1 TP + 0 FP)
    assert metrics.recall == 0.5        # 1 TP / (1 TP + 1 FN)
    assert round(metrics.f1, 4) == round(2 * 1.0 * 0.5 / 1.5, 4)


def test_match_rate_is_not_naive_matched_over_total():
    """match_rate must reflect actually-reconciled transactions, and must
    be independently reportable alongside (not instead of) precision/recall."""
    txns = [_reconciled_txn("INV-1", "BANK-1"), _unreconciled_txn("INV-2")]
    metrics = compute_metrics(txns, [])
    assert metrics.match_rate == 50.0
