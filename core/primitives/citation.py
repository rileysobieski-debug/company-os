"""
core/primitives/citation.py — Reference-based citation contract (§7.2)
======================================================================
Content-layer drift defense. When an agent's message references another
agent's prior message, it must carry the claim forward with its original
citation. Agent-internal claims (not referencing another agent) are
unrestricted — this is where §7.2's cost savings come from.

Schema (from §7.2):

    references:
      - referenced_message: "sessions/<id>/<agent-slug>-turn-N.md"
        referenced_claims:
          - claim: "<verbatim claim text>"
            original_citation:
              type: "priority_3_kb"
              ref:  "knowledge-base/chunks/maine-ttb-roster.md#c3"
              provenance: {...}
        how_used: "<short purpose>"

This module parses a message's YAML frontmatter references block into
typed envelopes and checks structural validity. It does NOT verify that
the referenced message exists on disk or that the claim is actually
present — that's the watchdog's job (§7.3, chunk 5.3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

import yaml

# Citation-type tokens recognized by the contract. Matches the
# AuthorityPriority tiers from core/primitives/state.py plus the "assumption"
# store (priority 8).
_VALID_CITATION_TYPES = frozenset(
    {
        "priority_1_founder",
        "priority_2_decision",
        "priority_3_kb",
        "priority_4_brand",
        "priority_5_handshake",
        "priority_6_memory",
        "priority_7_taste",
        "priority_8_assumption",
    }
)


class CitationStatus(Enum):
    VALID = "valid"
    INVALID = "invalid"


@dataclass(frozen=True)
class OriginalCitation:
    type: str
    ref: str
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class ReferencedClaim:
    claim: str
    original_citation: OriginalCitation


@dataclass(frozen=True)
class Reference:
    referenced_message: str
    referenced_claims: tuple[ReferencedClaim, ...]
    how_used: str = ""


@dataclass(frozen=True)
class CitationValidation:
    status: CitationStatus
    issues: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------
_FRONTMATTER_DELIM = "---\n"


def _extract_frontmatter(message: str) -> dict[str, Any] | None:
    if not message.startswith(_FRONTMATTER_DELIM):
        return None
    end = message.find("\n---\n", len(_FRONTMATTER_DELIM))
    if end < 0:
        return None
    raw = message[len(_FRONTMATTER_DELIM):end]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    return data if isinstance(data, dict) else None


def parse_references(message: str) -> list[Reference]:
    """Extract the `references:` block from `message`'s YAML frontmatter.

    Returns an empty list if the message has no frontmatter, no references
    key, or a malformed references block — callers distinguish "no block"
    from "invalid block" by also calling `validate_references_shape()`.
    """
    fm = _extract_frontmatter(message)
    if not fm:
        return []
    raw_refs = fm.get("references")
    if not isinstance(raw_refs, list):
        return []
    refs: list[Reference] = []
    for entry in raw_refs:
        if not isinstance(entry, Mapping):
            continue
        referenced_message = str(entry.get("referenced_message", ""))
        claims_raw = entry.get("referenced_claims", [])
        claims: list[ReferencedClaim] = []
        if isinstance(claims_raw, list):
            for c in claims_raw:
                if not isinstance(c, Mapping):
                    continue
                citation_data = c.get("original_citation") or {}
                if not isinstance(citation_data, Mapping):
                    citation_data = {}
                claims.append(
                    ReferencedClaim(
                        claim=str(c.get("claim", "")),
                        original_citation=OriginalCitation(
                            type=str(citation_data.get("type", "")),
                            ref=str(citation_data.get("ref", "")),
                            provenance=citation_data.get("provenance") or {},
                        ),
                    )
                )
        refs.append(
            Reference(
                referenced_message=referenced_message,
                referenced_claims=tuple(claims),
                how_used=str(entry.get("how_used", "")),
            )
        )
    return refs


# ---------------------------------------------------------------------------
# Shape validation
# ---------------------------------------------------------------------------
def validate_references_shape(
    references: list[Reference],
) -> CitationValidation:
    """Structural-only check. Every reference must name a message, carry
    at least one claim, and each claim's original_citation must have a
    recognized type + non-empty ref. Does NOT verify on-disk presence."""
    issues: list[str] = []
    for i, ref in enumerate(references):
        if not ref.referenced_message:
            issues.append(f"references[{i}]: missing referenced_message")
        if not ref.referenced_claims:
            issues.append(f"references[{i}]: no referenced_claims")
            continue
        for j, claim in enumerate(ref.referenced_claims):
            path = f"references[{i}].referenced_claims[{j}]"
            if not claim.claim.strip():
                issues.append(f"{path}: empty claim text")
            citation = claim.original_citation
            if citation.type not in _VALID_CITATION_TYPES:
                issues.append(
                    f"{path}.original_citation.type: "
                    f"{citation.type!r} is not a recognized store "
                    f"(expected one of {sorted(_VALID_CITATION_TYPES)})"
                )
            if not citation.ref:
                issues.append(f"{path}.original_citation.ref: missing")
    status = CitationStatus.VALID if not issues else CitationStatus.INVALID
    return CitationValidation(status=status, issues=tuple(issues))


# ---------------------------------------------------------------------------
# Convenience
# ---------------------------------------------------------------------------
def requires_references(message: str) -> bool:
    """Return True if `message` declares itself as referencing another
    agent's output (frontmatter `references_another_agent: true`). This
    is the handshake-rejection signal — a message that claims it
    references another agent but ships no `references` block is rejected.

    The flag defaults to False (agent-internal output) so the handshake
    handler doesn't need to classify every message.
    """
    fm = _extract_frontmatter(message) or {}
    return bool(fm.get("references_another_agent", False))
