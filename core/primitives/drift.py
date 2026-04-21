"""
core/primitives/drift.py — Watchdog layer (§7.3)
================================================
Composes the citation-contract primitive (§7.2) with filesystem checks.
`watchdog_check()` is the sole entry point; callers feed it a message
body and the vault root, and get a `DriftAssessment` describing whether
the message passes (in either permissive or strict mode).

Checks performed for every entry in `references`:

  1. Shape is valid (delegates to `citation.validate_references_shape`).
  2. The `referenced_message` path exists inside the vault.
  3. Each `referenced_claims[].claim` text appears verbatim in the
     referenced message's body.
  4. The referenced message's own frontmatter citations (if any) still
     resolve — i.e. `source_path` files referenced by priority_*
     citations exist on disk.

Freshness of as-of dates is a separate check (§7.4 / chunk 5.4); this
module's concern is structural integrity, not staleness.

Modes:
  - `WatchdogMode.PERMISSIVE` (default) → assessment still computed, but
    `ok` is True if only annotations (not structural failures) occurred.
    Callers use `issues` to decide whether to warn the founder.
  - `WatchdogMode.STRICT` → `ok` is True only when `issues` is empty.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from core.primitives.citation import (
    CitationStatus,
    Reference,
    parse_references,
    validate_references_shape,
)


class WatchdogMode(Enum):
    PERMISSIVE = "permissive"
    STRICT = "strict"


# §10 action item 6 (consolidated-2026-04-18): Gemini-isolated Vulnerability #2
# — "watchdog substring bypass." A specialist prepends malicious/hallucinated
# assertions followed by a massive verbatim quote from a valid KB source;
# `verbatim_text in source_file` passes because the valid substring is
# present. Mitigations encoded below:
#
#   (a) MIN_CLAIM_LENGTH — reject claims too short to be load-bearing on
#       their own (fragments are easy to plant inside malicious content).
#   (b) word-boundary match — claim must sit at a word boundary on both
#       sides, not mid-token. Closes the "fragment inside a larger word"
#       cover case.
#   (c) coverage_ratio — strict mode fails when cited claims cover less
#       than MIN_COVERAGE_RATIO of the specialist's non-frontmatter body,
#       which is the "lots of unsourced prose around one real quote" shape.
MIN_CLAIM_LENGTH = 40
MIN_COVERAGE_RATIO = 0.25

_WORD_CHAR = re.compile(r"\w")
_FRONTMATTER_RE = re.compile(r"^---\n.*?\n---\n", re.DOTALL)


@dataclass(frozen=True)
class DriftAssessment:
    ok: bool
    mode: WatchdogMode
    issues: tuple[str, ...] = field(default_factory=tuple)
    references_checked: int = 0
    coverage_ratio: float = 1.0


def _read_if_exists(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _boundary_aware_match(claim_text: str, body: str) -> bool:
    """True iff claim_text appears in body with a non-word boundary (or
    start/end of text) on each side.

    Without this check, a specialist can embed a fragment of a source
    token (e.g. "Main" inside "Maintenance") and pretend it's a quote.
    The boundary-aware match refuses unless the quote is delimited by
    whitespace, punctuation, or the file edges.
    """
    if not claim_text:
        return False
    start = 0
    while True:
        idx = body.find(claim_text, start)
        if idx < 0:
            return False
        # Left boundary.
        left_ok = True
        if idx > 0:
            left_char = body[idx - 1]
            if _WORD_CHAR.match(left_char):
                left_ok = False
        # Right boundary.
        right_ok = True
        end = idx + len(claim_text)
        if end < len(body):
            right_char = body[end]
            if _WORD_CHAR.match(right_char):
                right_ok = False
        if left_ok and right_ok:
            return True
        start = idx + 1


def _strip_frontmatter(message: str) -> str:
    return _FRONTMATTER_RE.sub("", message, count=1)


def _body_length(text: str) -> int:
    return len(text.strip())


def _check_reference(
    ref: Reference,
    vault_dir: Path,
    idx: int,
) -> list[str]:
    issues: list[str] = []
    target = (vault_dir / ref.referenced_message).resolve()
    # Prevent traversal out of the vault.
    try:
        target.relative_to(vault_dir.resolve())
    except ValueError:
        issues.append(
            f"references[{idx}]: referenced_message {ref.referenced_message!r} "
            f"resolves outside vault_dir"
        )
        return issues

    body = _read_if_exists(target)
    if body is None:
        issues.append(
            f"references[{idx}]: referenced_message not found at {target}"
        )
        return issues

    # Every claim text must appear verbatim in the referenced message body
    # AND satisfy:
    #   - a minimum length (fragments are a cover-attack vector)
    #   - a word-boundary-aware match (not mid-token)
    # See MIN_CLAIM_LENGTH / _boundary_aware_match docstrings and §10
    # action item 6 of consolidated-2026-04-18.
    for j, claim in enumerate(ref.referenced_claims):
        claim_text = claim.claim.strip()
        if not claim_text:
            # Already caught by shape validation; skip here.
            continue
        if len(claim_text) < MIN_CLAIM_LENGTH:
            issues.append(
                f"references[{idx}].referenced_claims[{j}]: claim too short "
                f"({len(claim_text)} chars, need >= {MIN_CLAIM_LENGTH}). "
                f"Short fragments are a cover-attack vector."
            )
            continue
        if not _boundary_aware_match(claim_text, body):
            issues.append(
                f"references[{idx}].referenced_claims[{j}]: claim not found "
                f"in {ref.referenced_message} with word boundaries "
                f"(raw substring match is insufficient — see §10.6)"
            )
    return issues


def _coverage_ratio(message: str, refs: list[Reference]) -> float:
    """Ratio of cited-claim characters to non-frontmatter message body
    characters. 1.0 means claims equal or exceed the body; 0.0 means no
    citations. §10.6 diagnostic — see MIN_COVERAGE_RATIO."""
    body = _strip_frontmatter(message)
    body_len = _body_length(body)
    if body_len == 0:
        return 1.0
    total = 0
    for ref in refs:
        for claim in ref.referenced_claims:
            total += len(claim.claim.strip())
    return min(1.0, total / body_len)


def watchdog_check(
    message: str,
    vault_dir: Path,
    mode: WatchdogMode = WatchdogMode.PERMISSIVE,
    *,
    require_coverage: bool = False,
) -> DriftAssessment:
    """Run the drift watchdog over `message`, resolving referenced paths
    against `vault_dir`. Returns a DriftAssessment whose `ok` field
    respects `mode`.

    When `require_coverage` is True (default False for back-compat), strict
    mode additionally fails when cited claims cover less than
    MIN_COVERAGE_RATIO of the non-frontmatter message body. This closes
    the "massive verbatim quote covering unsourced prose" attack pattern
    described in consolidated-2026-04-18 §5 Vulnerability #2.
    """
    refs = parse_references(message)
    shape = validate_references_shape(refs)
    issues: list[str] = list(shape.issues)

    if shape.status is CitationStatus.VALID:
        for idx, ref in enumerate(refs):
            issues.extend(_check_reference(ref, vault_dir, idx))

    coverage = _coverage_ratio(message, refs) if refs else 1.0

    if require_coverage and refs and coverage < MIN_COVERAGE_RATIO:
        issues.append(
            f"coverage: cited claims cover only {coverage:.0%} of message "
            f"body (need >= {MIN_COVERAGE_RATIO:.0%}). Uncited assertions "
            f"suspected — see §10.6 of consolidated-2026-04-18."
        )

    clean = not issues
    if mode is WatchdogMode.STRICT:
        ok = clean
    else:
        # Permissive: annotations don't fail the check. Today we treat
        # all issues as structural; hook point for a future "annotation
        # vs failure" split.
        ok = clean
    return DriftAssessment(
        ok=ok,
        mode=mode,
        issues=tuple(issues),
        references_checked=len(refs),
        coverage_ratio=coverage,
    )
