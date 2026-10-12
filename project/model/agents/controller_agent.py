"""
"Ask Finance Controller" -- a natural-language interface backed entirely by
tool calls, never by dumping the transaction/exception database into the
prompt. See tools/finance_tools.py and tools/tool_schemas.py.
"""

from functools import partial
from typing import Any, Dict, List

from llm.provider import get_provider
from services.store import ReconciliationRun
from tools.finance_tools import TOOL_FUNCTIONS
from tools.tool_schemas import FINANCE_TOOL_SCHEMAS

_SYSTEM_PROMPT = """You are the AI Finance Controller for a payments reconciliation system.
Answer questions about reconciliation status, exceptions, metrics, and policy using ONLY
the provided tools -- never guess a number or invoice id you haven't looked up.
Be concise and precise: cite specific ids, amounts, and counts from tool results.
If a tool returns an error or empty result, say so plainly rather than making something up."""

_MAX_TOOL_TURNS = 6


def ask_finance_controller(run: ReconciliationRun, question: str) -> Dict[str, Any]:
    provider = get_provider()
    bound_tools = {name: partial(fn, run) for name, fn in TOOL_FUNCTIONS.items()}
    # calculate_settlement and get_policy don't take `run` as first arg -- unbind those
    bound_tools["calculate_settlement"] = TOOL_FUNCTIONS["calculate_settlement"]
    bound_tools["get_policy"] = TOOL_FUNCTIONS["get_policy"]

    messages: List[Dict[str, Any]] = [{"role": "user", "content": question}]
    tool_call_log: List[Dict[str, Any]] = []

    for _ in range(_MAX_TOOL_TURNS):
        response = provider.complete_with_tools(_SYSTEM_PROMPT, messages, FINANCE_TOOL_SCHEMAS, max_tokens=1024)

        if response["stop_reason"] != "tool_use":
            return {"answer": response["text"], "tool_calls": tool_call_log}

        messages.append({"role": "assistant", "content": response["raw_content"]})
        tool_results = []
        for call in response["tool_calls"]:
            fn = bound_tools.get(call["name"])
            try:
                result = fn(**call["input"]) if fn else {"error": f"Unknown tool '{call['name']}'"}
            except Exception as exc:
                result = {"error": str(exc)}
            tool_call_log.append({"tool": call["name"], "input": call["input"], "result": result})
            tool_results.append(
                {"type": "tool_result", "tool_use_id": call["id"], "content": str(result)}
            )
        messages.append({"role": "user", "content": tool_results})

    return {
        "answer": "I looked into this across several tool calls but couldn't reach a final answer within the turn limit. Try narrowing the question.",
        "tool_calls": tool_call_log,
    }
