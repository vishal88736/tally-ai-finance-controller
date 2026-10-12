"""
File loading utilities.

Each uploaded document (regardless of source system: bank statement export,
invoicing tool, payment gateway log, tax ledger, etc.) is loaded into a
list of plain dicts -- one dict per row -- with the original column names
untouched. Normalization into a canonical schema happens later, in
agents/normalizer_node.py, because the *meaning* of columns differs per
document and is best resolved with an LLM rather than brittle heuristics.
"""

import json
import os
from typing import Any, Dict, List, Tuple

import pandas as pd

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls", ".json", ".tsv"}


def load_file(path: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Load a single file into (rows, column_names)."""
    ext = os.path.splitext(path)[1].lower()

    if ext == ".csv":
        df = pd.read_csv(path)
    elif ext == ".tsv":
        df = pd.read_csv(path, sep="\t")
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    elif ext == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Accept either a bare list of records or {"records": [...]}
        if isinstance(data, dict):
            data = data.get("records", data.get("data", []))
        df = pd.DataFrame(data)
    else:
        raise ValueError(
            f"Unsupported file type '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}"
        )

    df = df.dropna(how="all")  # drop fully-blank rows
    df.columns = [str(c).strip() for c in df.columns]
    rows = json.loads(df.to_json(orient="records"))
    return rows, list(df.columns)
