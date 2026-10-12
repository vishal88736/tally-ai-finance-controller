"""
Explainable scoring between two candidate records.

Every score component (amount, reference, date, merchant, description) is
computed independently and deterministically, then combined with
configurable weights. Nothing here is an LLM call -- confidence must come
from evidence, not from asking a model "how sure are you".
"""

from datetime import datetime
from typing import Any, Optional

from rapidfuzz import fuzz

from matching.candidate_generation import normalize_reference
from models.schemas import Invoice, Settlement

DEFAULT_WEIGHTS = {
    "amount": 0.35,
    "reference": 0.30,
    "date": 0.15,
    "merchant": 0.10,
    "description": 0.10,
}


def _get(record: Any, *field_names: str) -> Optional[Any]:
    for name in field_names:
        val = getattr(record, name, None)
        if val is not None:
            return val
    return None


def _resolve_amounts(a: Any, b: Any):
    """
    Amount is NOT a single unambiguous field on a Settlement -- it means
    gross_amount when compared to an Invoice, and net_amount when compared
    to a BankTransaction (the whole point of the lifecycle: gross minus fee
    minus tax equals what actually lands in the bank). Comparing the wrong
    pair (e.g. invoice gross vs settlement net) manufactures a false
    "amount mismatch" on every single clean transaction, which is exactly
    the kind of silent, hard-to-notice bug this function exists to prevent.
    """
    def value_for(record: Any, counterpart: Any):
        if isinstance(record, Settlement):
            if isinstance(counterpart, Invoice):
                return record.gross_amount
            return record.net_amount if record.net_amount is not None else record.gross_amount
        return getattr(record, "amount", None)

    return value_for(a, b), value_for(b, a)


def _amount_score(a_amount, b_amount, tolerance_abs: float, tolerance_pct: float) -> float:
    if a_amount is None or b_amount is None:
        return 0.0
    diff = abs(a_amount - b_amount)
    tol = max(tolerance_abs, tolerance_pct * max(abs(a_amount), 1))
    if diff <= tol:
        return 100.0
    # decay smoothly beyond tolerance rather than cliff-dropping to 0
    return max(0.0, 100.0 - (diff / max(tol, 0.01)) * 15)


def _reference_score(a_ref, b_ref) -> float:
    if not a_ref or not b_ref:
        return 0.0
    norm_a, norm_b = normalize_reference(a_ref), normalize_reference(b_ref)
    if not norm_a or not norm_b:
        return 0.0
    if norm_a == norm_b:
        return 100.0
    return float(fuzz.ratio(norm_a, norm_b))


def _date_score(a_date, b_date, window_days: int) -> float:
    if not a_date or not b_date:
        return 0.0
    try:
        d1 = datetime.fromisoformat(str(a_date)[:10])
        d2 = datetime.fromisoformat(str(b_date)[:10])
    except ValueError:
        return 0.0
    diff_days = abs((d1 - d2).days)
    if diff_days <= window_days:
        return 100.0
    return max(0.0, 100.0 - (diff_days - window_days) * 12)


def _merchant_score(a, b):
    """Returns (score, applicable). Not applicable when either side simply
    doesn't carry a merchant/customer id (e.g. raw bank transactions) --
    that's a missing SIGNAL, not evidence of a mismatch, and must not be
    scored as 0 or it silently caps every bank-side match well below the
    auto-reconcile threshold."""
    a_id = _get(a, "merchant_id", "customer_id")
    b_id = _get(b, "merchant_id", "customer_id")
    if not a_id or not b_id:
        return 0.0, False
    return (100.0 if str(a_id).strip().lower() == str(b_id).strip().lower() else 0.0), True


def _description_score(a, b):
    a_text = _get(a, "description")
    b_text = _get(b, "description")
    if not a_text or not b_text:
        return 0.0, False
    return float(fuzz.token_set_ratio(str(a_text), str(b_text))), True


def score_pair(
    a: Any,
    b: Any,
    weights: dict = None,
    amount_tolerance_abs: float = 1.0,
    amount_tolerance_pct: float = 0.005,
    date_window_days: int = 3,
) -> dict:
    """
    Score how likely records `a` and `b` refer to the same underlying
    transaction. Returns a dict with each component score, the weighted
    total, and short human-readable notes -- the same object both the
    matcher and the investigation agent consume, so "why did this match"
    is always answerable.
    """
    weights = weights or DEFAULT_WEIGHTS

    a_amount, b_amount = _resolve_amounts(a, b)
    a_ref = _get(a, "reference", "payment_reference")
    b_ref = _get(b, "reference", "payment_reference")
    a_date = _get(a, "date", "settlement_date")
    b_date = _get(b, "date", "settlement_date")

    amount_score = _amount_score(a_amount, b_amount, amount_tolerance_abs, amount_tolerance_pct)
    reference_score = _reference_score(a_ref, b_ref)
    date_score = _date_score(a_date, b_date, date_window_days)
    merchant_score, merchant_applicable = _merchant_score(a, b)
    description_score, description_applicable = _description_score(a, b)

    # amount/reference/date are always "applicable" if a value could be
    # computed at all; merchant/description are only applicable when both
    # sides actually carry that signal. Missing signals are excluded from
    # the weighted average (renormalized), not scored as a mismatch --
    # otherwise every source pair lacking a field (e.g. bank transactions
    # with no merchant_id) would be structurally capped below the
    # auto-reconcile threshold regardless of how well the rest agrees.
    components = [
        (amount_score, weights["amount"], a_amount is not None and b_amount is not None),
        (reference_score, weights["reference"], bool(a_ref) and bool(b_ref)),
        (date_score, weights["date"], bool(a_date) and bool(b_date)),
        (merchant_score, weights["merchant"], merchant_applicable),
        (description_score, weights["description"], description_applicable),
    ]
    applicable_weight = sum(w for _, w, ok in components if ok)
    weighted = (
        sum(score * w for score, w, ok in components if ok) / applicable_weight
        if applicable_weight > 0 else 0.0
    )

    notes = []
    if amount_score >= 99:
        notes.append("amount_match")
    if reference_score >= 90:
        notes.append("reference_match")
    if date_score >= 99:
        notes.append("date_within_window")
    if merchant_score >= 99:
        notes.append("merchant_match")

    return {
        "amount_score": round(amount_score, 2),
        "reference_score": round(reference_score, 2),
        "date_score": round(date_score, 2),
        "merchant_score": round(merchant_score, 2),
        "description_score": round(description_score, 2),
        "weighted_score": round(weighted, 2),
        "notes": notes,
    }
