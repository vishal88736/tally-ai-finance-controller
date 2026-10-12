"""
The AI Finance Controller pipeline, built with LangGraph.

    START
      |
      v
    ingest                 (validate/count incoming records)
      |
      v
    run_engine              (candidate generation + deterministic scoring +
      |                      constrained assignment -- see matching/engine.py;
      |                      these three stages are combined into one engine
      |                      call rather than three separate graph nodes,
      |                      because they share the same candidate indices
      |                      and splitting them across node boundaries would
      |                      mean re-serializing those indices for no benefit)
      v
    confidence_gate
      |
      +---------------------------+
      v                           v
    AUTO_RECONCILE           needs investigation/review
      |                           |
      v                           v
    resolve_auto            investigate_and_resolve --- Transaction DB
      |                           |                 --- Finance Policies (RAG)
      |                           |                 --- Rule-engine evidence
      |                           v
      |                     (investigation_agent -> resolution_agent,
      |                      or straight to resolution_agent for tiers
      |                      that don't warrant a model call at all)
      +-------------+-------------+
                    v
              audit_logger
                    v
                 metrics
                    v
                 reporting
                    v
                   END
"""

import time

from agents.investigation_agent import investigate_exception
from agents.resolution_agent import resolve_auto, resolve_from_investigation, resolve_without_investigation
from evaluation.metrics import compute_metrics
from langgraph.graph import END, StateGraph
from matching.engine import DEFAULT_ENGINE_CONFIG, run_reconciliation
from models.schemas import ConfidenceTier
from services.audit import log_decision
from services.store import ReconciliationRun

from graph.state import ControllerState


def ingest_node(state: ControllerState) -> dict:
    log = list(state.get("log", []))
    log.append(
        f"ingest: {len(state['invoices'])} invoices, {len(state['settlements'])} settlements, "
        f"{len(state['bank_transactions'])} bank transactions"
    )
    return {"log": log}


def run_engine_node(state: ControllerState) -> dict:
    cfg = {**DEFAULT_ENGINE_CONFIG, **state.get("config", {})}
    result = run_reconciliation(state["invoices"], state["settlements"], state["bank_transactions"], cfg)
    log = list(state.get("log", []))
    log.append(
        f"matching engine: {len(result['transactions'])} transactions built, "
        f"{len(result['exceptions'])} exceptions, "
        f"deterministic time {result['stats']['deterministic_time_seconds']}s"
    )
    return {
        "transactions": result["transactions"],
        "exceptions": result["exceptions"],
        "deterministic_time_seconds": result["stats"]["deterministic_time_seconds"],
        "log": log,
    }


def investigate_and_resolve_node(state: ControllerState) -> dict:
    """
    This node covers BOTH branches of the confidence gate:
      - AUTO_RECONCILE transactions get a resolution record with no model call.
      - Everything else routes through investigation (if the confidence tier
        warrants it) then resolution.
    Combined into one node because the branching is per-transaction, not
    per-batch -- a conditional EDGE would only let us pick one path for the
    whole graph run, not transaction-by-transaction, so the gate is applied
    as in-node logic instead. See DEFAULT_ENGINE_CONFIG for the thresholds
    that define each tier.
    """
    transactions = state["transactions"]
    exceptions_by_txn = {}
    for exc in state["exceptions"]:
        exceptions_by_txn[exc.transaction_id] = exc

    enable_llm = state.get("enable_llm_investigation", True)
    cfg = {**DEFAULT_ENGINE_CONFIG, **state.get("config", {})}

    resolutions = []
    llm_calls = 0
    llm_time = 0.0

    for txn in transactions:
        exc = exceptions_by_txn.get(txn.transaction_id)

        if exc is None:
            resolutions.append(resolve_auto(txn))
            continue

        if txn.confidence_tier == ConfidenceTier.AI_INVESTIGATION and enable_llm:
            t0 = time.perf_counter()
            investigation = investigate_exception(txn, exc, cfg)
            llm_time += time.perf_counter() - t0
            llm_calls += 1
            resolutions.append(resolve_from_investigation(txn, exc, investigation))
        else:
            resolutions.append(resolve_without_investigation(txn, exc))

    log = list(state.get("log", []))
    log.append(f"investigation/resolution: {llm_calls} LLM calls, {round(llm_time, 3)}s LLM time")
    return {"resolutions": resolutions, "llm_calls": llm_calls, "llm_time_seconds": round(llm_time, 4), "log": log}


def audit_node(state: ControllerState) -> dict:
    """Writes one AuditEntry per transaction into a fresh in-memory run
    record (the API layer swaps this into the shared RunStore afterward)."""
    run = ReconciliationRun(run_id="pending")  # real run_id assigned by the API layer
    resolutions_by_txn = {r.transaction_id: r for r in state["resolutions"]}

    for txn in state["transactions"]:
        resolution = resolutions_by_txn.get(txn.transaction_id)
        if resolution is None:
            continue
        log_decision(
            run,
            transaction_id=txn.transaction_id,
            decision=resolution.action_type.value,
            confidence=resolution.confidence,
            evidence=resolution.evidence,
            policy_used=resolution.policy_used,
            reason=resolution.reason,
        )

    exceptions_by_id = {e.exception_id: e for e in state["exceptions"]}

    log = list(state.get("log", []))
    log.append(f"audit: {len(run.audit_log)} decisions logged")
    return {"audit_entries": [e.model_dump() for e in run.audit_log], "log": log}


def metrics_node(state: ControllerState) -> dict:
    total_records = len(state["invoices"]) + len(state["settlements"]) + len(state["bank_transactions"])
    total_time = state.get("deterministic_time_seconds", 0.0) + state.get("llm_time_seconds", 0.0)

    metrics = compute_metrics(
        state["transactions"],
        state["exceptions"],
        ground_truth=state.get("ground_truth"),
        processing_time_seconds=total_time,
        deterministic_time_seconds=state.get("deterministic_time_seconds", 0.0),
        llm_time_seconds=state.get("llm_time_seconds", 0.0),
        llm_calls=state.get("llm_calls", 0),
        total_records=total_records,
    )
    log = list(state.get("log", []))
    log.append(
        f"metrics: match_rate={metrics.match_rate}% precision={metrics.precision} "
        f"recall={metrics.recall} f1={metrics.f1} throughput={metrics.throughput_records_per_sec} rec/s"
    )
    return {"metrics": metrics, "total_time_seconds": round(total_time, 4), "log": log}


def report_node(state: ControllerState) -> dict:
    resolutions_by_txn = {r.transaction_id: r for r in state["resolutions"]}
    report = {
        "metrics": state["metrics"].model_dump(),
        "transactions": [
            {**t.model_dump(), "resolution": resolutions_by_txn[t.transaction_id].model_dump() if t.transaction_id in resolutions_by_txn else None}
            for t in state["transactions"]
        ],
        "exceptions": [e.model_dump() for e in state["exceptions"]],
        "audit_entries": state.get("audit_entries", []),
        "log": state.get("log", []),
    }
    return {"report": report}


def build_graph():
    workflow = StateGraph(ControllerState)

    workflow.add_node("ingest", ingest_node)
    workflow.add_node("run_engine", run_engine_node)
    workflow.add_node("investigate_and_resolve", investigate_and_resolve_node)
    workflow.add_node("audit", audit_node)
    workflow.add_node("metrics", metrics_node)
    workflow.add_node("report", report_node)

    workflow.set_entry_point("ingest")
    workflow.add_edge("ingest", "run_engine")
    workflow.add_edge("run_engine", "investigate_and_resolve")
    workflow.add_edge("investigate_and_resolve", "audit")
    workflow.add_edge("audit", "metrics")
    workflow.add_edge("metrics", "report")
    workflow.add_edge("report", END)

    return workflow.compile()


controller_graph = build_graph()
