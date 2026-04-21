"""Citation contract primitive (Phase 5.2 — §7.2)."""
from __future__ import annotations

from core.primitives.citation import (
    CitationStatus,
    OriginalCitation,
    Reference,
    ReferencedClaim,
    parse_references,
    requires_references,
    validate_references_shape,
)


VALID_MESSAGE = """---
agent: marketing-manager
references_another_agent: true
references:
  - referenced_message: sessions/abc/marketing-turn-3.md
    referenced_claims:
      - claim: Maine has zero documented alternating-proprietor host wineries
        original_citation:
          type: priority_3_kb
          ref: knowledge-base/chunks/maine-ttb-roster.md#c3
          provenance:
            updated_at: 2026-04-17T00:00:00+00:00
            updated_by: kb.ingest
            source_path: knowledge-base/source/maine-ttb-roster.md
            ingested_at: 2026-04-17T00:00:00+00:00
    how_used: as input to the 12-month timeline
---

Body text here.
"""


def test_parse_references_extracts_block() -> None:
    refs = parse_references(VALID_MESSAGE)
    assert len(refs) == 1
    assert refs[0].referenced_message == "sessions/abc/marketing-turn-3.md"
    assert refs[0].how_used == "as input to the 12-month timeline"
    assert len(refs[0].referenced_claims) == 1


def test_parse_references_typed_envelope() -> None:
    refs = parse_references(VALID_MESSAGE)
    claim = refs[0].referenced_claims[0]
    assert isinstance(claim, ReferencedClaim)
    assert isinstance(claim.original_citation, OriginalCitation)
    assert claim.original_citation.type == "priority_3_kb"
    assert claim.original_citation.ref.endswith("#c3")


def test_parse_references_empty_when_no_frontmatter() -> None:
    assert parse_references("just prose, no frontmatter") == []


def test_parse_references_empty_when_key_missing() -> None:
    msg = "---\nagent: x\n---\nbody"
    assert parse_references(msg) == []


def test_parse_references_tolerates_malformed_yaml() -> None:
    # Unterminated frontmatter — should return [] not raise.
    assert parse_references("---\nbroken: [unclosed\n") == []


def test_validate_shape_valid() -> None:
    refs = parse_references(VALID_MESSAGE)
    result = validate_references_shape(refs)
    assert result.status is CitationStatus.VALID
    assert result.issues == ()


def test_validate_shape_rejects_empty_referenced_message() -> None:
    refs = [
        Reference(
            referenced_message="",
            referenced_claims=(
                ReferencedClaim(
                    claim="x",
                    original_citation=OriginalCitation(
                        type="priority_3_kb", ref="a/b", provenance={}
                    ),
                ),
            ),
        )
    ]
    result = validate_references_shape(refs)
    assert result.status is CitationStatus.INVALID
    assert any("referenced_message" in i for i in result.issues)


def test_validate_shape_rejects_unknown_citation_type() -> None:
    refs = [
        Reference(
            referenced_message="sessions/x/y.md",
            referenced_claims=(
                ReferencedClaim(
                    claim="x",
                    original_citation=OriginalCitation(
                        type="priority_99_nonsense", ref="a/b", provenance={}
                    ),
                ),
            ),
        )
    ]
    result = validate_references_shape(refs)
    assert result.status is CitationStatus.INVALID
    assert any("not a recognized store" in i for i in result.issues)


def test_validate_shape_rejects_missing_claims() -> None:
    refs = [
        Reference(
            referenced_message="sessions/x/y.md",
            referenced_claims=(),
        )
    ]
    result = validate_references_shape(refs)
    assert result.status is CitationStatus.INVALID
    assert any("no referenced_claims" in i for i in result.issues)


def test_validate_shape_rejects_empty_claim_text() -> None:
    refs = [
        Reference(
            referenced_message="sessions/x/y.md",
            referenced_claims=(
                ReferencedClaim(
                    claim="   ",  # whitespace only
                    original_citation=OriginalCitation(
                        type="priority_3_kb", ref="a/b", provenance={}
                    ),
                ),
            ),
        )
    ]
    result = validate_references_shape(refs)
    assert result.status is CitationStatus.INVALID
    assert any("empty claim text" in i for i in result.issues)


def test_requires_references_true_when_flag_set() -> None:
    assert requires_references(VALID_MESSAGE) is True


def test_requires_references_false_by_default() -> None:
    msg = "---\nagent: x\n---\nbody"
    assert requires_references(msg) is False


def test_requires_references_false_when_no_frontmatter() -> None:
    assert requires_references("plain body") is False


def test_validate_shape_accepts_all_known_priority_stores() -> None:
    """All 8 priority stores + assumption must pass shape validation."""
    known = [
        "priority_1_founder",
        "priority_2_decision",
        "priority_3_kb",
        "priority_4_brand",
        "priority_5_handshake",
        "priority_6_memory",
        "priority_7_taste",
        "priority_8_assumption",
    ]
    for t in known:
        refs = [
            Reference(
                referenced_message="sessions/x/y.md",
                referenced_claims=(
                    ReferencedClaim(
                        claim="ok",
                        original_citation=OriginalCitation(type=t, ref="r", provenance={}),
                    ),
                ),
            )
        ]
        assert validate_references_shape(refs).status is CitationStatus.VALID, t
