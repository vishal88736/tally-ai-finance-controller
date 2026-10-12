"""
Ground-truth loading and label comparison.

Ground truth format (per invoice record_id):
    {
      "INV-0001": {"expected_bank_transaction": "UTR-0001", "expected_settlement": "SET-0001", "status": "matched"},
      "INV-0002": {"expected_bank_transaction": null, "expected_settlement": null, "status": "missing_bank_transaction"}
    }

This module is the ONLY place that knows what "correct" means for a given
synthetic batch. The engine itself never sees ground truth -- it only sees
raw records -- which is what makes the resulting precision/recall numbers
meaningful rather than circular.
"""

import json
from dataclasses import dataclass
from typing import Dict, List, Optional

from models.schemas import Transaction


@dataclass
class GroundTruthEntry:
    invoice_id: str
    expected_bank_transaction: Optional[str]
    expected_settlement: Optional[str]
    status: str  # "matched" | "missing_bank_transaction" | "missing_settlement" | ...


def load_ground_truth(path: str) -> Dict[str, GroundTruthEntry]:
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {
        invoice_id: GroundTruthEntry(
            invoice_id=invoice_id,
            expected_bank_transaction=entry.get("expected_bank_transaction"),
            expected_settlement=entry.get("expected_settlement"),
            status=entry.get("status", "unknown"),
        )
        for invoice_id, entry in raw.items()
    }


@dataclass
class LabelComparison:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0
    true_negatives: int = 0
    mismatched_links: List[str] = None  # invoice ids reconciled, but to the WRONG record

    def __post_init__(self):
        if self.mismatched_links is None:
            self.mismatched_links = []


def compare_against_ground_truth(
    transactions: List[Transaction], ground_truth: Dict[str, GroundTruthEntry]
) -> LabelComparison:
    """
    A transaction counts as a "positive prediction" when the engine marked
    it AUTO_RECONCILE-tier and assigned a bank transaction. It's a TRUE
    positive only if that assignment matches the ground-truth expected
    bank transaction id EXACTLY -- an invoice reconciled against the WRONG
    bank transaction (e.g. the double-claim bug this whole engine exists to
    prevent) counts as a false positive, not a true positive, even though
    "something" got matched.
    """
    result = LabelComparison()

    for txn in transactions:
        invoice_id = txn.invoice.record_id
        gt = ground_truth.get(invoice_id)
        if gt is None:
            continue  # no label available for this record; skip rather than guess

        predicted_positive = txn.is_fully_reconciled
        actual_positive = gt.status == "matched"

        predicted_bank_id = txn.bank_transaction.record_id if txn.bank_transaction else None

        if predicted_positive and actual_positive:
            if gt.expected_bank_transaction and predicted_bank_id != gt.expected_bank_transaction:
                result.false_positives += 1
                result.mismatched_links.append(invoice_id)
            else:
                result.true_positives += 1
        elif predicted_positive and not actual_positive:
            result.false_positives += 1
            result.mismatched_links.append(invoice_id)
        elif not predicted_positive and actual_positive:
            result.false_negatives += 1
        else:
            result.true_negatives += 1

    return result
