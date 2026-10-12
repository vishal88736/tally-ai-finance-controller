"""
Loads canonical-schema CSVs (the format produced by scripts/generate_dataset.py
and expected by the reconciliation engine) directly into Pydantic models.

This is deliberately separate from the LLM-based normalizer (see
agents/normalizer_node.py in earlier iterations / the /api/reconcile-upload
path): the benchmark and demo dataset path never needs a model call just to
parse a CSV whose columns we control. The LLM-based arbitrary-file upload
path is preserved for messier real-world files with unknown column names.
"""

import csv
from typing import List

from models.schemas import BankTransaction, Invoice, Settlement


def _read_csv(path: str) -> List[dict]:
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_invoices(path: str) -> List[Invoice]:
    rows = _read_csv(path)
    return [
        Invoice(
            record_id=row["record_id"],
            reference=row.get("reference") or None,
            amount=_to_float(row.get("amount")),
            currency=row.get("currency", "INR"),
            date=row.get("date") or None,
            customer_id=row.get("customer_id") or None,
            merchant_id=row.get("merchant_id") or None,
            status=row.get("status") or None,
            description=row.get("description") or None,
            raw=row,
        )
        for row in rows
    ]


def load_settlements(path: str) -> List[Settlement]:
    rows = _read_csv(path)
    return [
        Settlement(
            record_id=row["record_id"],
            reference=row.get("reference") or None,
            payment_reference=row.get("payment_reference") or None,
            gross_amount=_to_float(row.get("gross_amount")),
            fee=_to_float(row.get("fee")),
            tax=_to_float(row.get("tax")),
            net_amount=_to_float(row.get("net_amount")),
            # NOTE: deliberately NOT setting the generic `amount` field here.
            # Settlement amount is ambiguous (gross vs net) depending on which
            # side of the lifecycle it's being compared to -- see
            # matching/scoring.py:_resolve_amounts for the type-aware fix.
            date=row.get("settlement_date") or row.get("date") or None,
            settlement_date=row.get("settlement_date") or None,
            merchant_id=row.get("merchant_id") or None,
            currency=row.get("currency", "INR"),
            raw=row,
        )
        for row in rows
    ]


def load_bank_transactions(path: str) -> List[BankTransaction]:
    rows = _read_csv(path)
    return [
        BankTransaction(
            record_id=row["record_id"],
            reference=row.get("reference") or None,
            amount=_to_float(row.get("amount")),
            currency=row.get("currency", "INR"),
            date=row.get("date") or None,
            description=row.get("description") or None,
            raw=row,
        )
        for row in rows
    ]
