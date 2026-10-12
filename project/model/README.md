# AI Finance Controller — model/

A LangGraph-based agent that reconciles the full financial lifecycle
(invoice → settlement → bank transaction) using constrained, deterministic
matching, escalates genuinely ambiguous cases to an evidence-grounded LLM
investigator, and produces an auditable, ground-truth-verified report.

## Architecture

```
backend layout:
model/
├── models/schemas.py       Pydantic data model: Invoice, Settlement, BankTransaction,
│                            Transaction (lifecycle), ReconciliationException, ResolutionAction, AuditEntry
├── matching/
│   ├── candidate_generation.py   indexed lookup (reference + amount bucket), not O(n²)
│   ├── scoring.py                explainable weighted scoring (amount/reference/date/merchant/description)
│   ├── assignment.py             constrained one-to-one greedy assignment (see below)
│   └── engine.py                 multi-stage engine: invoice<->settlement<->bank chaining + exception categorization
├── evaluation/
│   ├── ground_truth.py     loads ground truth, compares engine output against it
│   └── metrics.py          precision / recall / F1 / throughput / value stats
├── policies/                5 markdown policy docs + lightweight keyword retrieval (RAG-lite)
├── llm/provider.py          LLMProvider abstraction (ClaudeProvider live; OpenAI/Gemini stubs)
├── tools/                   finance_tools.py (10 tools) + tool_schemas.py (Anthropic tool-use format)
├── agents/
│   ├── investigation_agent.py   LLM classifies exceptions using PRE-COMPUTED evidence + retrieved policy
│   ├── resolution_agent.py      deterministic mapping from investigation result -> resolution action
│   └── controller_agent.py      natural-language Q&A via tool-use loop (never dumps the dataset into the prompt)
├── graph/
│   ├── state.py             ControllerState TypedDict
│   └── pipeline.py          the LangGraph: ingest -> run_engine -> investigate_and_resolve -> audit -> metrics -> report
├── services/
│   ├── data_loader.py       fast CSV->Pydantic loader (no LLM calls for the canonical dataset path)
│   ├── store.py             in-memory multi-run store
│   └── audit.py             append-only audit log writer/query
└── api/app.py                FastAPI: /api/reconcile, /api/runs/{id}/{metrics,exceptions,audit,ask}, approve/escalate
```

## The core problem this engine fixes

A naive similarity-clustering matcher (e.g. union-find over "these look
similar" pairs) will happily merge two unrelated invoices of the same
amount into one bank transaction's cluster. `matching/assignment.py`
replaces that with **constrained one-to-one greedy assignment**: candidates
are sorted by score descending, and a match is only accepted if BOTH sides
are still unclaimed. See the module's docstring for why greedy-by-score
(not the Hungarian algorithm) was chosen, and `tests/test_matching.py`'s
`test_one_to_one_constraint_prevents_double_reconciliation` for the
regression test that guards it — verified with **zero duplicate bank
claims** across 100/500/1000-record benchmarks.

## Run it

```bash
cd model
pip install -r requirements.txt --break-system-packages   # or use a venv
cp .env.example .env   # add ANTHROPIC_API_KEY (needed for AI investigation + Ask Controller)
uvicorn api.app:app --reload --port 8000
```

Without an API key, everything still runs end-to-end: the investigation
agent catches the auth failure and returns `status: "ESCALATE"` rather
than crashing or guessing (verified in `tests/test_exceptions.py`).

## Generate datasets & run the benchmark

```bash
cd ..   # project root
python scripts/generate_dataset.py --records 500 --seed 42
python scripts/benchmark.py --records 500 --seed 42
```

Sample benchmark output (500 records, seed 42, no LLM key):
```
Match rate:               74.2%
Precision:                99.7%
Recall:                   82.0%
F1:                       90.0%
Auto-resolved:            392
Throughput:               6894.7 records/sec
Duplicate bank claims:    0 (constraint holds)
```

## Run the tests

```bash
pytest tests/ -v   # 23 tests: matching, one-to-one constraint, evaluation, exceptions/escalation
```

## API surface

| Endpoint | Purpose |
|---|---|
| `POST /api/reconcile` | Runs the full pipeline on invoices (+ optional settlements/bank/ground_truth CSVs) |
| `GET /api/runs/{id}/metrics` | Match rate, precision/recall/F1, throughput, value stats |
| `GET /api/runs/{id}/exceptions` | Filterable exception queue |
| `GET /api/runs/{id}/exceptions/{eid}` | Full detail: transaction, evidence, AI decision, resolution |
| `POST .../approve` / `.../escalate` | Human-in-the-loop approval gate |
| `GET /api/runs/{id}/audit` | Full audit trail, optionally filtered by transaction |
| `POST /api/runs/{id}/ask` | Natural-language Q&A via tool-use (never dumps the dataset into the prompt) |

## Known limitations
- In-memory run store (not persisted across server restarts) — fine for a demo/competition backend, would need a real datastore for production.
- OpenAI/Gemini providers are structural stubs, not wired to live SDKs.
- The Hungarian-algorithm alternative to greedy assignment is documented but not implemented (greedy already satisfies the one-to-one constraint and the regression tests; see `matching/assignment.py` docstring for the tradeoff).
