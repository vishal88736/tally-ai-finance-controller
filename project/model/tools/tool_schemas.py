"""
Tool-use schemas (Anthropic /v1/messages `tools` format) for every function
in tools/finance_tools.py. Kept separate from the function implementations
so the schema (what the model is told) and the implementation (what
actually runs) can be diffed against each other easily.
"""

FINANCE_TOOL_SCHEMAS = [
    {
        "name": "get_transaction",
        "description": "Fetch a single transaction's full reconciliation summary by transaction or invoice id.",
        "input_schema": {
            "type": "object",
            "properties": {"transaction_id": {"type": "string"}},
            "required": ["transaction_id"],
        },
    },
    {
        "name": "search_transactions",
        "description": "Search transactions by merchant, reconciliation status, or amount range. Returns up to `limit` summaries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "merchant_id": {"type": "string"},
                "is_fully_reconciled": {"type": "boolean"},
                "min_amount": {"type": "number"},
                "max_amount": {"type": "number"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "find_matching_records",
        "description": "Look up any invoice/settlement/bank record by reference substring or approximate amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {"type": "string"},
                "amount": {"type": "number"},
            },
        },
    },
    {
        "name": "get_exception",
        "description": "Fetch one exception's full detail by exception id.",
        "input_schema": {
            "type": "object",
            "properties": {"exception_id": {"type": "string"}},
            "required": ["exception_id"],
        },
    },
    {
        "name": "get_exceptions",
        "description": "List exceptions, optionally filtered by category (e.g. FEE_DISCREPANCY) or status (open/resolved/escalated).",
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "status": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
        },
    },
    {
        "name": "get_reconciliation_metrics",
        "description": "Get the overall run metrics: match rate, precision, recall, F1, reconciled/unreconciled value, throughput.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_merchant_metrics",
        "description": "Get per-merchant reconciliation stats (exception counts, unreconciled value), optionally for one merchant_id.",
        "input_schema": {
            "type": "object",
            "properties": {"merchant_id": {"type": "string"}},
        },
    },
    {
        "name": "get_audit_history",
        "description": "Get the audit trail, optionally filtered to one transaction_id.",
        "input_schema": {
            "type": "object",
            "properties": {"transaction_id": {"type": "string"}},
        },
    },
    {
        "name": "calculate_settlement",
        "description": "Deterministically compute fee, tax, and net settlement amount for a given gross amount.",
        "input_schema": {
            "type": "object",
            "properties": {
                "gross_amount": {"type": "number"},
                "fee_pct": {"type": "number", "default": 0.02},
                "gst_pct": {"type": "number", "default": 0.18},
            },
            "required": ["gross_amount"],
        },
    },
    {
        "name": "get_policy",
        "description": "Fetch the full text of a named finance policy (settlement_rules, fee_rules, tax_rules, reconciliation_policy, exception_policy).",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
]
