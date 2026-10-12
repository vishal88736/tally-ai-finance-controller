# Tally AI Finance Controller

Autonomous multi-agent financial controller for invoice matching, settlement reconciliation, tax matching, and audit-trail verification.

## Architecture

- **`project/model`**: FastAPI backend with LangGraph reconciliation pipelines, candidate matching, and LLM explanation layer.
- **`project/website`**: Web interface for viewing transactions, match explanations, and audit reports.
- **`project/data`**: Synthetic test benchmarks and ground-truth verification datasets.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
