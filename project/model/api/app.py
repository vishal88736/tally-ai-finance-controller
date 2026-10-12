"""
HTTP entrypoint for the AI Finance Controller.

Run with:
    uvicorn api.app:app --reload --port 8000
"""

import os
import shutil
import tempfile
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from fastapi.staticfiles import StaticFiles

from agents.controller_agent import ask_finance_controller
from evaluation.ground_truth import load_ground_truth
from graph.pipeline import controller_graph
from models.schemas import ExceptionStatus, ResolutionActionType
from services.audit import log_decision
from services.data_loader import load_bank_transactions, load_invoices, load_settlements
from services.store import store

load_dotenv()

app = FastAPI(title="AI Finance Controller API", version="2.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.post("/api/reconcile")
async def reconcile(
    invoices_file: UploadFile = File(...),
    settlements_file: Optional[UploadFile] = File(None),
    bank_file: Optional[UploadFile] = File(None),
    ground_truth_file: Optional[UploadFile] = File(None),
    enable_llm_investigation: bool = Form(True),
    amount_tolerance_abs: float = Form(1.0),
    amount_tolerance_pct: float = Form(0.005),
    date_window_days: int = Form(3),
    auto_reconcile_threshold: float = Form(95.0),
    ai_investigation_threshold: float = Form(80.0),
    human_review_threshold: float = Form(50.0),
):
    """
    Runs the full LangGraph pipeline on canonical-schema CSVs (see
    scripts/generate_dataset.py for the expected columns) and returns a
    run_id plus the full report. Settlement and bank files are optional so
    the "missing settlement"/"missing bank transaction" scenarios can be
    demoed with a subset of files.
    """
    tmp_dir = tempfile.mkdtemp(prefix="reconcile_")
    try:
        def save(upload: UploadFile) -> str:
            dest = os.path.join(tmp_dir, upload.filename)
            with open(dest, "wb") as f:
                shutil.copyfileobj(upload.file, f)
            return dest

        invoices = load_invoices(save(invoices_file))
        settlements = load_settlements(save(settlements_file)) if settlements_file else []
        banks = load_bank_transactions(save(bank_file)) if bank_file else []
        ground_truth = load_ground_truth(save(ground_truth_file)) if ground_truth_file else None

        initial_state = {
            "invoices": invoices,
            "settlements": settlements,
            "bank_transactions": banks,
            "ground_truth": ground_truth,
            "enable_llm_investigation": enable_llm_investigation,
            "config": {
                "amount_tolerance_abs": amount_tolerance_abs,
                "amount_tolerance_pct": amount_tolerance_pct,
                "date_window_days": date_window_days,
                "auto_reconcile_threshold": auto_reconcile_threshold,
                "ai_investigation_threshold": ai_investigation_threshold,
                "human_review_threshold": human_review_threshold,
            },
        }

        final_state = controller_graph.invoke(initial_state)

        run = store.create()
        run.transactions = final_state["transactions"]
        run.exceptions = {e.exception_id: e for e in final_state["exceptions"]}
        run.resolutions = final_state["resolutions"]
        run.metrics = final_state["metrics"]
        run.log = final_state["log"]

        # the pipeline logs audit entries against a throwaway run object (it
        # doesn't know the real run_id yet, since that's assigned by this API
        # layer) -- re-log them here against the real, persisted run instead.
        run.audit_log = []
        resolutions_by_txn = {r.transaction_id: r for r in run.resolutions}
        for txn in run.transactions:
            r = resolutions_by_txn.get(txn.transaction_id)
            if r:
                log_decision(run, txn.transaction_id, r.action_type.value, r.confidence, r.evidence, r.policy_used, r.reason)

        return {"run_id": run.run_id, "report": final_state["report"]}

    except Exception as exc:
        return {"error": str(exc)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/api/runs/{run_id}/metrics")
def get_metrics(run_id: str):
    run = store.get(run_id)
    if not run:
        return {"error": "run not found"}
    return run.metrics.model_dump() if run.metrics else {"error": "metrics not ready"}


@app.get("/api/runs/{run_id}/exceptions")
def list_exceptions(run_id: str, category: Optional[str] = None, status: Optional[str] = None):
    run = store.get(run_id)
    if not run:
        return {"error": "run not found"}
    results = list(run.exceptions.values())
    if category:
        results = [e for e in results if e.category.value == category]
    if status:
        results = [e for e in results if e.status.value == status]
    return [e.model_dump() for e in results]


@app.get("/api/runs/{run_id}/exceptions/{exception_id}")
def get_exception_detail(run_id: str, exception_id: str):
    run = store.get(run_id)
    if not run:
        return {"error": "run not found"}
    exc = run.exceptions.get(exception_id)
    if not exc:
        return {"error": "exception not found"}
    txn = next((t for t in run.transactions if t.transaction_id == exc.transaction_id), None)
    resolution = next((r for r in run.resolutions if r.exception_id == exception_id), None)
    return {
        "exception": exc.model_dump(),
        "transaction": txn.model_dump() if txn else None,
        "resolution": resolution.model_dump() if resolution else None,
    }


@app.post("/api/runs/{run_id}/exceptions/{exception_id}/approve")
def approve_exception(run_id: str, exception_id: str):
    run = store.get(run_id)
    if not run:
        return {"error": "run not found"}
    exc = run.exceptions.get(exception_id)
    if not exc:
        return {"error": "exception not found"}
    exc.status = ExceptionStatus.RESOLVED
    for r in run.resolutions:
        if r.exception_id == exception_id:
            r.approved = True
    log_decision(run, exc.transaction_id, "HUMAN_APPROVED", 1.0, exc.evidence, [], "Approved by human controller via dashboard.")
    return {"status": "approved", "exception": exc.model_dump()}


@app.post("/api/runs/{run_id}/exceptions/{exception_id}/escalate")
def escalate_exception(run_id: str, exception_id: str):
    run = store.get(run_id)
    if not run:
        return {"error": "run not found"}
    exc = run.exceptions.get(exception_id)
    if not exc:
        return {"error": "exception not found"}
    exc.status = ExceptionStatus.ESCALATED
    log_decision(run, exc.transaction_id, "HUMAN_ESCALATED", 1.0, exc.evidence, [], "Escalated by human controller via dashboard.")
    return {"status": "escalated", "exception": exc.model_dump()}


@app.get("/api/runs/{run_id}/audit")
def get_audit(run_id: str, transaction_id: Optional[str] = None):
    run = store.get(run_id)
    if not run:
        return {"error": "run not found"}
    entries = run.audit_log
    if transaction_id:
        entries = [e for e in entries if e.transaction_id == transaction_id]
    return [e.model_dump() for e in entries]


@app.post("/api/runs/{run_id}/ask")
def ask(run_id: str, question: str = Form(...)):
    run = store.get(run_id)
    if not run:
        return {"error": "run not found"}
    try:
        return ask_finance_controller(run, question)
    except Exception as exc:
        return {
            "answer": None,
            "error": f"The Finance Controller agent couldn't complete this request: {exc}. "
                     f"Check that an LLM provider is configured (ANTHROPIC_API_KEY).",
            "tool_calls": [],
        }


# Mount frontend static files
website_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "website"))
if os.path.exists(website_dir):
    app.mount("/", StaticFiles(directory=website_dir, html=True), name="website")
