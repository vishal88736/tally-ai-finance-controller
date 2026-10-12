"""
Core data model for the AI Finance Controller.

The central idea: a financial "Transaction" is a LIFECYCLE, not a row.
It may be composed of an invoice, a payment, a settlement, and a bank
transaction -- and reconciliation means asking "does the complete chain
add up?", not "do these two rows look similar?".

    Transaction
    ├── invoice            (what was billed)
    ├── settlement         (what the payment processor actually paid out,
    │                       net of fees/tax)
    └── bank_transaction   (what actually landed in the bank account)

Every one of these pieces is optional on a given Transaction -- that's
precisely what missing_bank_transaction / missing_settlement exceptions
are for.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------
# Source records (one canonical schema per source system)
# --------------------------------------------------------------------------

class SourceRecord(BaseModel):
    record_id: str
    source: str  # "invoice" | "bank" | "settlement"
    reference: Optional[str] = None
    amount: Optional[float] = None
    currency: str = "INR"
    date: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


class Invoice(SourceRecord):
    source: str = "invoice"
    customer_id: Optional[str] = None
    merchant_id: Optional[str] = None
    status: Optional[str] = None
    description: Optional[str] = None


class BankTransaction(SourceRecord):
    source: str = "bank"
    description: Optional[str] = None


class Settlement(SourceRecord):
    source: str = "settlement"
    payment_reference: Optional[str] = None
    gross_amount: Optional[float] = None
    fee: Optional[float] = None
    tax: Optional[float] = None
    net_amount: Optional[float] = None
    settlement_date: Optional[str] = None
    merchant_id: Optional[str] = None


# --------------------------------------------------------------------------
# Confidence tiers
# --------------------------------------------------------------------------

class ConfidenceTier(str, Enum):
    AUTO_RECONCILE = "AUTO_RECONCILE"
    AI_INVESTIGATION = "AI_INVESTIGATION"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    UNRESOLVED = "UNRESOLVED"


# --------------------------------------------------------------------------
# The reconciled lifecycle unit
# --------------------------------------------------------------------------

class MatchEvidence(BaseModel):
    amount_score: float = 0.0
    reference_score: float = 0.0
    date_score: float = 0.0
    merchant_score: float = 0.0
    description_score: float = 0.0
    weighted_score: float = 0.0
    notes: List[str] = Field(default_factory=list)


class Transaction(BaseModel):
    transaction_id: str
    invoice: Optional[Invoice] = None
    settlement: Optional[Settlement] = None
    bank_transaction: Optional[BankTransaction] = None

    confidence: float = 0.0
    confidence_tier: ConfidenceTier = ConfidenceTier.UNRESOLVED
    evidence: MatchEvidence = Field(default_factory=MatchEvidence)
    method: str = "unmatched"  # "exact" | "scored" | "llm_arbitration" | "unmatched"
    is_fully_reconciled: bool = False


# --------------------------------------------------------------------------
# Exception taxonomy
# --------------------------------------------------------------------------

class ExceptionCategory(str, Enum):
    MISSING_BANK_TRANSACTION = "MISSING_BANK_TRANSACTION"
    MISSING_SETTLEMENT = "MISSING_SETTLEMENT"
    AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
    DATE_MISMATCH = "DATE_MISMATCH"
    REFERENCE_MISMATCH = "REFERENCE_MISMATCH"
    DUPLICATE_TRANSACTION = "DUPLICATE_TRANSACTION"
    FEE_DISCREPANCY = "FEE_DISCREPANCY"
    TAX_DISCREPANCY = "TAX_DISCREPANCY"
    AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
    UNKNOWN = "UNKNOWN"


class ExceptionSeverity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ExceptionStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class ReconciliationException(BaseModel):
    exception_id: str
    transaction_id: str
    category: ExceptionCategory
    severity: ExceptionSeverity
    evidence: List[str] = Field(default_factory=list)
    candidate_matches: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    recommended_action: Optional[str] = None
    status: ExceptionStatus = ExceptionStatus.OPEN
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# --------------------------------------------------------------------------
# Investigation (LLM) output -- validated before it's trusted
# --------------------------------------------------------------------------

class InvestigationClassification(str, Enum):
    VALID_FEE_DEDUCTION = "valid_fee_deduction"
    TIMING_DIFFERENCE = "timing_difference"
    AMOUNT_MISMATCH = "amount_mismatch"
    DUPLICATE = "duplicate"
    MISSING_RECORD = "missing_record"
    REFERENCE_MISMATCH = "reference_mismatch"
    TAX_DISCREPANCY = "tax_discrepancy"
    UNKNOWN = "unknown"


class InvestigationResult(BaseModel):
    exception_id: str
    classification: InvestigationClassification
    status: str  # "RESOLVE" | "ESCALATE"
    confidence: float
    reasoning: str
    evidence_cited: List[str] = Field(default_factory=list)
    policy_cited: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Resolution
# --------------------------------------------------------------------------

class ResolutionActionType(str, Enum):
    AUTO_RECONCILE = "AUTO_RECONCILE"
    MARK_AS_TIMING_DIFFERENCE = "MARK_AS_TIMING_DIFFERENCE"
    MARK_AS_FEE_VARIANCE = "MARK_AS_FEE_VARIANCE"
    MARK_AS_TAX_VARIANCE = "MARK_AS_TAX_VARIANCE"
    MERGE_DUPLICATE = "MERGE_DUPLICATE"
    REQUEST_HUMAN_REVIEW = "REQUEST_HUMAN_REVIEW"
    ESCALATE = "ESCALATE"


class ResolutionAction(BaseModel):
    action_id: str
    transaction_id: str
    exception_id: Optional[str] = None
    action_type: ResolutionActionType
    reason: str
    evidence: List[str] = Field(default_factory=list)
    confidence: float
    policy_used: List[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    agent_version: str = "v2.0"
    approved: bool = False  # human-approval gate; True once a controller signs off


# --------------------------------------------------------------------------
# Audit trail
# --------------------------------------------------------------------------

class AuditEntry(BaseModel):
    transaction_id: str
    decision: str
    confidence: float
    evidence: List[str] = Field(default_factory=list)
    policy_used: List[str] = Field(default_factory=list)
    reason: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    agent_version: str = "v2.0"


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

class ReconciliationMetrics(BaseModel):
    total_records: int = 0
    total_transactions: int = 0

    match_rate: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    false_positives: int = 0
    false_negatives: int = 0

    auto_resolved: int = 0
    human_review: int = 0
    unresolved: int = 0
    exception_count: int = 0
    auto_resolution_rate: float = 0.0

    reconciled_value: float = 0.0
    unreconciled_value: float = 0.0

    processing_time_seconds: float = 0.0
    deterministic_time_seconds: float = 0.0
    llm_time_seconds: float = 0.0
    throughput_records_per_sec: float = 0.0
    llm_calls: int = 0
