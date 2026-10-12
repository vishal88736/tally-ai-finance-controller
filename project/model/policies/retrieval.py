"""
Lightweight policy retrieval ("RAG-lite").

This is intentionally NOT a vector database. The policy corpus is a handful
of short markdown files -- for a corpus this small, embeddings add latency
and a dependency without adding retrieval quality. Instead, each policy
document is split into bullet-point chunks and scored against the query
with simple keyword overlap (still genuine retrieval: the investigation
agent gets only the relevant chunks, not the whole corpus dumped into the
prompt).

If the policy corpus grows meaningfully, swap `_score_chunk` for an
embedding-similarity lookup without changing the public `retrieve()` API.
"""

import os
import re
from dataclasses import dataclass
from typing import List

_POLICY_DIR = os.path.dirname(__file__)
_STOPWORDS = {"the", "a", "an", "is", "are", "of", "to", "and", "or", "in", "on", "for", "be", "not", "than"}


@dataclass
class PolicyChunk:
    source: str  # filename, e.g. "settlement_rules.md"
    text: str


def _load_chunks() -> List[PolicyChunk]:
    chunks: List[PolicyChunk] = []
    for fname in sorted(os.listdir(_POLICY_DIR)):
        if not fname.endswith(".md"):
            continue
        path = os.path.join(_POLICY_DIR, fname)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        for block in re.split(r"\n(?=- )|\n\n", content):
            block = block.strip()
            if block and not block.startswith("#"):
                chunks.append(PolicyChunk(source=fname, text=block))
    return chunks


_CHUNKS = _load_chunks()  # loaded once at import time


def _tokenize(text: str) -> set:
    words = re.findall(r"[a-zA-Z0-9%.]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def retrieve(query: str, top_k: int = 3) -> List[PolicyChunk]:
    """Return the top_k policy chunks most relevant to the query."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return _CHUNKS[:top_k]

    scored = []
    for chunk in _CHUNKS:
        chunk_tokens = _tokenize(chunk.text)
        overlap = len(query_tokens & chunk_tokens)
        if overlap:
            scored.append((overlap, chunk))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [chunk for _, chunk in scored[:top_k]] or _CHUNKS[:top_k]


def get_policy(topic: str) -> str:
    """Return the full text of a named policy file, e.g. 'fee_rules'."""
    fname = topic if topic.endswith(".md") else f"{topic}.md"
    path = os.path.join(_POLICY_DIR, fname)
    if not os.path.exists(path):
        available = [f for f in os.listdir(_POLICY_DIR) if f.endswith(".md")]
        return f"No such policy '{fname}'. Available: {available}"
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
