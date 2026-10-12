#!/usr/bin/env python3
"""
End-to-end benchmark for the AI Finance Controller.

Usage:
    python scripts/benchmark.py --records 500 --seed 42
    python scripts/benchmark.py --records 500 --seed 42 --with-llm   # also runs real investigation agent calls (needs ANTHROPIC_API_KEY)
    python scripts/benchmark.py --records 500 --seed 42 --regenerate  # regenerate the dataset first even if it already exists

Generates the dataset (if missing), runs the full LangGraph pipeline, and
prints a benchmark report. Every number in the report comes from the
actual run -- nothing here is hard-coded.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "model"))

from evaluation.ground_truth import load_ground_truth  # noqa: E402
from graph.pipeline import controller_graph  # noqa: E402
from services.data_loader import load_bank_transactions, load_invoices, load_settlements  # noqa: E402

import generate_dataset  # noqa: E402  (sibling script)


def run_benchmark(records: int, seed: int, with_llm: bool, regenerate: bool):
    out_dir = os.path.join(os.path.dirname(__file__), "..", "data", "synthetic", f"{records}_seed{seed}")

    if regenerate or not os.path.exists(os.path.join(out_dir, "invoices.csv")):
        generate_dataset.generate(records, seed, out_dir)

    invoices = load_invoices(os.path.join(out_dir, "invoices.csv"))
    settlements_path = os.path.join(out_dir, "settlements.csv")
    bank_path = os.path.join(out_dir, "bank_transactions.csv")
    settlements = load_settlements(settlements_path) if os.path.exists(settlements_path) else []
    banks = load_bank_transactions(bank_path) if os.path.exists(bank_path) else []
    ground_truth = load_ground_truth(os.path.join(out_dir, "ground_truth.json"))

    wall_start = time.perf_counter()
    final_state = controller_graph.invoke({
        "invoices": invoices,
        "settlements": settlements,
        "bank_transactions": banks,
        "ground_truth": ground_truth,
        "enable_llm_investigation": with_llm,
        "config": {},
    })
    wall_time = time.perf_counter() - wall_start

    m = final_state["metrics"]

    # duplicate-reconciliation safety check -- must ALWAYS be zero
    bank_claims = [t.bank_transaction.record_id for t in final_state["transactions"] if t.bank_transaction]
    duplicate_claims = [b for b in set(bank_claims) if bank_claims.count(b) > 1]

    lines = []
    lines.append("=" * 60)
    lines.append("AI FINANCE CONTROLLER BENCHMARK")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Dataset:                  {records} records (seed={seed})")
    lines.append(f"Records processed:        {m.total_records}")
    lines.append(f"Transactions:             {m.total_transactions}")
    lines.append("")
    lines.append(f"Match rate:               {m.match_rate:.1f}%")
    lines.append(f"Precision:                {m.precision * 100:.1f}%")
    lines.append(f"Recall:                   {m.recall * 100:.1f}%")
    lines.append(f"F1:                       {m.f1 * 100:.1f}%")
    lines.append(f"False positives:          {m.false_positives}")
    lines.append(f"False negatives:          {m.false_negatives}")
    lines.append("")
    lines.append(f"Auto-resolved:            {m.auto_resolved}")
    lines.append(f"Human review:             {m.human_review}")
    lines.append(f"Unresolved:               {m.unresolved}")
    lines.append(f"Exceptions:               {m.exception_count}")
    lines.append("")
    lines.append(f"Reconciled value:         ₹{m.reconciled_value:,.2f}")
    lines.append(f"Unreconciled value:       ₹{m.unreconciled_value:,.2f}")
    lines.append("")
    lines.append(f"Deterministic time:       {m.deterministic_time_seconds:.4f} sec")
    lines.append(f"LLM investigation time:   {m.llm_time_seconds:.4f} sec")
    lines.append(f"Total processing time:    {m.processing_time_seconds:.4f} sec")
    lines.append(f"Wall clock time:          {wall_time:.4f} sec")
    lines.append(f"Throughput:               {m.throughput_records_per_sec:.1f} records/sec")
    lines.append("")
    lines.append(f"LLM calls:                {m.llm_calls}")
    lines.append("")
    lines.append(f"Duplicate bank claims:    {len(duplicate_claims)} {'(!! CONSTRAINT VIOLATED !!)' if duplicate_claims else '(constraint holds)'}")
    lines.append("=" * 60)

    report = "\n".join(lines)
    print(report)
    return report


def main():
    parser = argparse.ArgumentParser(description="Benchmark the AI Finance Controller")
    parser.add_argument("--records", type=int, default=500)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--with-llm", action="store_true", help="run real LLM investigation calls (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--regenerate", action="store_true", help="regenerate the dataset even if it already exists")
    args = parser.parse_args()

    run_benchmark(args.records, args.seed, args.with_llm, args.regenerate)


if __name__ == "__main__":
    main()
