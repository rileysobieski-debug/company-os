"""
core/primitives/voice.py — `voice.diff_from_brand` pure skill (§4)
==================================================================
Deterministic, zero-LLM diff between a draft and the current Brand DB.
Inputs: the draft prose + an iterable of `BrandEntry` records (voice
verdicts only — image entries are ignored). Output: a `VoiceDiff` with

  * `gold_alignment`   — recall of distinctive gold-bucket tokens &
                         bigrams that the draft reproduces. Range [0, 1].
  * `anti_exemplar_hits` — distinctive anti-exemplar tokens/bigrams that
                         the draft contains verbatim.
  * `missing_gold_markers` — top gold tokens/bigrams absent from the
                         draft (prompts for editing).
  * `entries_considered` — count of voice entries that contributed to
                         either the gold or anti-exemplar corpus.
  * `reason` — one-sentence human summary.

Why no LLM call: the plan (§4) cites `voice.diff_from_brand` as a
`mode: pure` skill. The specialist decides whether further reasoning is
warranted — this primitive's job is to produce a reproducible signal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from core.brand_db.store import BrandEntry

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")
_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "of", "in", "on", "at", "to",
    "for", "with", "from", "by", "is", "are", "was", "were", "be", "been",
    "being", "it", "its", "this", "that", "these", "those", "as", "if",
    "then", "so", "not", "no", "do", "does", "did", "have", "has", "had",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "we", "i", "you", "they", "he", "she", "our", "my", "your", "their",
    "will", "would", "should", "can", "could", "may", "might",
})

_GOLD_VERDICT = "gold"
_ANTI_VERDICT = "anti-exemplar"
_MAX_MISSING_MARKERS = 5
_MAX_HITS = 10


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def _bigrams(tokens: list[str]) -> list[str]:
    return [f"{tokens[i]} {tokens[i + 1]}" for i in range(len(tokens) - 1)]


def _unique(seq: Iterable[str]) -> list[str]:
    """Preserve first-seen order while deduping — keeps outputs stable."""
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


@dataclass(frozen=True)
class VoiceDiff:
    gold_alignment: float
    anti_exemplar_hits: tuple[str, ...] = field(default_factory=tuple)
    missing_gold_markers: tuple[str, ...] = field(default_factory=tuple)
    entries_considered: int = 0
    reason: str = ""


def _gather_markers(entries: list[BrandEntry], verdict: str) -> list[str]:
    """Collect distinctive tokens + bigrams across all entries with the
    given verdict. Tags count as first-class markers."""
    markers: list[str] = []
    for entry in entries:
        if entry.verdict != verdict:
            continue
        corpus = entry.content
        if entry.description:
            corpus = f"{entry.description}\n{corpus}"
        tokens = _tokenize(corpus)
        markers.extend(tokens)
        markers.extend(_bigrams(tokens))
        for tag in entry.tags:
            tag_tokens = _tokenize(tag)
            if tag_tokens:
                markers.append(tag_tokens[0])
    return _unique(markers)


def diff_from_brand(
    draft: str, entries: Iterable[BrandEntry]
) -> VoiceDiff:
    """Pure-skill diff between `draft` and the voice entries in `entries`.
    Image entries and entries with non-voice `kind` are ignored.

    Determinism guarantees:
      - Same inputs → same VoiceDiff (byte-exact).
      - No randomness; no LLM call; no global state.
    """
    voice_entries = [e for e in entries if e.kind == "voice"]
    if not voice_entries:
        return VoiceDiff(
            gold_alignment=0.0,
            entries_considered=0,
            reason="no voice entries in brand-db; nothing to diff against",
        )

    gold_markers = _gather_markers(voice_entries, _GOLD_VERDICT)
    anti_markers = _gather_markers(voice_entries, _ANTI_VERDICT)

    draft_tokens = _tokenize(draft)
    draft_set: set[str] = set(draft_tokens) | set(_bigrams(draft_tokens))

    if gold_markers:
        present = [m for m in gold_markers if m in draft_set]
        gold_alignment = len(present) / len(gold_markers)
    else:
        gold_alignment = 0.0

    missing = [m for m in gold_markers if m not in draft_set][:_MAX_MISSING_MARKERS]
    hits = [m for m in anti_markers if m in draft_set][:_MAX_HITS]

    if gold_markers and anti_markers:
        reason = (
            f"aligned on {int(round(gold_alignment * 100))}% of "
            f"{len(gold_markers)} gold markers; {len(hits)} anti-exemplar hits"
        )
    elif gold_markers:
        reason = (
            f"aligned on {int(round(gold_alignment * 100))}% of "
            f"{len(gold_markers)} gold markers; no anti-exemplar corpus"
        )
    elif anti_markers:
        reason = (
            f"no gold corpus; {len(hits)} anti-exemplar hits "
            f"out of {len(anti_markers)} markers"
        )
    else:
        reason = "no gold or anti-exemplar voice entries present"

    return VoiceDiff(
        gold_alignment=gold_alignment,
        anti_exemplar_hits=tuple(hits),
        missing_gold_markers=tuple(missing),
        entries_considered=len(voice_entries),
        reason=reason,
    )
