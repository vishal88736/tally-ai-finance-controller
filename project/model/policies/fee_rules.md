# Gateway Fee Policy

- The standard payment gateway fee is **2% of the gross invoice amount**,
  configurable via `DEFAULT_FEE_PCT`.
- Fee = `round(gross_amount * fee_pct, 2)`.
- A settlement whose `fee` differs from the expected fee by more than
  ₹1 (or 0.5%, whichever is larger) should be classified as
  `FEE_DISCREPANCY`, not silently accepted.
- Some legacy merchants are on a flat ₹5 fee instead of a percentage fee.
  If the percentage-based expected fee doesn't reconcile, check whether the
  flat fee explains the gap before escalating.
