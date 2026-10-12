"""
In-memory store for a single reconciliation run.

Keyed by run_id so the API can support multiple concurrent runs (e.g. one
per browser tab) without them clobbering each other. Not persisted to disk
-- this is a competition/demo backend, not a production data store; see
README for what would need to change for that.
"""

import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from models.schemas import (
    AuditEntry,
    ReconciliationException,
    ReconciliationMetrics,
    ResolutionAction,
    Transaction,
)


@dataclass
class ReconciliationRun:
    run_id: str
    transactions: List[Transaction] = field(default_factory=list)
    exceptions: Dict[str, ReconciliationException] = field(default_factory=dict)  # exception_id -> exception
    resolutions: List[ResolutionAction] = field(default_factory=list)
    audit_log: List[AuditEntry] = field(default_factory=list)
    metrics: Optional[ReconciliationMetrics] = None
    log: List[str] = field(default_factory=list)


class RunStore:
    def __init__(self):
        self._runs: Dict[str, ReconciliationRun] = {}

    def create(self) -> ReconciliationRun:
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        run = ReconciliationRun(run_id=run_id)
        self._runs[run_id] = run
        return run

    def get(self, run_id: str) -> Optional[ReconciliationRun]:
        return self._runs.get(run_id)


# module-level singleton -- fine for a single-process demo backend
store = RunStore()
