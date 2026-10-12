"""
Turns engine output (+ optional ground truth) into a ReconciliationMetrics
object. Every number here is computed from the actual run -- nothing is
hard-coded or estimated.
"""

from typing import Dict, List, Optional

from evaluation.ground_truth import GroundTruthEntry, compare_against_ground_truth
from models.schemas import ConfidenceTier, ReconciliationException, ReconciliationMetrics, Transaction


def compute_metrics(
    transactions: List[Transaction],
    exceptions: List[ReconciliationException],
    ground_truth: Optional[Dict[str, GroundTruthEntry]] = None,
    processing_time_seconds: float = 0.0,
    deterministic_time_seconds: float = 0.0,
    llm_time_seconds: float = 0.0,
    llm_calls: int = 0,
    total_records: Optional[int] = None,
) -> ReconciliationMetrics:
    total_transactions = len(transactions)
    auto_resolved = sum(1 for t in transactions if t.confidence_tier == ConfidenceTier.AUTO_RECONCILE)
    ai_investigation = sum(1 for t in transactions if t.confidence_tier == ConfidenceTier.AI_INVESTIGATION)
    human_review = sum(1 for t in transactions if t.confidence_tier == ConfidenceTier.HUMAN_REVIEW)
    unresolved = sum(1 for t in transactions if t.confidence_tier == ConfidenceTier.UNRESOLVED)

    reconciled_value = sum(
        (t.invoice.amount or 0.0) for t in transactions if t.is_fully_reconciled
    )
    unreconciled_value = sum(
        (t.invoice.amount or 0.0) for t in transactions if not t.is_fully_reconciled
    )

    match_rate = round((sum(1 for t in transactions if t.is_fully_reconciled) / total_transactions) * 100, 2) if total_transactions else 0.0
    auto_resolution_rate = round((auto_resolved / total_transactions) * 100, 2) if total_transactions else 0.0

    precision = recall = f1 = 0.0
    false_positives = false_negatives = 0
    if ground_truth:
        cmp = compare_against_ground_truth(transactions, ground_truth)
        precision = round(cmp.true_positives / (cmp.true_positives + cmp.false_positives), 4) if (cmp.true_positives + cmp.false_positives) else 0.0
        recall = round(cmp.true_positives / (cmp.true_positives + cmp.false_negatives), 4) if (cmp.true_positives + cmp.false_negatives) else 0.0
        f1 = round((2 * precision * recall) / (precision + recall), 4) if (precision + recall) else 0.0
        false_positives = cmp.false_positives
        false_negatives = cmp.false_negatives

    records = total_records if total_records is not None else total_transactions
    throughput = round(records / processing_time_seconds, 2) if processing_time_seconds > 0 else 0.0

    return ReconciliationMetrics(
        total_records=records,
        total_transactions=total_transactions,
        match_rate=match_rate,
        precision=precision,
        recall=recall,
        f1=f1,
        false_positives=false_positives,
        false_negatives=false_negatives,
        auto_resolved=auto_resolved,
        human_review=human_review + ai_investigation,
        unresolved=unresolved,
        exception_count=len(exceptions),
        auto_resolution_rate=auto_resolution_rate,
        reconciled_value=round(reconciled_value, 2),
        unreconciled_value=round(unreconciled_value, 2),
        processing_time_seconds=round(processing_time_seconds, 4),
        deterministic_time_seconds=round(deterministic_time_seconds, 4),
        llm_time_seconds=round(llm_time_seconds, 4),
        throughput_records_per_sec=throughput,
        llm_calls=llm_calls,
    )
