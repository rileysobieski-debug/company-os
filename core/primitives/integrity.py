"""
core/primitives/integrity.py — Hash-backed provenance binding (§9 / §10.1)
==========================================================================

Both Grok-4 and Gemini-2.5-Pro independently identified the same
top-severity attack surface in the Phase 13 governance stack
(consolidated-2026-04-18 §5, Vulnerability #1):

> "check_provenance verifies only the presence of string fields, not
> their integrity. The LLM controls the provenance metadata it writes."

Specifically:

- Grok's attack: specialist hallucinates a Claim with a parsable-but-
  nonexistent chunk path → fake Claim passes watchdog's superficial
  check → propagates through memory_updater → hallucination cascade.

- Gemini's attack: specialist prompt-injected to append a JSON block
  formatted as a Priority 2 Decision with fabricated `supersedes` list
  pointing to a valid Priority 1 Founder rule, paired with
  `updated_at='2099-12-31'`. `resolve_conflict` evaluates the
  malicious Priority 2 claim, sees the explicit override + future
  timestamp, permanently overrides the Founder's core conviction.

(Timestamp part closed by state.py FUTURE_TIMESTAMP_TOLERANCE_SECONDS;
the provenance-spoofing part closed here.)

**Mitigation — hash-backed provenance binding:**

At ingest time (engine-side, OUT of any agent context), compute a
deterministic SHA-256 over (content || normalized_provenance). Store the
hex digest in the file's frontmatter as `integrity_hash`.

When `resolve_conflict` evaluates a claim, it can optionally re-read the
source file, re-derive the hash, and compare. A mismatch (or missing
hash when one is required) rejects the claim before it wins any
tiebreaker.

The architectural principle synthesized from the two reviews:

> "Any field an LLM writes must also be re-derivable or verifiable by
> the engine from out-of-band state." (consolidated §9)

This module is the smallest primitive that enforces that principle for
provenance metadata. Prompt-versioning (Grok's proposal) would extend
the same principle to prompts; deferred per §10 action item 9.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

# Fields that participate in the integrity hash. If any of these change
# on disk without a matching rewrite of `integrity_hash`, verification
# fails. `integrity_hash` itself is excluded (it's the output).
#
# `content_hash` is the pre-existing body-only hash (16 hex) that kb/ingest
# already writes. It's included in the integrity hash as a second line of
# defense: even if the body survives, tampering with the body changes
# content_hash, which changes integrity_hash. Belt and suspenders.
BOUND_PROVENANCE_FIELDS = (
    "source_path",
    "ingested_at",
    "source_asof",
    "stale_after",
    "content_hash",
    "chunk_index",
    "updated_at",
    "updated_by",
)

INTEGRITY_HASH_FIELD = "integrity_hash"
# Fields written by the LLM (not trusted). Must not participate in hash.
_UNTRUSTED_FIELDS = frozenset({INTEGRITY_HASH_FIELD, "founder_signature"})

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class IntegrityCheck:
    ok: bool
    reason: str
    expected: str = ""
    actual: str = ""


# ---------------------------------------------------------------------------
# Canonical serialization + hashing
# ---------------------------------------------------------------------------
def _canonical_provenance(provenance: Mapping[str, Any]) -> str:
    """Deterministic JSON serialization of the subset of provenance
    fields that participate in the hash.

    We include only BOUND_PROVENANCE_FIELDS (and only when present) so
    callers can add arbitrary other frontmatter keys without breaking
    verification. Missing bound fields are encoded as empty strings —
    NOT skipped — so that removing a field is itself a tamper event.
    """
    payload = {key: str(provenance.get(key, "")) for key in BOUND_PROVENANCE_FIELDS}
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _normalize_body(body: str) -> str:
    """Canonicalize a body string for hashing. Drops trailing whitespace
    + normalizes to `rstrip() + "\\n"` form so the exact bytes written
    to disk match the bytes re-read at verification time."""
    return body.rstrip() + "\n"


def compute_integrity_hash(body: str, provenance: Mapping[str, Any]) -> str:
    """SHA-256 hex digest over `normalize(body) || canonical(provenance)`.

    Deterministic: same inputs always produce the same digest. Any
    change to the body or any bound provenance field changes the
    output. Callers store the result as `integrity_hash` in frontmatter.

    Body normalization is intentional — the render path writes
    `body.rstrip() + "\\n"` to disk; we hash the same canonical form
    here so verification after disk round-trip succeeds.
    """
    h = hashlib.sha256()
    h.update(_normalize_body(body).encode("utf-8"))
    h.update(b"\x1f")  # ASCII unit separator — disambiguate body/provenance boundary
    h.update(_canonical_provenance(provenance).encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Frontmatter helpers (lightweight YAML — matches kb/ingest style)
# ---------------------------------------------------------------------------
def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter_dict, body). Shallow `key: value` parser —
    same shape as kb/ingest's parser, kept local to avoid a cross-module
    dependency. If no frontmatter fence, returns ({}, text)."""
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    raw = match.group(1)
    body = text[match.end():]
    fm: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm, body


def render_frontmatter_with_hash(
    *,
    body: str,
    provenance: Mapping[str, Any],
    extra_fields: Mapping[str, Any] | None = None,
) -> str:
    """Render a chunk file with `integrity_hash` baked in.

    Only BOUND_PROVENANCE_FIELDS + extra_fields are written; the caller
    controls ordering by passing an ordered mapping. The `integrity_hash`
    line is appended last.
    """
    lines = ["---"]
    for key in BOUND_PROVENANCE_FIELDS:
        if key in provenance and provenance[key] != "":
            lines.append(f"{key}: {provenance[key]}")
    if extra_fields:
        for key, value in extra_fields.items():
            if key in _UNTRUSTED_FIELDS:
                continue  # never let caller inject the hash
            lines.append(f"{key}: {value}")
    digest = compute_integrity_hash(body, provenance)
    lines.append(f"{INTEGRITY_HASH_FIELD}: {digest}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + body.rstrip() + "\n"


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
def verify_file_integrity(file_path: Path) -> IntegrityCheck:
    """Re-derive the integrity hash from a file's current on-disk state
    and compare it against the `integrity_hash` field in frontmatter.

    - No frontmatter → `ok=False, reason="no-frontmatter"`.
    - Missing integrity_hash field → `ok=False, reason="no-hash"`.
    - Hash mismatch → `ok=False, reason="mismatch"` with expected/actual.
    - Match → `ok=True`.

    This is the hot path — must be cheap. O(file_size) SHA-256.
    """
    if not file_path.exists() or not file_path.is_file():
        return IntegrityCheck(ok=False, reason="missing-file")
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover — defensive
        return IntegrityCheck(ok=False, reason=f"read-error: {exc}")
    fm, body = parse_frontmatter(text)
    if not fm:
        return IntegrityCheck(ok=False, reason="no-frontmatter")
    stored = fm.pop(INTEGRITY_HASH_FIELD, "")
    if not stored:
        return IntegrityCheck(ok=False, reason="no-hash")
    derived = compute_integrity_hash(body, fm)
    if derived != stored:
        return IntegrityCheck(
            ok=False,
            reason="mismatch",
            expected=stored,
            actual=derived,
        )
    return IntegrityCheck(ok=True, reason="match", expected=stored, actual=derived)


def verify_claim_integrity(
    claim_provenance: Mapping[str, Any],
    vault_dir: Path,
) -> IntegrityCheck:
    """Verify the integrity of the source file referenced by a Claim's
    provenance. `source_path` must be a vault-relative path.

    Safe on path traversal: resolves under vault_dir and refuses any
    resolution that escapes the root.
    """
    source = claim_provenance.get("source_path")
    if not source:
        return IntegrityCheck(ok=False, reason="no-source-path")
    candidate = (vault_dir / str(source)).resolve()
    try:
        candidate.relative_to(vault_dir.resolve())
    except ValueError:
        return IntegrityCheck(ok=False, reason="path-escapes-vault")
    return verify_file_integrity(candidate)
