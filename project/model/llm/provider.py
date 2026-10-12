"""
LLM provider abstraction.

Every agent talks to `get_provider()` and calls `.complete_json(...)` or
`.complete_with_tools(...)`. Nothing in graph/ or agents/ imports a vendor
SDK directly -- swap the provider by changing `LLM_PROVIDER` in the
environment, not by editing agent code.
"""

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class LLMProvider(ABC):
    """Common interface every model backend must implement."""

    @abstractmethod
    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict:
        """Return a parsed JSON object from a system/user prompt pair."""
        raise NotImplementedError

    @abstractmethod
    def complete_with_tools(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        """
        Run one turn of a tool-use conversation. Returns a dict:
            {"stop_reason": "tool_use" | "end_turn",
             "text": "<any text content>",
             "tool_calls": [{"id":..., "name":..., "input": {...}}, ...]}
        """
        raise NotImplementedError


def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}|\[.*\]", cleaned, flags=re.DOTALL)
    payload = match.group(0) if match else cleaned
    return json.loads(payload)


class ClaudeProvider(LLMProvider):
    """Default provider. Uses the Anthropic SDK directly (no LangChain
    dependency needed for this layer, keeping tool-use handling explicit)."""

    def __init__(self, model: Optional[str] = None):
        import anthropic  # imported lazily so the rest of the app works without the SDK installed

        self.model = model or os.environ.get("RECONCILER_MODEL", "claude-sonnet-4-6")
        self.client = anthropic.Anthropic()

    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return _extract_json(text)

    def complete_with_tools(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 1024,
    ) -> Dict[str, Any]:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=tools,
        )
        text_parts = [b.text for b in response.content if b.type == "text"]
        tool_calls = [
            {"id": b.id, "name": b.name, "input": b.input}
            for b in response.content
            if b.type == "tool_use"
        ]
        return {
            "stop_reason": response.stop_reason,
            "text": "\n".join(text_parts),
            "tool_calls": tool_calls,
            "raw_content": response.content,  # needed to round-trip into the next turn
        }


class OpenAIProvider(LLMProvider):
    """Stub -- implement when an OpenAI key is configured. Kept here so the
    interface (and the fact that switching providers is a config change,
    not a graph rewrite) is demonstrated end to end."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o")

    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict:
        raise NotImplementedError("Install/configure the openai SDK to enable OpenAIProvider.")

    def complete_with_tools(self, system, messages, tools, max_tokens=1024):
        raise NotImplementedError("Install/configure the openai SDK to enable OpenAIProvider.")


class GeminiProvider(LLMProvider):
    """Stub -- same rationale as OpenAIProvider."""

    def __init__(self, model: Optional[str] = None):
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict:
        raise NotImplementedError("Install/configure the google-generativeai SDK to enable GeminiProvider.")

    def complete_with_tools(self, system, messages, tools, max_tokens=1024):
        raise NotImplementedError("Install/configure the google-generativeai SDK to enable GeminiProvider.")


_PROVIDERS = {
    "claude": ClaudeProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}

_provider_instance: Optional[LLMProvider] = None


def get_provider() -> LLMProvider:
    """Singleton provider selected via the LLM_PROVIDER env var (default: claude)."""
    global _provider_instance
    if _provider_instance is None:
        name = os.environ.get("LLM_PROVIDER", "claude").lower()
        if name not in _PROVIDERS:
            raise ValueError(f"Unknown LLM_PROVIDER '{name}'. Options: {list(_PROVIDERS)}")
        _provider_instance = _PROVIDERS[name]()
    return _provider_instance
