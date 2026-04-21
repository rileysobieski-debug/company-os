"""KB chunk → Claim adapter (Phase 4.4).

Covers:
  - chunk_to_claim produces a valid Priority 3 Claim
  - The produced Claim passes check_provenance
  - resolve_conflict: KB claim loses to a Priority 1 Founder claim
  - resolve_conflict: KB claim wins over a Priority 6 Memory claim
  - matches_to_claims preserves retrieval order
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.kb import (
    chunk_to_claim,
    ingest_source_doc,
    kb_query,
    matches_to_claims,
)
from core.kb.ingest import SOURCE_SUBDIR
from core.primitives.state import (
    AuthorityPriority,
    Claim,
    ProvenanceStatus,
    check_provenance,
    resolve_conflict,
)


@pytest.fixture
def company_with_chunks(tmp_path: Path) -> Path:
    src = tmp_path / SOURCE_SUBDIR / "note.md"
    src.parent.mkdir(parents=True)
    src.write_text("Maine TTB permits take 90 days typical.", encoding="utf-8")
    ingest_source_doc(src, tmp_path)
    return tmp_path


def _founder_claim(ref: str, updated_at: str, content: str = "founder content") -> Claim:
    return Claim(
        priority=AuthorityPriority.FOUNDER,
        content=content,
        ref=ref,
        provenance={
            "updated_at": updated_at,
            "updated_by": "riley",
            "source_path": "context.md",
            "ingested_at": updated_at,
        },
    )


def _memory_claim(ref: str, updated_at: str, content: str = "memory content") -> Claim:
    return Claim(
        priority=AuthorityPriority.MEMORY,
        content=content,
        ref=ref,
        provenance={
            "updated_at": updated_at,
            "updated_by": "marketing-manager",
            "source_path": "departments/marketing/manager-memory.md",
            "ingested_at": updated_at,
        },
    )


def test_chunk_to_claim_produces_priority_3(company_with_chunks: Path) -> None:
    matches = kb_query(company_with_chunks, "Maine TTB permits")
    assert matches
    claim = chunk_to_claim(matches[0].chunk)
    assert claim.priority is AuthorityPriority.KB
    assert claim.priority.value == 3


def test_chunk_to_claim_carries_provenance(company_with_chunks: Path) -> None:
    matches = kb_query(company_with_chunks, "Maine")
    claim = chunk_to_claim(matches[0].chunk)
    assert check_provenance(claim.provenance) is ProvenanceStatus.VALID
    assert claim.provenance["updated_by"] == "kb.ingest"
    assert claim.provenance["source_path"].startswith("knowledge-base/source/")


def test_chunk_to_claim_ref_encodes_kb_priority(company_with_chunks: Path) -> None:
    matches = kb_query(company_with_chunks, "Maine")
    claim = chunk_to_claim(matches[0].chunk)
    assert claim.ref.startswith("priority_3_kb:")
    assert "#c0" in claim.ref or "#c" in claim.ref


def test_kb_claim_loses_to_founder(company_with_chunks: Path) -> None:
    matches = kb_query(company_with_chunks, "Maine")
    kb = chunk_to_claim(matches[0].chunk)
    founder = _founder_claim("context.md#truth", "2026-04-17T00:00:00+00:00")
    result = resolve_conflict(kb, founder)
    assert result.winner is founder, "Founder claim must outrank KB"
    assert "priority_1_founder" in result.reason


def test_kb_claim_wins_over_memory(company_with_chunks: Path) -> None:
    matches = kb_query(company_with_chunks, "Maine")
    kb = chunk_to_claim(matches[0].chunk)
    memory = _memory_claim("departments/marketing/manager-memory.md#x", "2026-04-17T00:00:00+00:00")
    result = resolve_conflict(kb, memory)
    assert result.winner is kb, "KB must outrank Memory (priority 3 < 6)"
    assert "priority_3_kb" in result.reason


def test_matches_to_claims_preserves_order(company_with_chunks: Path) -> None:
    # Add one more source to give us two matches
    extra = company_with_chunks / SOURCE_SUBDIR / "extra.md"
    extra.write_text("Maine vineyards small-production focus.", encoding="utf-8")
    ingest_source_doc(extra, company_with_chunks)
    matches = kb_query(company_with_chunks, "Maine")
    claims = matches_to_claims(matches)
    assert len(claims) == len(matches)
    for m, c in zip(matches, claims):
        assert c.content == m.chunk.body


def test_chunk_to_claim_rejects_missing_ingested_at() -> None:
    from core.kb.store import Chunk
    bad = Chunk(
        path=Path("/nonexistent/x.md"),
        source_path="knowledge-base/source/x.md",
        ingested_at="",  # missing!
        source_asof="2025-01-01",
        stale_after="180d",
        content_hash="abc123",
        chunk_index=0,
        body="text",
    )
    with pytest.raises(ValueError, match="ingested_at"):
        chunk_to_claim(bad)
