# Tax (GST) Policy

- GST is charged on the gateway fee, not on the gross invoice amount.
- Standard GST rate on gateway fees is **18%**, configurable via
  `DEFAULT_GST_PCT`.
- Tax = `round(fee * gst_pct, 2)`.
- Expected net settlement amount = `gross_amount - fee - tax`.
- A settlement whose net amount differs from the expected net amount by
  more than ₹1 after accounting for fee and tax should be classified as
  `TAX_DISCREPANCY` if the fee itself is correct, or `AMOUNT_MISMATCH`
  if neither fee nor tax explains the gap.
