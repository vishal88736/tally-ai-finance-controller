"""
State for the AI Finance Controller LangGraph pipeline.
"""

from typing import Any, Dict, List, Optional, TypedDict

from evaluation.ground_truth import GroundTruthEntry
from models.schemas import (
    Invoice,
    BankTransaction,
    ReconciliationException,
    ReconciliationMetrics,
    ResolutionAction,
    Settlement,
    Transaction,
)


class ControllerState(TypedDict, total=False):
    # inputs
    invoices: List[Invoice]
    settlements: List[Settlement]
    bank_transactions: List[BankTransaction]
    config: Dict[str, Any]
    ground_truth: Optional[Dict[str, GroundTruthEntry]]
    enable_llm_investigation: bool  # allow disabling for offline/no-key benchmarking

    # working data
    transactions: List[Transaction]
    exceptions: List[ReconciliationException]
    resolutions: List[ResolutionAction]
    audit_entries: List[Dict[str, Any]]

    # output
    metrics: ReconciliationMetrics
    report: Dict[str, Any]

    # diagnostics
    llm_calls: int
    llm_time_seconds: float
    deterministic_time_seconds: float
    total_time_seconds: float
    log: List[str]
