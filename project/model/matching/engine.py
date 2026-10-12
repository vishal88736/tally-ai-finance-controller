"""
The reconciliation engine.

Builds the full transaction lifecycle (invoice -> settlement -> bank) using
constrained assignment rather than generic clustering, then categorizes
every incomplete or discrepant lifecycle into the exception taxonomy.

Chaining strategy (documented decision):
    1. Match invoices <-> settlements (one-to-one, constrained).
    2. For invoices that got a settlement, match that settlement <->
       bank transactions (one-to-one, constrained) using the settlement's
       own reference/net_amount.
    3. For invoices that did NOT get a settlement, attempt a direct
       invoice <-> bank match against whatever bank transactions remain
       unconsumed (handles "missing settlement record" scenarios where the
       payment still landed in the bank).
This two-stage chained bipartite matching approximates full 3-way lifecycle
matching without needing joint optimization across all three sources at
once, which would be substantially more expensive for marginal gain on a
dataset of this shape (each stage's candidate pool is already narrowed by
CandidateIndex, so cost stays close to linear in record count).
"""

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Tuple

from matching.assignment import ScoredCandidate, constrained_assign
from matching.candidate_generation import CandidateIndex, normalize_reference
from matching.scoring import DEFAULT_WEIGHTS, score_pair
from models.schemas import (
    BankTransaction,
    ConfidenceTier,
    ExceptionCategory,
    ExceptionSeverity,
    Invoice,
    MatchEvidence,
    ReconciliationException,
    Settlement,
    Transaction,
)

DEFAULT_ENGINE_CONFIG = {
    "amount_tolerance_abs": 1.0,
    "amount_tolerance_pct": 0.005,
    "date_window_days": 3,
    "weights": DEFAULT_WEIGHTS,
    "auto_reconcile_threshold": 95.0,
    "ai_investigation_threshold": 80.0,
    "human_review_threshold": 50.0,
    "fee_pct": 0.02,
    "gst_pct": 0.18,
}


def _tier_for_score(score: float, cfg: dict) -> ConfidenceTier:
    if score >= cfg["auto_reconcile_threshold"]:
        return ConfidenceTier.AUTO_RECONCILE
    if score >= cfg["ai_investigation_threshold"]:
        return ConfidenceTier.AI_INVESTIGATION
    if score >= cfg["human_review_threshold"]:
        return ConfidenceTier.HUMAN_REVIEW
    return ConfidenceTier.UNRESOLVED


def _left_query_amount(left: Any, right_pool_is_settlement: bool) -> Any:
    """Mirrors matching/scoring.py:_resolve_amounts so candidate lookup and
    scoring always agree on which amount field means what."""
    from models.schemas import Settlement

    if isinstance(left, Settlement):
        return left.net_amount if left.net_amount is not None else left.gross_amount
    return getattr(left, "amount", None)


def _build_candidates(lefts: List[Any], right_index: CandidateIndex, cfg: dict) -> List[ScoredCandidate]:
    candidates = []
    for left in lefts:
        left_ref = getattr(left, "reference", None) or getattr(left, "payment_reference", None)
        left_amount = _left_query_amount(left, right_pool_is_settlement=False)
        for right in right_index.candidates_for(left_ref, left_amount, cfg["amount_tolerance_pct"]):
            score = score_pair(
                left, right,
                weights=cfg["weights"],
                amount_tolerance_abs=cfg["amount_tolerance_abs"],
                amount_tolerance_pct=cfg["amount_tolerance_pct"],
                date_window_days=cfg["date_window_days"],
            )
            if score["weighted_score"] > 0:
                candidates.append(ScoredCandidate(left.record_id, right.record_id, left, right, score))
    return candidates


def _is_true_duplicate(a: Invoice, b: Invoice) -> bool:
    """A true duplicate shares BOTH reference and amount -- not just
    amount, which is the exact false-positive case the regression test
    guards against (same amount, different reference => NOT a duplicate,
    just a coincidence that must not cause double reconciliation)."""
    if not a.reference or not b.reference:
        return False
    return (
        normalize_reference(a.reference) == normalize_reference(b.reference)
        and a.amount is not None and b.amount is not None
        and abs(a.amount - b.amount) < 0.01
    )


def _expected_fee_tax(gross: float, cfg: dict) -> Tuple[float, float, float]:
    fee = round(gross * cfg["fee_pct"], 2)
    tax = round(fee * cfg["gst_pct"], 2)
    net = round(gross - fee - tax, 2)
    return fee, tax, net


def _categorize_exception(
    txn: Transaction, cfg: dict, duplicate_invoice_ids: set
) -> Tuple[ExceptionCategory, ExceptionSeverity, List[str]]:
    inv, settle, bank = txn.invoice, txn.settlement, txn.bank_transaction
    evidence: List[str] = []

    if inv.record_id in duplicate_invoice_ids:
        return ExceptionCategory.DUPLICATE_TRANSACTION, ExceptionSeverity.CRITICAL, [
            "invoice shares identical reference and amount with another invoice in this batch"
        ]

    if settle is None and bank is None:
        return ExceptionCategory.MISSING_BANK_TRANSACTION, (
            ExceptionSeverity.CRITICAL if (inv.amount or 0) > 50000 else ExceptionSeverity.HIGH
        ), ["no settlement or bank record found for this invoice"]

    if settle is None and bank is not None:
        return ExceptionCategory.MISSING_SETTLEMENT, ExceptionSeverity.MEDIUM, [
            f"bank transaction {bank.record_id} matched directly; no settlement record links them"
        ]

    if settle is not None and bank is None:
        return ExceptionCategory.MISSING_BANK_TRANSACTION, ExceptionSeverity.HIGH, [
            f"settlement {settle.record_id} found but no corresponding bank transaction landed"
        ]

    # both settlement and bank present -- check whether the numbers actually reconcile
    gross = inv.amount or settle.gross_amount or 0.0
    fee, tax, expected_net = _expected_fee_tax(gross, cfg)
    actual_net = bank.amount if bank.amount is not None else settle.net_amount
    settle_fee = settle.fee if settle.fee is not None else fee
    settle_tax = settle.tax if settle.tax is not None else tax

    if actual_net is not None and abs(actual_net - expected_net) > max(1.0, expected_net * 0.005):
        if abs(settle_fee - fee) > max(1.0, fee * 0.05):
            evidence.append(f"expected fee ₹{fee}, settlement shows ₹{settle_fee}")
            return ExceptionCategory.FEE_DISCREPANCY, ExceptionSeverity.MEDIUM, evidence
        if abs(settle_tax - tax) > max(1.0, tax * 0.05):
            evidence.append(f"expected tax ₹{tax}, settlement shows ₹{settle_tax}")
            return ExceptionCategory.TAX_DISCREPANCY, ExceptionSeverity.MEDIUM, evidence
        evidence.append(f"expected net ₹{expected_net}, bank shows ₹{actual_net}")
        return ExceptionCategory.AMOUNT_MISMATCH, ExceptionSeverity.HIGH, evidence

    if inv.date and bank.date:
        try:
            gap = abs((datetime.fromisoformat(str(bank.date)[:10]) - datetime.fromisoformat(str(inv.date)[:10])).days)
            if gap > cfg["date_window_days"]:
                evidence.append(f"settlement landed {gap} days after invoice date (policy window: {cfg['date_window_days']})")
                return ExceptionCategory.DATE_MISMATCH, ExceptionSeverity.MEDIUM, evidence
        except ValueError:
            pass

    if txn.evidence.reference_score < 90:
        evidence.append("reference strings differ beyond a clean match despite amount/date agreement")
        return ExceptionCategory.REFERENCE_MISMATCH, ExceptionSeverity.LOW, evidence

    evidence.append("no clear discrepancy identified; confidence below auto-reconcile threshold")
    return ExceptionCategory.AMBIGUOUS_MATCH, ExceptionSeverity.MEDIUM, evidence


def run_reconciliation(
    invoices: List[Invoice],
    settlements: List[Settlement],
    bank_transactions: List[BankTransaction],
    config: dict = None,
) -> Dict[str, Any]:
    """
    Runs the full multi-stage engine. Returns a dict with:
        transactions: List[Transaction]
        exceptions: List[ReconciliationException]
        stats: timing + assignment stats (no LLM calls happen in this function)
    """
    cfg = {**DEFAULT_ENGINE_CONFIG, **(config or {})}
    t0 = time.perf_counter()

    # --- duplicate pre-check (reference + amount identical => true duplicate) ---
    duplicate_ids = set()
    seen_keys: Dict[Tuple[str, float], str] = {}
    for inv in invoices:
        key = (normalize_reference(inv.reference or ""), round(inv.amount or 0, 2))
        if key[0] and key in seen_keys:
            duplicate_ids.add(inv.record_id)
        else:
            seen_keys[key] = inv.record_id

    # --- stage 1+2+3: invoice <-> settlement ---
    # Settlements are indexed by GROSS amount here because they're being
    # queried by invoices (which only know the gross amount). The same
    # settlement pool gets re-indexed by NET amount further down when it's
    # queried by bank transactions instead -- see _resolve_amounts.
    settlement_index = CandidateIndex(settlements, amount_attrs=("gross_amount", "amount"))
    inv_settle_candidates = _build_candidates(invoices, settlement_index, cfg)
    inv_settle_assignment = constrained_assign(
        inv_settle_candidates,
        all_left_ids=[i.record_id for i in invoices],
        all_right_ids=[s.record_id for s in settlements],
    )
    settle_by_id = {s.record_id: s for s in settlements}
    inv_by_id = {i.record_id: i for i in invoices}
    invoice_to_settlement = {c.left_id: c for c in inv_settle_assignment.assigned}

    # --- stage: settlement <-> bank (only for settlements that got claimed) ---
    claimed_settlements = [settle_by_id[c.right_id] for c in inv_settle_assignment.assigned]
    bank_index = CandidateIndex(bank_transactions)
    settle_bank_candidates = _build_candidates(claimed_settlements, bank_index, cfg)
    settle_bank_assignment = constrained_assign(
        settle_bank_candidates,
        all_left_ids=[s.record_id for s in claimed_settlements],
        all_right_ids=[b.record_id for b in bank_transactions],
    )
    bank_by_id = {b.record_id: b for b in bank_transactions}
    settlement_to_bank = {c.left_id: c for c in settle_bank_assignment.assigned}

    consumed_bank_ids = {c.right_id for c in settle_bank_assignment.assigned}
    remaining_banks = [b for b in bank_transactions if b.record_id not in consumed_bank_ids]

    # --- fallback: invoices with no settlement match go straight to bank ---
    unmatched_invoices = [inv_by_id[lid] for lid in inv_settle_assignment.unassigned_left_ids]
    remaining_bank_index = CandidateIndex(remaining_banks)
    inv_bank_candidates = _build_candidates(unmatched_invoices, remaining_bank_index, cfg)
    inv_bank_assignment = constrained_assign(
        inv_bank_candidates,
        all_left_ids=[i.record_id for i in unmatched_invoices],
        all_right_ids=[b.record_id for b in remaining_banks],
    )
    invoice_to_direct_bank = {c.left_id: c for c in inv_bank_assignment.assigned}

    deterministic_time = time.perf_counter() - t0

    # --- assemble Transaction lifecycle objects ---
    transactions: List[Transaction] = []
    exceptions: List[ReconciliationException] = []

    for inv in invoices:
        settle_cand = invoice_to_settlement.get(inv.record_id)
        settle_obj = settle_by_id.get(settle_cand.right_id) if settle_cand else None

        bank_cand = None
        bank_obj = None
        if settle_obj is not None:
            sb_cand = settlement_to_bank.get(settle_obj.record_id)
            if sb_cand:
                bank_cand = sb_cand
                bank_obj = bank_by_id.get(sb_cand.right_id)
        else:
            direct_cand = invoice_to_direct_bank.get(inv.record_id)
            if direct_cand:
                bank_cand = direct_cand
                bank_obj = bank_by_id.get(direct_cand.right_id)

        # combine evidence from whichever links exist
        component_scores = [c.score for c in (settle_cand, bank_cand) if c]
        if component_scores:
            avg = lambda key: sum(s[key] for s in component_scores) / len(component_scores)
            evidence = MatchEvidence(
                amount_score=round(avg("amount_score"), 2),
                reference_score=round(avg("reference_score"), 2),
                date_score=round(avg("date_score"), 2),
                merchant_score=round(avg("merchant_score"), 2),
                description_score=round(avg("description_score"), 2),
                weighted_score=round(avg("weighted_score"), 2),
                notes=sorted({n for s in component_scores for n in s["notes"]}),
            )
        else:
            evidence = MatchEvidence()

        confidence = evidence.weighted_score
        tier = _tier_for_score(confidence, cfg)
        method = "exact" if evidence.reference_score >= 99.9 and evidence.amount_score >= 99.9 else (
            "scored" if component_scores else "unmatched"
        )
        fully_reconciled = bool(settle_obj and bank_obj and tier == ConfidenceTier.AUTO_RECONCILE) or \
                            bool(bank_obj and not settle_obj and tier == ConfidenceTier.AUTO_RECONCILE)

        # --- independent fee/tax POLICY compliance check ---
        # This runs regardless of matching confidence: a settlement can be
        # internally self-consistent (net == gross - fee - tax, so the
        # bank amount matches the settlement amount perfectly) while still
        # applying an off-policy fee rate. That is a real compliance
        # exception, not a matching problem, so it must not be masked by
        # a high matching confidence score.
        policy_violation = None
        if settle_obj is not None and inv.amount:
            expected_fee, expected_tax, _ = _expected_fee_tax(inv.amount, cfg)
            actual_fee = settle_obj.fee if settle_obj.fee is not None else expected_fee
            actual_tax = settle_obj.tax if settle_obj.tax is not None else expected_tax
            if abs(actual_fee - expected_fee) > max(1.0, expected_fee * 0.05):
                policy_violation = (ExceptionCategory.FEE_DISCREPANCY, ExceptionSeverity.MEDIUM,
                                     [f"expected fee ₹{expected_fee}, settlement shows ₹{actual_fee}"])
            elif abs(actual_tax - expected_tax) > max(1.0, expected_tax * 0.05):
                policy_violation = (ExceptionCategory.TAX_DISCREPANCY, ExceptionSeverity.MEDIUM,
                                     [f"expected tax ₹{expected_tax}, settlement shows ₹{actual_tax}"])
        if policy_violation is not None:
            fully_reconciled = False

        txn = Transaction(
            transaction_id=f"TXN-{inv.record_id}",
            invoice=inv,
            settlement=settle_obj,
            bank_transaction=bank_obj,
            confidence=confidence,
            confidence_tier=tier,
            evidence=evidence,
            method=method,
            is_fully_reconciled=fully_reconciled,
        )
        transactions.append(txn)

        if not fully_reconciled:
            if policy_violation is not None:
                category, severity, notes = policy_violation
            else:
                category, severity, notes = _categorize_exception(txn, cfg, duplicate_ids)
            candidate_ids = []
            if inv.record_id in inv_settle_assignment.left_had_competing_claim:
                candidate_ids = inv_settle_assignment.left_had_competing_claim[inv.record_id]
            elif inv.record_id in inv_bank_assignment.left_had_competing_claim:
                candidate_ids = inv_bank_assignment.left_had_competing_claim[inv.record_id]

            exceptions.append(
                ReconciliationException(
                    exception_id=f"EXC-{uuid.uuid4().hex[:8]}",
                    transaction_id=txn.transaction_id,
                    category=category,
                    severity=severity,
                    evidence=notes + evidence.notes,
                    candidate_matches=candidate_ids,
                    confidence=confidence,
                    recommended_action=None,  # filled in by the resolution agent
                    status="open",
                )
            )

    total_time = time.perf_counter() - t0

    return {
        "transactions": transactions,
        "exceptions": exceptions,
        "stats": {
            "deterministic_time_seconds": round(deterministic_time, 4),
            "total_time_seconds": round(total_time, 4),
            "invoice_settlement_candidates": len(inv_settle_candidates),
            "settlement_bank_candidates": len(settle_bank_candidates),
            "invoice_bank_fallback_candidates": len(inv_bank_candidates),
        },
    }
