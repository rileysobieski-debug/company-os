"""Knowledge-base retrieval tests (Phase 4.2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.kb import ingest_all, kb_query
from core.kb.ingest import SOURCE_SUBDIR


@pytest.fixture
def company_with_kb(tmp_path: Path) -> Path:
    src = tmp_path / SOURCE_SUBDIR
    src.mkdir(parents=True)
    (src / "maine-ttb.md").write_text(
        "Maine TTB licensing requires federal basic permit before state approval.\n"
        "The federal basic permit is issued by the Alcohol and Tobacco Tax Bureau.",
        encoding="utf-8",
    )
    (src / "branding.md").write_text(
        "Old Press brand voice is understated, coastal, and historically grounded.\n"
        "Arena Magazine aesthetic is the recurring reference.",
        encoding="utf-8",
    )
    (src / "compliance-notes.md").write_text(
        "State compliance in Maine requires county-level documentation.",
        encoding="utf-8",
    )
    ingest_all(tmp_path)
    return tmp_path


def test_kb_query_returns_relevant_chunk(company_with_kb: Path) -> None:
    matches = kb_query(company_with_kb, "federal permit for TTB licensing")
    assert len(matches) >= 1
    top = matches[0]
    assert "federal" in top.chunk.body.lower()
    assert "ttb" in top.chunk.source_path.lower() or "ttb" in top.chunk.body.lower()


def test_kb_query_ranks_by_relevance(company_with_kb: Path) -> None:
    matches = kb_query(company_with_kb, "brand voice Arena Magazine aesthetic")
    assert matches
    # Top hit should be branding, not maine-ttb.
    assert "branding" in matches[0].chunk.source_path


def test_kb_query_respects_k(company_with_kb: Path) -> None:
    matches = kb_query(company_with_kb, "Maine", k=1)
    assert len(matches) <= 1


def test_kb_query_tag_filter_narrows_scope(company_with_kb: Path) -> None:
    matches = kb_query(company_with_kb, "Maine", tag_filter="compliance")
    assert matches
    for m in matches:
        assert "compliance" in m.chunk.source_path


def test_kb_query_empty_query_returns_empty(company_with_kb: Path) -> None:
    assert kb_query(company_with_kb, "") == []
    # Stopword-only query is also empty after tokenization.
    assert kb_query(company_with_kb, "the and of") == []


def test_kb_query_missing_company_dir_returns_empty(tmp_path: Path) -> None:
    # A company dir with no KB at all must not raise.
    assert kb_query(tmp_path, "anything") == []


def test_kb_query_deterministic_ordering(company_with_kb: Path) -> None:
    """Ties must break the same way on every call — supports stable citations."""
    first = kb_query(company_with_kb, "Maine compliance")
    second = kb_query(company_with_kb, "Maine compliance")
    assert [m.chunk.path for m in first] == [m.chunk.path for m in second]


def test_chunk_record_preserves_provenance(company_with_kb: Path) -> None:
    from core.kb import load_all
    chunks = load_all(company_with_kb)
    assert chunks
    for c in chunks:
        assert c.source_path.startswith("knowledge-base/source/")
        assert c.content_hash and len(c.content_hash) == 16
        assert c.ingested_at  # ISO timestamp set at ingest
        assert c.stale_after == "180d"
