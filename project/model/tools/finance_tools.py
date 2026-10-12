"""
Tools for the natural-language Finance Controller agent.

The controller agent must NEVER receive the entire transaction/exception
list as prompt context -- for a 500+ record batch that's slow, expensive,
and pushes the model toward summarizing instead of actually querying. Each
tool here returns a small, targeted slice of data so the agent behaves
like an analyst running queries, not a model reading a CSV dump.

Every tool takes the ReconciliationRun as first argument; the controller
agent binds that once per request (see agents/controller_agent.py) so the
LLM only ever sees the tool's own declared parameters.
"""

from typing import Any, Dict, List, Optional

from evaluation.metrics import compute_metrics
from models.schemas import ExceptionCategory
from policies.retrieval import get_policy as _get_policy_text
from services.audit import get_audit_history as _get_audit_history
from services.store import ReconciliationRun


def _txn_summary(txn) -> Dict[str, Any]:
    return {
        "transaction_id": txn.transaction_id,
        "invoice_id": txn.invoice.record_id if txn.invoice else None,
        "invoice_amount": txn.invoice.amount if txn.invoice else None,
        "merchant_id": txn.invoice.merchant_id if txn.invoice else None,
        "customer_id": txn.invoice.customer_id if txn.invoice else None,
        "settlement_id": txn.settlement.record_id if txn.settlement else None,
        "bank_transaction_id": txn.bank_transaction.record_id if txn.bank_transaction else None,
        "confidence": txn.confidence,
        "confidence_tier": txn.confidence_tier.value,
        "is_fully_reconciled": txn.is_fully_reconciled,
    }


def get_transaction(run: ReconciliationRun, transaction_id: str) -> Dict[str, Any]:
    for txn in run.transactions:
        if txn.transaction_id == transaction_id or (txn.invoice and txn.invoice.record_id == transaction_id):
            return _txn_summary(txn)
    return {"error": f"No transaction found for '{transaction_id}'"}


def search_transactions(
    run: ReconciliationRun,
    merchant_id: Optional[str] = None,
    is_fully_reconciled: Optional[bool] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    results = []
    for txn in run.transactions:
        if merchant_id and (not txn.invoice or txn.invoice.merchant_id != merchant_id):
            continue
        if is_fully_reconciled is not None and txn.is_fully_reconciled != is_fully_reconciled:
            continue
        amount = txn.invoice.amount if txn.invoice else None
        if min_amount is not None and (amount is None or amount < min_amount):
            continue
        if max_amount is not None and (amount is None or amount > max_amount):
            continue
        results.append(_txn_summary(txn))
        if len(results) >= limit:
            break
    return results


def find_matching_records(run: ReconciliationRun, reference: Optional[str] = None, amount: Optional[float] = None) -> List[Dict[str, Any]]:
    """Ad-hoc lookup by reference or approximate amount, across whatever
    lifecycle pieces each transaction has -- useful for 'have we seen
    anything for order X' style questions."""
    results = []
    for txn in run.transactions:
        pieces = [p for p in (txn.invoice, txn.settlement, txn.bank_transaction) if p]
        for p in pieces:
            ref_match = reference and p.reference and reference.lower() in str(p.reference).lower()
            amt = getattr(p, "amount", None) or getattr(p, "net_amount", None) or getattr(p, "gross_amount", None)
            amount_match = amount is not None and amt is not None and abs(amt - amount) <= max(1.0, amount * 0.01)
            if ref_match or amount_match:
                results.append({"transaction_id": txn.transaction_id, "record_id": p.record_id, "source": p.source})
    return results[:20]


def get_exception(run: ReconciliationRun, exception_id: str) -> Dict[str, Any]:
    exc = run.exceptions.get(exception_id)
    if not exc:
        return {"error": f"No exception '{exception_id}'"}
    return exc.model_dump()


def get_exceptions(
    run: ReconciliationRun, category: Optional[str] = None, status: Optional[str] = None, limit: int = 20
) -> List[Dict[str, Any]]:
    results = []
    for exc in run.exceptions.values():
        if category and exc.category.value != category:
            continue
        if status and exc.status.value != status:
            continue
        results.append(exc.model_dump())
        if len(results) >= limit:
            break
    return results


def get_reconciliation_metrics(run: ReconciliationRun) -> Dict[str, Any]:
    if run.metrics is None:
        return {"error": "Metrics have not been computed for this run yet."}
    return run.metrics.model_dump()


def get_merchant_metrics(run: ReconciliationRun, merchant_id: Optional[str] = None) -> List[Dict[str, Any]]:
    by_merchant: Dict[str, Dict[str, Any]] = {}
    for txn in run.transactions:
        mid = txn.invoice.merchant_id if txn.invoice else "UNKNOWN"
        if merchant_id and mid != merchant_id:
            continue
        bucket = by_merchant.setdefault(mid, {"merchant_id": mid, "total": 0, "reconciled": 0, "exceptions": 0, "unreconciled_value": 0.0})
        bucket["total"] += 1
        if txn.is_fully_reconciled:
            bucket["reconciled"] += 1
        else:
            bucket["exceptions"] += 1
            bucket["unreconciled_value"] += txn.invoice.amount or 0.0
    return sorted(by_merchant.values(), key=lambda b: b["exceptions"], reverse=True)


def get_audit_history(run: ReconciliationRun, transaction_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return [e.model_dump() for e in _get_audit_history(run, transaction_id)]


def calculate_settlement(gross_amount: float, fee_pct: float = 0.02, gst_pct: float = 0.18) -> Dict[str, float]:
    """Pure deterministic arithmetic -- exposed as a tool so the LLM can
    ask for a recomputation instead of doing the math itself."""
    fee = round(gross_amount * fee_pct, 2)
    tax = round(fee * gst_pct, 2)
    net = round(gross_amount - fee - tax, 2)
    return {"gross_amount": gross_amount, "fee": fee, "tax": tax, "net_amount": net}


def get_policy(topic: str) -> str:
    return _get_policy_text(topic)


TOOL_FUNCTIONS = {
    "get_transaction": get_transaction,
    "search_transactions": search_transactions,
    "find_matching_records": find_matching_records,
    "get_exception": get_exception,
    "get_exceptions": get_exceptions,
    "get_reconciliation_metrics": get_reconciliation_metrics,
    "get_merchant_metrics": get_merchant_metrics,
    "get_audit_history": get_audit_history,
    "calculate_settlement": calculate_settlement,
    "get_policy": get_policy,
}
