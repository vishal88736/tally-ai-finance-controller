# Settlement Timing Policy

- A settlement's `settlement_date` may fall **up to 3 business days** after
  the invoice/payment date. This is the normal payment-gateway settlement
  cycle and is NOT an exception on its own.
- A gap of 4-7 days should be classified as `timing_difference` but flagged
  `MEDIUM` severity for review, since it exceeds the standard cycle.
- A gap of more than 7 days should be treated as a genuine anomaly
  (`AMBIGUOUS_MATCH` or `MISSING_SETTLEMENT`), not a timing difference.
- Weekend and bank-holiday settlement delays are common and should be
  weighed leniently when the gap is 1-2 days beyond the window.
