"""Knowledge-base ingest tests (Phase 4.1).

Covers:
  - chunk_text paragraph-aware splitting
  - ingest_source_doc writes one chunk file per chunk with provenance
  - provenance fields contain source_path, ingested_at, source_asof, stale_after
  - idempotency (re-running ingest doesn't rewrite unchanged chunks)
  - ingest_all walks the source dir
  - source frontmatter asof overrides file mtime
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.kb.ingest import (
    CHUNKS_SUBDIR,
    SOURCE_SUBDIR,
    chunk_text,
    ingest_all,
    ingest_source_doc,
)


@pytest.fixture
def company(tmp_path: Path) -> Path:
    """A skeleton company dir with a knowledge-base/source/ tree."""
    (tmp_path / SOURCE_SUBDIR).mkdir(parents=True)
    return tmp_path


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------
def test_chunk_text_empty() -> None:
    assert chunk_text("") == []


def test_chunk_text_single_short_doc_is_one_chunk() -> None:
    out = chunk_text("hello\n\nworld")
    assert len(out) == 1
    assert "hello" in out[0] and "world" in out[0]


def test_chunk_text_respects_paragraph_boundaries() -> None:
    # Make target small so we force splitting
    para_a = "a" * 400
    para_b = "b" * 400
    para_c = "c" * 400
    text = f"{para_a}\n\n{para_b}\n\n{para_c}"
    chunks = chunk_text(text, target_size=500)
    # With target 500 and three 400-char paras, we should not glue all three
    # into one chunk.
    assert len(chunks) >= 2
    # Every chunk must be a whole-paragraph boundary — no paragraph mid-split.
    for c in chunks:
        assert "a" not in c or c.count("a") in (0, 400)
        assert "b" not in c or c.count("b") in (0, 400)


# ---------------------------------------------------------------------------
# ingest_source_doc
# ---------------------------------------------------------------------------
def test_ingest_writes_chunk_file_with_provenance(company: Path) -> None:
    src = company / SOURCE_SUBDIR / "notes.md"
    src.write_text("# Notes\n\nFirst paragraph.\n\nSecond paragraph.", encoding="utf-8")
    written = ingest_source_doc(src, company)
    assert len(written) == 1
    body = written[0].read_text(encoding="utf-8")
    assert body.startswith("---\n")
    for required in (
        "source_path:",
        "ingested_at:",
        "source_asof:",
        "stale_after:",
        "content_hash:",
        "chunk_index: 0",
    ):
        assert required in body, f"missing {required} in chunk frontmatter"


def test_ingest_source_path_is_relative_to_company(company: Path) -> None:
    src = company / SOURCE_SUBDIR / "x.md"
    src.write_text("hello", encoding="utf-8")
    [chunk] = ingest_source_doc(src, company)
    body = chunk.read_text(encoding="utf-8")
    m = re.search(r"^source_path:\s*(.+)$", body, re.MULTILINE)
    assert m, body
    assert m.group(1).strip() == "knowledge-base/source/x.md"


def test_ingest_stale_after_defaults_to_180d(company: Path) -> None:
    src = company / SOURCE_SUBDIR / "x.md"
    src.write_text("body", encoding="utf-8")
    [chunk] = ingest_source_doc(src, company)
    body = chunk.read_text(encoding="utf-8")
    assert "stale_after: 180d" in body


def test_ingest_honors_explicit_source_asof(company: Path) -> None:
    src = company / SOURCE_SUBDIR / "dated.md"
    src.write_text("---\nasof: 2025-01-15\n---\n\nbody text", encoding="utf-8")
    [chunk] = ingest_source_doc(src, company)
    body = chunk.read_text(encoding="utf-8")
    assert "source_asof: 2025-01-15" in body


def test_ingest_is_idempotent(company: Path) -> None:
    src = company / SOURCE_SUBDIR / "x.md"
    src.write_text("one paragraph.\n\ntwo paragraph.", encoding="utf-8")
    first = ingest_source_doc(src, company)
    first_mtimes = {p: p.stat().st_mtime_ns for p in first}
    # Re-ingest: same filenames must come back, and none must be rewritten.
    second = ingest_source_doc(src, company)
    assert [p.name for p in first] == [p.name for p in second]
    for p in second:
        assert p.stat().st_mtime_ns == first_mtimes[p], (
            f"{p.name} was rewritten — ingest should be idempotent"
        )


def test_ingest_multiple_chunks_have_unique_filenames(company: Path) -> None:
    src = company / SOURCE_SUBDIR / "big.md"
    # Force at least two chunks with small target size
    src.write_text(("A" * 400) + "\n\n" + ("B" * 400) + "\n\n" + ("C" * 400), encoding="utf-8")
    written = ingest_source_doc(src, company, target_size=500)
    assert len(written) >= 2
    assert len({p.name for p in written}) == len(written)


def test_ingest_empty_doc_writes_nothing(company: Path) -> None:
    src = company / SOURCE_SUBDIR / "empty.md"
    src.write_text("", encoding="utf-8")
    assert ingest_source_doc(src, company) == []


# ---------------------------------------------------------------------------
# ingest_all
# ---------------------------------------------------------------------------
def test_ingest_all_walks_all_files(company: Path) -> None:
    (company / SOURCE_SUBDIR / "a.md").write_text("apple", encoding="utf-8")
    (company / SOURCE_SUBDIR / "b.txt").write_text("banana", encoding="utf-8")
    # A non-supported extension must be skipped.
    (company / SOURCE_SUBDIR / "c.png").write_bytes(b"\x89PNG")
    result = ingest_all(company)
    assert result.sources_scanned == 2
    chunks = list((company / CHUNKS_SUBDIR).glob("*.md"))
    assert len(chunks) == 2


def test_ingest_all_handles_missing_source_dir(tmp_path: Path) -> None:
    # company_dir that has no source/ must not raise.
    result = ingest_all(tmp_path)
    assert result.sources_scanned == 0
    assert result.chunks_written == 0


def test_ingest_all_populates_chunks_by_source(company: Path) -> None:
    (company / SOURCE_SUBDIR / "one.md").write_text("alpha text", encoding="utf-8")
    (company / SOURCE_SUBDIR / "two.md").write_text("beta text", encoding="utf-8")
    result = ingest_all(company)
    assert result.chunks_by_source == {"one.md": 1, "two.md": 1}
