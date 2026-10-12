"""
Candidate generation.

Instead of comparing every record against every other record (O(n^2), and
wasteful -- a ₹15,000 invoice from Merchant A has no business being
compared against a ₹40 bank transaction from an unrelated reference), we
index records by normalized reference and by amount bucket, then only pull
out the plausible neighborhood for each query record.

This keeps a 500-1000 record batch fast: index build is O(n), and each
lookup touches only the records that share a reference token or fall in a
nearby amount bucket -- not the whole dataset.
"""

import re
from collections import defaultdict
from typing import Any, Dict, List

AMOUNT_BUCKET_SIZE = 500.0  # group amounts into ₹500 buckets for indexing


def normalize_reference(ref: str) -> str:
    """Strip formatting noise so 'ORD-4821', 'ord4821', 'ORD 4821' all match."""
    if not ref:
        return ""
    return re.sub(r"[^A-Za-z0-9]", "", ref).upper()


def _amount_bucket(amount: float) -> int:
    return int(round(amount / AMOUNT_BUCKET_SIZE))


class CandidateIndex:
    """Indexes a pool of records (settlements or bank transactions) for
    fast candidate lookup against a query record (typically an invoice)."""

    def __init__(self, records: List[Any], amount_attrs: tuple = ("amount", "net_amount", "gross_amount")):
        """
        `amount_attrs` controls which field is used to bucket THIS pool for
        lookup. Pass ("gross_amount", "amount") when indexing settlements
        that will be queried by invoices (gross-to-gross comparison); leave
        the default when indexing bank transactions or settlements that
        will be queried by their net-amount counterpart. See
        matching/scoring.py:_resolve_amounts for the full rationale.
        """
        self.records = records
        self.by_reference: Dict[str, List[Any]] = defaultdict(list)
        self.by_amount_bucket: Dict[int, List[Any]] = defaultdict(list)

        for r in records:
            ref = normalize_reference(getattr(r, "reference", None) or getattr(r, "payment_reference", None) or "")
            if ref:
                self.by_reference[ref].append(r)
            amount = None
            for attr in amount_attrs:
                amount = getattr(r, attr, None)
                if amount is not None:
                    break
            if amount is not None:
                self.by_amount_bucket[_amount_bucket(amount)].append(r)

    def candidates_for(self, reference: str, amount: float, amount_tolerance_pct: float = 0.1) -> List[Any]:
        """Return the union of same-reference and nearby-amount records,
        deduplicated, WITHOUT scanning the full record set."""
        seen_ids = set()
        results: List[Any] = []

        norm_ref = normalize_reference(reference)
        if norm_ref:
            for r in self.by_reference.get(norm_ref, []):
                if r.record_id not in seen_ids:
                    seen_ids.add(r.record_id)
                    results.append(r)

        if amount is not None:
            center_bucket = _amount_bucket(amount)
            # widen by tolerance: how many ±500 buckets does the tolerance span?
            bucket_span = max(1, int((amount * amount_tolerance_pct) / AMOUNT_BUCKET_SIZE) + 1)
            for offset in range(-bucket_span, bucket_span + 1):
                for r in self.by_amount_bucket.get(center_bucket + offset, []):
                    if r.record_id not in seen_ids:
                        seen_ids.add(r.record_id)
                        results.append(r)

        return results
