"""
core/primitives/evaluators/score_parser.py -- robust score parser (R3)
======================================================================
`extract_score` -- robust score parser for LLM rubric responses.

Accepts many real-world response shapes and returns a validated Decimal
in [0, 1], or None when the input cannot be parsed into a valid score.

Two-tier extraction strategy
----------------------------
Tier 1 (primary, structured): match `<score>N</score>` tags.
  - Case-insensitive.
  - Tolerates whitespace inside tags.
  - Tolerates optional `%` suffix inside tags.
  - If a tag is present, its value is AUTHORITATIVE. If the tagged
    value is out of range, return None (fail-closed even on structured
    output; the LLM emitted a structured-but-invalid answer and we
    trust the tag rather than fishing in the prose for a better
    number).

Tier 2 (fallback, heuristic): regex scan of bare numbers.
  - Used only when no <score> tag is present.
  - Lookbehind `(?<![a-zA-Z0-9.])` excludes version numbers and
    mid-identifier digits (so `v1.0`, `ID-123`, `2026-04-21` do not
    contaminate the scan).
  - Allows whitespace before `%`: `90 %` is treated like `90%`.
  - Allows the word `percent` (case-insensitive, word-bounded) as
    an alternative to `%`: `90 percent` -> Decimal("0.90").
  - LAST-valid-wins: collect all in-range matches and return the
    last one in input order. LLMs put final answers at the end,
    and earlier numbers are typically thresholds, references, or
    sub-scores.
  - Out-of-range values in the fallback path are skipped (not
    fail-closed) so that a leading threshold like `"1 is best; my
    score is 0.9"` returns 0.9 rather than None.

Why structured-tag primary?
---------------------------
Both Gemini and Grok independently flagged that heuristic regex scans
are brittle under realistic LLM output (version-number collisions,
first-valid-wins mis-extracting conclusions, percent-whitespace misses).
Tag-based extraction is the robust long-term answer; the fallback
keeps the evaluator usable with LLMs that have not yet been prompted
to emit tags. Instructing the LLM to emit <score> tags is a v1c
follow-up (changing the default rubric template now would bump the
evaluator's canonical_hash).

Design decisions
----------------
- Pure regex plus Decimal arithmetic. No `json` import.
- Percent division happens before the range check in both tiers.
- `Decimal(str(numeric_str))` is used throughout to avoid float
  precision artifacts (e.g. Decimal(0.1) is not Decimal("0.1")).
- Fail-closed on Tier 1 out-of-range (trust the tag). Skip-and-
  continue on Tier 2 out-of-range (treat as noise).
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

__all__ = ["extract_score"]


# Tier 1: structured <score>N</score> tag.
# Case-insensitive, tolerates inner whitespace, optional % suffix.
_TAG_RE = re.compile(
    r"<score>\s*(-?\d+(?:\.\d+)?)\s*(%)?\s*</score>",
    re.IGNORECASE,
)

# Tier 2: heuristic bare-number scan.
# Lookbehind `(?<![a-zA-Z0-9.])` excludes version numbers (v1.0), mid-
# identifier digits (ID-123), and date components (2026-04-21). The
# percent marker accepts either `%` (with optional leading whitespace)
# or the word `percent` (case-insensitive, word-bounded).
_FALLBACK_RE = re.compile(
    r"(?<![a-zA-Z0-9.])(-?\d+(?:\.\d+)?)\s*(%|percent\b)?",
    re.IGNORECASE,
)

_ZERO = Decimal("0")
_ONE = Decimal("1")
_HUNDRED = Decimal("100")


def _normalize(numeric_str: str, percent_marker: str | None) -> Decimal | None:
    """Parse a captured numeric string, apply percent division, return Decimal.

    Returns None if the string is not a valid Decimal literal. Does NOT
    perform range checking; the caller decides the range policy.
    """
    try:
        value = Decimal(str(numeric_str))
    except InvalidOperation:
        return None

    if percent_marker:
        value = value / _HUNDRED

    return value


def _extract_tagged(llm_text: str) -> Decimal | None:
    """Tier 1: extract an authoritative score from a <score>N</score> tag.

    Returns:
      - Decimal in [0, 1] when the tag is present and in range.
      - None when the tag is present but out of range or garbage
        (fail-closed; trust the structured output).
      - The sentinel _NO_TAG when no tag is present so the caller
        knows to fall through to Tier 2.
    """
    match = _TAG_RE.search(llm_text)
    if match is None:
        return _NO_TAG

    value = _normalize(match.group(1), match.group(2))
    if value is None:
        # Tag present but inner value is not a valid numeric literal.
        # Fail closed -- the LLM emitted a structured answer we cannot
        # trust. Do not fall back to the prose scan.
        return None

    if _ZERO <= value <= _ONE:
        return value
    # Tag present but out of range. Fail closed.
    return None


def _extract_fallback(llm_text: str) -> Decimal | None:
    """Tier 2: last-valid-wins regex scan of bare numbers in the prose.

    Collects every in-range numeric token and returns the last one in
    input order. Out-of-range tokens are skipped (treated as noise,
    thresholds, or references rather than as evaluator output).
    """
    last_valid: Decimal | None = None
    for match in _FALLBACK_RE.finditer(llm_text):
        value = _normalize(match.group(1), match.group(2))
        if value is None:
            continue
        if _ZERO <= value <= _ONE:
            last_valid = value
        # Out-of-range values are silently skipped so that reference
        # constants like "1 is best" do not suppress a later valid score.
    return last_valid


# Sentinel used by _extract_tagged to distinguish "no tag present"
# (fall through to Tier 2) from "tag present but invalid" (fail closed).
_NO_TAG: object = object()


def extract_score(llm_text: str) -> Decimal | None:
    """Parse an LLM response and return the score in [0, 1], or None.

    Two-tier strategy (see module docstring for rationale):

    1. If `<score>N</score>` is present, use it. Out-of-range or
       non-numeric tag contents return None (fail-closed).
    2. Otherwise, scan bare numbers with a lookbehind that ignores
       version numbers and identifier digits, and return the LAST
       in-range value. Out-of-range values in this tier are skipped.

    Returns None on:
      - Empty / whitespace-only input
      - Tag present but out of range or non-numeric (Tier 1 fail-closed)
      - No in-range numeric token in Tier 2 scan
      - Any parsing error
    """
    if not llm_text or not llm_text.strip():
        return None

    tagged = _extract_tagged(llm_text)
    if tagged is not _NO_TAG:
        # Tag path was taken. Its result is authoritative, even if None.
        return tagged  # type: ignore[return-value]

    return _extract_fallback(llm_text)
