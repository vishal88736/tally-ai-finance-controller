# Reconciliation Policy

## Confidence tiers
- **>= 95%**: AUTO_RECONCILE. No human or LLM review required.
- **80% - 94.99%**: AI_INVESTIGATION. Route to the investigation agent with
  full evidence before any action is taken.
- **50% - 79.99%**: HUMAN_REVIEW. The AI may recommend an action but a human
  controller must approve it before it is treated as final.
- **< 50%**: UNRESOLVED. Do not guess. Escalate.

## One-to-one constraint
- A single bank transaction may reconcile against at most one invoice's
  lifecycle, and a single invoice's lifecycle may reconcile against at most
  one bank transaction, UNLESS the dataset explicitly marks the transaction
  type as a split or aggregate settlement.
- When multiple invoices are equally plausible matches for the same bank
  transaction, only the highest-scoring one may be auto-reconciled; the
  rest must become exceptions (`AMBIGUOUS_MATCH` or `DUPLICATE_TRANSACTION`)
  rather than being silently matched.

## Evidence requirement
- Never mark a transaction reconciled without at least an amount signal
  AND one of: a reference match or a date-window match. Confidence must be
  computed from these signals, not asserted.
