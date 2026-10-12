#!/usr/bin/env python3
"""
Generates a reproducible synthetic reconciliation dataset: invoices.csv,
settlements.csv, bank_transactions.csv, and ground_truth.json.

Usage:
    python scripts/generate_dataset.py --records 500 --seed 42 --out-dir data/synthetic

The same --seed always regenerates the identical dataset and ground truth,
which is what makes the benchmark numbers reproducible and comparable
across runs.
"""

import argparse
import csv
import json
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "model"))

FEE_PCT = 0.02
GST_PCT = 0.18

MERCHANTS = [
    ("MERCHANT-01", "Acme Retail Pvt Ltd"),
    ("MERCHANT-02", "Bluewave Traders"),
    ("MERCHANT-03", "Kiran Textiles"),
    ("MERCHANT-04", "Northline Logistics"),
    ("MERCHANT-05", "Pinnacle Foods"),
    ("MERCHANT-06", "Orbit Media"),
    ("MERCHANT-07", "Sundar Electronics"),
    ("MERCHANT-08", "Global Ventures"),
]

BASE_DATE = datetime(2026, 8, 1)

# scenario distribution -- weights sum to 1.0
SCENARIOS = [
    ("clean", 0.60),
    ("date_mismatch", 0.10),
    ("amount_mismatch", 0.08),
    ("missing_bank_transaction", 0.05),
    ("missing_settlement", 0.05),
    ("duplicate_transaction", 0.04),
    ("reference_format_mismatch", 0.03),
    ("fee_tax_discrepancy", 0.03),
    ("ambiguous_match", 0.02),
]


def _weighted_choice(rng: random.Random, choices):
    names, weights = zip(*choices)
    return rng.choices(names, weights=weights, k=1)[0]


def _fmt_date(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


def _perturb_reference(rng: random.Random, ref: str) -> str:
    """Simulate a re-keyed reference: drop or swap one character."""
    if len(ref) < 4:
        return ref
    i = rng.randint(1, len(ref) - 2)
    chars = list(ref)
    op = rng.choice(["drop", "swap"])
    if op == "drop":
        del chars[i]
    else:
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
    return "".join(chars)


def generate(num_records: int, seed: int, out_dir: str):
    rng = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)

    invoices, settlements, bank_txns = [], [], []
    ground_truth = {}

    n = 0
    idx = 0
    while n < num_records:
        idx += 1
        inv_id = f"INV-{idx:05d}"
        order_ref = f"ORD{idx:05d}"
        merchant_id, merchant_name = rng.choice(MERCHANTS)
        customer_id = f"CUST-{rng.randint(100, 999)}"
        gross = round(rng.uniform(500, 60000), 2)
        invoice_date = BASE_DATE + timedelta(days=rng.randint(0, 45))
        scenario = _weighted_choice(rng, SCENARIOS)

        invoice = {
            "record_id": inv_id,
            "reference": order_ref,
            "amount": gross,
            "currency": "INR",
            "date": _fmt_date(invoice_date),
            "customer_id": customer_id,
            "merchant_id": merchant_id,
            "status": "paid",
            "description": f"Invoice for order {order_ref} - {merchant_name}",
        }
        invoices.append(invoice)
        n += 1

        fee = round(gross * FEE_PCT, 2)
        tax = round(fee * GST_PCT, 2)
        net = round(gross - fee - tax, 2)
        settle_id = f"SET-{idx:05d}"
        bank_id = f"UTR-{idx:05d}"

        if scenario == "clean":
            settle_date = invoice_date + timedelta(days=rng.randint(0, 2))
            settlements.append({
                "record_id": settle_id, "reference": order_ref, "payment_reference": order_ref,
                "gross_amount": gross, "fee": fee, "tax": tax, "net_amount": net,
                "settlement_date": _fmt_date(settle_date), "merchant_id": merchant_id, "currency": "INR",
            })
            bank_txns.append({
                "record_id": bank_id, "reference": order_ref, "amount": net, "currency": "INR",
                "date": _fmt_date(settle_date), "description": f"NEFT/{order_ref}/{merchant_name}",
            })
            ground_truth[inv_id] = {"expected_bank_transaction": bank_id, "expected_settlement": settle_id, "status": "matched"}

        elif scenario == "date_mismatch":
            settle_date = invoice_date + timedelta(days=rng.randint(6, 12))  # well beyond 3-day policy window
            settlements.append({
                "record_id": settle_id, "reference": order_ref, "payment_reference": order_ref,
                "gross_amount": gross, "fee": fee, "tax": tax, "net_amount": net,
                "settlement_date": _fmt_date(settle_date), "merchant_id": merchant_id, "currency": "INR",
            })
            bank_txns.append({
                "record_id": bank_id, "reference": order_ref, "amount": net, "currency": "INR",
                "date": _fmt_date(settle_date), "description": f"NEFT/{order_ref}/{merchant_name}",
            })
            ground_truth[inv_id] = {"expected_bank_transaction": bank_id, "expected_settlement": settle_id, "status": "matched"}

        elif scenario == "amount_mismatch":
            settle_date = invoice_date + timedelta(days=rng.randint(0, 2))
            bad_net = round(net - rng.uniform(50, 500), 2)  # unexplained shortfall beyond fee/tax
            settlements.append({
                "record_id": settle_id, "reference": order_ref, "payment_reference": order_ref,
                "gross_amount": gross, "fee": fee, "tax": tax, "net_amount": net,
                "settlement_date": _fmt_date(settle_date), "merchant_id": merchant_id, "currency": "INR",
            })
            bank_txns.append({
                "record_id": bank_id, "reference": order_ref, "amount": bad_net, "currency": "INR",
                "date": _fmt_date(settle_date), "description": f"NEFT/{order_ref}/{merchant_name}",
            })
            # engine will see a genuine numeric mismatch; ground truth still expects the link,
            # but it will legitimately fail the amount check and become an exception -- that's intended.
            ground_truth[inv_id] = {"expected_bank_transaction": bank_id, "expected_settlement": settle_id, "status": "matched"}

        elif scenario == "missing_bank_transaction":
            settle_date = invoice_date + timedelta(days=rng.randint(0, 2))
            settlements.append({
                "record_id": settle_id, "reference": order_ref, "payment_reference": order_ref,
                "gross_amount": gross, "fee": fee, "tax": tax, "net_amount": net,
                "settlement_date": _fmt_date(settle_date), "merchant_id": merchant_id, "currency": "INR",
            })
            ground_truth[inv_id] = {"expected_bank_transaction": None, "expected_settlement": settle_id, "status": "missing_bank_transaction"}

        elif scenario == "missing_settlement":
            settle_date = invoice_date + timedelta(days=rng.randint(0, 2))
            bank_txns.append({
                "record_id": bank_id, "reference": order_ref, "amount": net, "currency": "INR",
                "date": _fmt_date(settle_date), "description": f"NEFT/{order_ref}/{merchant_name}",
            })
            ground_truth[inv_id] = {"expected_bank_transaction": bank_id, "expected_settlement": None, "status": "matched"}

        elif scenario == "duplicate_transaction":
            # the ORIGINAL reconciles cleanly...
            settle_date = invoice_date + timedelta(days=rng.randint(0, 2))
            settlements.append({
                "record_id": settle_id, "reference": order_ref, "payment_reference": order_ref,
                "gross_amount": gross, "fee": fee, "tax": tax, "net_amount": net,
                "settlement_date": _fmt_date(settle_date), "merchant_id": merchant_id, "currency": "INR",
            })
            bank_txns.append({
                "record_id": bank_id, "reference": order_ref, "amount": net, "currency": "INR",
                "date": _fmt_date(settle_date), "description": f"NEFT/{order_ref}/{merchant_name}",
            })
            ground_truth[inv_id] = {"expected_bank_transaction": bank_id, "expected_settlement": settle_id, "status": "matched"}

            # ...but a duplicate invoice (same reference + amount) is also submitted and must NOT reconcile
            if n < num_records:
                idx += 1
                dup_id = f"INV-{idx:05d}"
                invoices.append({**invoice, "record_id": dup_id, "description": invoice["description"] + " (resubmitted)"})
                ground_truth[dup_id] = {"expected_bank_transaction": None, "expected_settlement": None, "status": "duplicate"}
                n += 1

        elif scenario == "reference_format_mismatch":
            settle_date = invoice_date + timedelta(days=rng.randint(0, 2))
            reworked_ref = _perturb_reference(rng, order_ref)
            settlements.append({
                "record_id": settle_id, "reference": reworked_ref, "payment_reference": reworked_ref,
                "gross_amount": gross, "fee": fee, "tax": tax, "net_amount": net,
                "settlement_date": _fmt_date(settle_date), "merchant_id": merchant_id, "currency": "INR",
            })
            bank_txns.append({
                "record_id": bank_id, "reference": reworked_ref, "amount": net, "currency": "INR",
                "date": _fmt_date(settle_date), "description": f"NEFT/{reworked_ref}/{merchant_name}",
            })
            ground_truth[inv_id] = {"expected_bank_transaction": bank_id, "expected_settlement": settle_id, "status": "matched"}

        elif scenario == "fee_tax_discrepancy":
            settle_date = invoice_date + timedelta(days=rng.randint(0, 2))
            wrong_fee = round(gross * rng.choice([0.035, 0.045]), 2)  # off-policy fee rate
            wrong_tax = round(wrong_fee * GST_PCT, 2)
            wrong_net = round(gross - wrong_fee - wrong_tax, 2)
            settlements.append({
                "record_id": settle_id, "reference": order_ref, "payment_reference": order_ref,
                "gross_amount": gross, "fee": wrong_fee, "tax": wrong_tax, "net_amount": wrong_net,
                "settlement_date": _fmt_date(settle_date), "merchant_id": merchant_id, "currency": "INR",
            })
            bank_txns.append({
                "record_id": bank_id, "reference": order_ref, "amount": wrong_net, "currency": "INR",
                "date": _fmt_date(settle_date), "description": f"NEFT/{order_ref}/{merchant_name}",
            })
            ground_truth[inv_id] = {"expected_bank_transaction": bank_id, "expected_settlement": settle_id, "status": "matched"}

        elif scenario == "ambiguous_match":
            # bank transaction lands with an unrelated reference but a coincidentally close amount/date --
            # the engine will find it via the amount index, but reference/merchant won't corroborate it.
            settle_date = invoice_date + timedelta(days=rng.randint(0, 2))
            unrelated_ref = f"ORD{rng.randint(90000, 99999)}"
            bank_txns.append({
                "record_id": bank_id, "reference": unrelated_ref, "amount": round(net + rng.uniform(-3, 3), 2),
                "currency": "INR", "date": _fmt_date(settle_date), "description": f"NEFT/misc/{merchant_name}",
            })
            ground_truth[inv_id] = {"expected_bank_transaction": None, "expected_settlement": None, "status": "ambiguous"}

    # write CSVs
    def write_csv(filename, rows, fieldnames):
        with open(os.path.join(out_dir, filename), "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    write_csv("invoices.csv", invoices, list(invoices[0].keys()))
    if settlements:
        write_csv("settlements.csv", settlements, list(settlements[0].keys()))
    if bank_txns:
        write_csv("bank_transactions.csv", bank_txns, list(bank_txns[0].keys()))

    with open(os.path.join(out_dir, "ground_truth.json"), "w", encoding="utf-8") as f:
        json.dump(ground_truth, f, indent=2)

    print(f"Generated {len(invoices)} invoices, {len(settlements)} settlements, "
          f"{len(bank_txns)} bank transactions -> {out_dir} (seed={seed})")


def main():
    parser = argparse.ArgumentParser(description="Generate synthetic reconciliation dataset")
    parser.add_argument("--records", type=int, default=100, help="number of invoice records to generate")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out-dir", type=str, default=None)
    args = parser.parse_args()

    out_dir = args.out_dir or os.path.join(
        os.path.dirname(__file__), "..", "data", "synthetic", f"{args.records}_seed{args.seed}"
    )
    generate(args.records, args.seed, out_dir)


if __name__ == "__main__":
    main()
