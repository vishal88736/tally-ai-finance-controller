# Exception Handling Policy

- Every exception must carry a category from the fixed taxonomy
  (MISSING_BANK_TRANSACTION, MISSING_SETTLEMENT, AMOUNT_MISMATCH,
  DATE_MISMATCH, REFERENCE_MISMATCH, DUPLICATE_TRANSACTION,
  FEE_DISCREPANCY, TAX_DISCREPANCY, AMBIGUOUS_MATCH, UNKNOWN).
- Severity guidance:
  - CRITICAL: unreconciled value > ₹50,000, or a duplicate reconciliation
    risk (the same bank transaction claimed by two invoices).
  - HIGH: amount mismatch beyond tolerance with no fee/tax explanation.
  - MEDIUM: timing differences beyond the standard settlement window, fee
    or tax discrepancies within a plausible range.
  - LOW: cosmetic reference-format mismatches with strong amount+date
    agreement.
- If the investigation agent cannot explain a discrepancy using the
  available evidence and policies, it MUST return `status: "ESCALATE"`
  rather than fabricate a resolution. A wrong "resolved" is worse than an
  honest "unresolved".
