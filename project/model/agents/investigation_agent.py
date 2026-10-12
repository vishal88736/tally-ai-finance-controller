"""
Investigation agent.

This is the ONLY place an LLM is asked to reason about a specific
exception, and it is deliberately constrained:
  - All arithmetic (expected fee, expected tax, expected net, date gaps)
    is computed in Python BEFORE the model ever sees the exception --
    the model is handed the numbers, not asked to derive them.
  - Relevant policy chunks are retrieved and attached explicitly (RAG),
    not "known" by the model from training data.
  - The output is validated against the InvestigationResult Pydantic model.
    If the model's response doesn't parse into a valid classification, or
    its own stated confidence is low, the agent overrides it to ESCALATE
    rather than trust an unparseable or hedging answer.
"""

from typing import List

from llm.provider import get_provider
from models.schemas import (
    ExceptionCategory,
    Invoice,
    InvestigationClassification,
    InvestigationResult,
    ReconciliationException,
    Settlement,
    Transaction,
)
from policies.retrieval import retrieve

_SYSTEM_PROMPT = """You are a financial reconciliation investigator at a payments company.
You are given a specific exception with pre-computed evidence (all arithmetic has
already been done correctly in Python -- do not recompute or second-guess the numbers,
only interpret them) and relevant excerpts from the company's finance policies.

Classify the exception into exactly one of:
valid_fee_deduction, timing_difference, amount_mismatch, duplicate, missing_record,
reference_mismatch, tax_discrepancy, unknown

Decide status:
- "RESOLVE" only if the evidence and policy clearly explain the discrepancy.
- "ESCALATE" if the evidence is insufficient, contradictory, or you are not confident.
NEVER fabricate a resolution. An honest ESCALATE is always better than a wrong RESOLVE.

Respond with ONLY this JSON shape:
{"classification": "<one of the categories above>", "status": "RESOLVE"|"ESCALATE",
 "confidence": <0.0-1.0>, "reasoning": "<one or two sentences citing the evidence>",
 "evidence_cited": ["<short evidence strings you relied on>"],
 "policy_cited": ["<policy filenames you relied on>"]}"""


def _build_evidence_block(txn: Transaction, exc: ReconciliationException, cfg: dict) -> str:
    inv: Invoice = txn.invoice
    settle: Settlement = txn.settlement
    bank = txn.bank_transaction

    lines = [f"Exception category (rule-assigned): {exc.category.value}", f"Severity: {exc.severity.value}"]
    lines.append(f"Invoice: {inv.record_id}, amount ₹{inv.amount}, date {inv.date}, reference {inv.reference}")
    if settle:
        lines.append(
            f"Settlement: {settle.record_id}, gross ₹{settle.gross_amount}, fee ₹{settle.fee}, "
            f"tax ₹{settle.tax}, net ₹{settle.net_amount}, date {settle.settlement_date}"
        )
    else:
        lines.append("Settlement: NONE FOUND")
    if bank:
        lines.append(f"Bank transaction: {bank.record_id}, amount ₹{bank.amount}, date {bank.date}, reference {bank.reference}")
    else:
        lines.append("Bank transaction: NONE FOUND")

    if inv.amount:
        expected_fee = round(inv.amount * cfg.get("fee_pct", 0.02), 2)
        expected_tax = round(expected_fee * cfg.get("gst_pct", 0.18), 2)
        expected_net = round(inv.amount - expected_fee - expected_tax, 2)
        lines.append(f"Pre-computed expectation (Python, not the model): fee ₹{expected_fee}, tax ₹{expected_tax}, expected net ₹{expected_net}")

    lines.append(f"Rule-engine evidence notes: {exc.evidence}")
    lines.append(f"Rule-engine confidence score: {exc.confidence}")
    return "\n".join(lines)


def investigate_exception(txn: Transaction, exc: ReconciliationException, cfg: dict = None) -> InvestigationResult:
    cfg = cfg or {}
    evidence_block = _build_evidence_block(txn, exc, cfg)

    query = f"{exc.category.value} {' '.join(exc.evidence)}"
    policy_chunks = retrieve(query, top_k=3)
    policy_text = "\n".join(f"[{c.source}] {c.text}" for c in policy_chunks)

    user_prompt = f"EVIDENCE:\n{evidence_block}\n\nRELEVANT POLICY EXCERPTS:\n{policy_text}\n\nClassify this exception now."

    try:
        provider = get_provider()
        raw = provider.complete_json(_SYSTEM_PROMPT, user_prompt, max_tokens=512)
        result = InvestigationResult(
            exception_id=exc.exception_id,
            classification=InvestigationClassification(raw.get("classification", "unknown")),
            status=raw.get("status", "ESCALATE"),
            confidence=float(raw.get("confidence", 0.0)),
            reasoning=raw.get("reasoning", ""),
            evidence_cited=raw.get("evidence_cited", []),
            policy_cited=raw.get("policy_cited", []),
        )
    except Exception as exc_err:
        # unparseable / API failure -- never guess, always escalate
        result = InvestigationResult(
            exception_id=exc.exception_id,
            classification=InvestigationClassification.UNKNOWN,
            status="ESCALATE",
            confidence=0.0,
            reasoning=f"Investigation could not be completed: {exc_err}",
            evidence_cited=[],
            policy_cited=[],
        )

    # never trust a low-confidence "RESOLVE" -- override to ESCALATE
    if result.status == "RESOLVE" and result.confidence < 0.7:
        result.status = "ESCALATE"
        result.reasoning += " (auto-downgraded to ESCALATE: model confidence below 0.7 threshold)"

    return result
