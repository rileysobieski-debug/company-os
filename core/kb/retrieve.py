"""
Knowledge-base retrieval — kb.query primitive
==============================================
Returns the top-K chunks for a query. This is the stable contract the
`employee.kb-retriever` agentic skill calls through.

Current backend: keyword scoring over chunk body + frontmatter metadata.
Deterministic, zero external deps — works in CI without network or an
embedding service. sqlite-vec + real embeddings land later; the return
shape stays identical so the swap is a drop-in.

Design notes:
  - Stopword filter strips the most common English tokens so queries
    don't score on noise.
  - `tag_filter` is a string that must appear in source_path; keeps
    per-department KB scoping trivial until we add real tag metadata.
  - Ties broken by chunk_index then source_path for determinism.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from core.kb.store import Chunk, iter_chunks

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to",
    "for", "with", "from", "by", "is", "are", "was", "were", "be", "been",
    "being", "it", "its", "this", "that", "these", "those", "as", "if",
    "then", "so", "not", "no", "do", "does", "did", "have", "has", "had",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
}


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


@dataclass(frozen=True)
class Match:
    chunk: Chunk
    score: float


def _score(query_tokens: list[str], chunk: Chunk) -> float:
    if not query_tokens:
        return 0.0
    body_tokens = _tokenize(chunk.body)
    if not body_tokens:
        return 0.0
    counts: dict[str, int] = {}
    for t in body_tokens:
        counts[t] = counts.get(t, 0) + 1
    total = 0.0
    for qt in query_tokens:
        if qt in counts:
            # Mild sublinear weighting — repeated hits help, but diminishing.
            total += 1.0 + 0.2 * (counts[qt] - 1)
    # Short chunks win ties over long ones (info density proxy).
    length_penalty = 1.0 / (1.0 + 0.0005 * len(body_tokens))
    return total * length_penalty


def kb_query(
    company_dir: Path,
    query: str,
    k: int = 5,
    tag_filter: str | None = None,
) -> list[Match]:
    """Return top-K matches for `query`, optionally filtered by a substring
    that must appear in the chunk's `source_path`."""
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []
    scored: list[Match] = []
    for chunk in iter_chunks(company_dir):
        if tag_filter and tag_filter not in chunk.source_path:
            continue
        s = _score(query_tokens, chunk)
        if s > 0:
            scored.append(Match(chunk=chunk, score=s))
    scored.sort(key=lambda m: (-m.score, m.chunk.chunk_index, m.chunk.source_path))
    return scored[:k]
