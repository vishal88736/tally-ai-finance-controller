# Tally — AI Finance Controller (Razorpay Buildathon)

Autonomous reconciliation, investigation, and exception resolution across
the full financial lifecycle: **invoice → settlement → bank transaction.**

Not "an LLM that compares CSV files" — a system where deterministic code
owns every calculation and matching decision, and the LLM is reserved for
genuinely ambiguous cases, always with cited evidence, and always with the
option to honestly escalate instead of guessing.

```
.
├── model/            LangGraph pipeline + FastAPI backend (Python)
│   ├── models/        transaction lifecycle data model
│   ├── matching/       constrained assignment engine (the core fix)
│   ├── evaluation/     precision/recall/F1 against ground truth
│   ├── policies/       finance policy docs + RAG-lite retrieval
│   ├── llm/            provider abstraction (Claude live, OpenAI/Gemini stubs)
│   ├── tools/          10 structured tools for the natural-language controller
│   ├── agents/         investigation, resolution, controller agents
│   ├── graph/          the LangGraph pipeline itself
│   ├── services/       data loading, run store, audit log
│   └── api/            FastAPI app
├── website/           UI/UX dashboard (HTML/CSS/JS) — Reconcile, Overview,
│                        Exceptions, Exception Detail, Audit Trail, Ask Controller
├── scripts/
│   ├── generate_dataset.py   reproducible synthetic dataset + ground truth
│   └── benchmark.py          end-to-end benchmark CLI
├── data/synthetic/     generated datasets (100 / 500 / 1000 records, seed 42)
└── tests/              23 tests: matching, one-to-one constraint, evaluation, escalation
```

## Quick start

```bash
# 1. Backend
cd model
pip install -r requirements.txt --break-system-packages
cp .env.example .env        # add ANTHROPIC_API_KEY (optional — see below)
uvicorn api.app:app --reload --port 8000

# 2. Frontend — just open website/index.html in a browser
#    (talks to http://localhost:8000 by default; change under Settings)

# 3. Generate data + run the benchmark
cd ..
python scripts/generate_dataset.py --records 500 --seed 42
python scripts/benchmark.py --records 500 --seed 42

# 4. Run the tests
pytest tests/ -v
```

**No API key?** Everything still works. The investigation agent and Ask
Controller fail *safely* — they escalate or return a clear error, never a
guess or a crash. Deterministic matching, evaluation, and the dashboard
all work fully offline.

## The core fix

The brief's central problem: a naive similarity matcher can reconcile two
different ₹10,000 invoices against the *same* ₹10,000 bank transaction.
`model/matching/assignment.py` replaces generic clustering with
**constrained one-to-one greedy assignment** — verified via
`tests/test_matching.py::test_one_to_one_constraint_prevents_double_reconciliation`
and confirmed at scale: **zero duplicate bank claims across 100/500/1000
record benchmarks.**

## Verified results (500 records, seed 42, no LLM key)

```
Match rate:               74.2%      Auto-resolved:      392
Precision:                99.7%      Human review:        70
Recall:                   82.0%      Unresolved:           38
F1:                       90.0%      Exceptions:          129
Throughput:          6,894.7 rec/s   Duplicate claims:      0
```

See `model/README.md` for the full architecture writeup, and the final
summary below for a point-by-point account of what changed and why.
