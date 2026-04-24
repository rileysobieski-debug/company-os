"""KB store silent-data-loss fix tests (Gemini review finding).

Covers the new `strict=True` mode on `load_chunk` / `iter_chunks` +
the `find_malformed_chunks` inventory helper.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.kb.store import (
    CHUNKS_SUBDIR,
    MalformedChunkError,
    find_malformed_chunks,
    iter_chunks,
    load_chunk,
)


def _write_chunk(path: Path, *, source_path: str = "src.md", content_hash: str = "abc", body: str = "body") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nsource_path: {source_path}\ncontent_hash: {content_hash}\nchunk_index: 0\n---\n{body}",
        encoding="utf-8",
    )


def _write_malformed(path: Path, *, missing: str = "content_hash") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if missing == "both":
        fm = ""
    elif missing == "source_path":
        fm = "content_hash: xyz\n"
    else:
        fm = "source_path: x.md\n"
    path.write_text(f"---\n{fm}---\nbody", encoding="utf-8")


# ---------------------------------------------------------------------------
# load_chunk default: permissive (None on malformed)
# ---------------------------------------------------------------------------
def test_load_chunk_permissive_returns_none_on_missing_hash(tmp_path: Path) -> None:
    p = tmp_path / "bad.md"
    _write_malformed(p, missing="content_hash")
    assert load_chunk(p) is None


def test_load_chunk_permissive_returns_none_on_missing_source_path(tmp_path: Path) -> None:
    p = tmp_path / "bad.md"
    _write_malformed(p, missing="source_path")
    assert load_chunk(p) is None


def test_load_chunk_permissive_returns_chunk_on_valid(tmp_path: Path) -> None:
    p = tmp_path / "ok.md"
    _write_chunk(p)
    chunk = load_chunk(p)
    assert chunk is not None
    assert chunk.source_path == "src.md"


# ---------------------------------------------------------------------------
# load_chunk strict: raises on missing fields
# ---------------------------------------------------------------------------
def test_load_chunk_strict_raises_on_missing_hash(tmp_path: Path) -> None:
    p = tmp_path / "bad.md"
    _write_malformed(p, missing="content_hash")
    with pytest.raises(MalformedChunkError) as exc:
        load_chunk(p, strict=True)
    assert "content_hash" in exc.value.missing


def test_load_chunk_strict_raises_on_missing_source_path(tmp_path: Path) -> None:
    p = tmp_path / "bad.md"
    _write_malformed(p, missing="source_path")
    with pytest.raises(MalformedChunkError) as exc:
        load_chunk(p, strict=True)
    assert "source_path" in exc.value.missing


def test_load_chunk_strict_exception_carries_both_missing(tmp_path: Path) -> None:
    p = tmp_path / "bad.md"
    _write_malformed(p, missing="both")
    with pytest.raises(MalformedChunkError) as exc:
        load_chunk(p, strict=True)
    assert "content_hash" in exc.value.missing
    assert "source_path" in exc.value.missing


def test_load_chunk_strict_accepts_valid(tmp_path: Path) -> None:
    p = tmp_path / "ok.md"
    _write_chunk(p)
    chunk = load_chunk(p, strict=True)
    assert chunk is not None


# ---------------------------------------------------------------------------
# iter_chunks strict: aborts on first malformed
# ---------------------------------------------------------------------------
def test_iter_chunks_permissive_skips_malformed(tmp_path: Path) -> None:
    chunks_dir = tmp_path / CHUNKS_SUBDIR
    _write_chunk(chunks_dir / "a.md")
    _write_malformed(chunks_dir / "b.md", missing="content_hash")
    _write_chunk(chunks_dir / "c.md")
    result = list(iter_chunks(tmp_path))
    assert len(result) == 2


def test_iter_chunks_strict_raises_on_first_malformed(tmp_path: Path) -> None:
    chunks_dir = tmp_path / CHUNKS_SUBDIR
    _write_chunk(chunks_dir / "a.md")
    _write_malformed(chunks_dir / "b.md", missing="content_hash")
    _write_chunk(chunks_dir / "c.md")
    with pytest.raises(MalformedChunkError):
        list(iter_chunks(tmp_path, strict=True))


def test_iter_chunks_missing_dir_is_noop(tmp_path: Path) -> None:
    assert list(iter_chunks(tmp_path)) == []
    assert list(iter_chunks(tmp_path, strict=True)) == []


# ---------------------------------------------------------------------------
# find_malformed_chunks
# ---------------------------------------------------------------------------
def test_find_malformed_returns_empty_on_clean_dir(tmp_path: Path) -> None:
    chunks_dir = tmp_path / CHUNKS_SUBDIR
    _write_chunk(chunks_dir / "a.md")
    _write_chunk(chunks_dir / "b.md")
    assert find_malformed_chunks(tmp_path) == []


def test_find_malformed_lists_all_problems(tmp_path: Path) -> None:
    chunks_dir = tmp_path / CHUNKS_SUBDIR
    _write_chunk(chunks_dir / "good.md")
    _write_malformed(chunks_dir / "no_hash.md", missing="content_hash")
    _write_malformed(chunks_dir / "no_source.md", missing="source_path")
    _write_malformed(chunks_dir / "neither.md", missing="both")
    problems = find_malformed_chunks(tmp_path)
    paths = {p.name: missing for p, missing in problems}
    assert "good.md" not in paths
    assert "content_hash" in paths["no_hash.md"]
    assert "source_path" in paths["no_source.md"]
    assert set(paths["neither.md"]) == {"content_hash", "source_path"}


def test_find_malformed_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert find_malformed_chunks(tmp_path) == []


def test_malformed_error_has_path_and_missing() -> None:
    err = MalformedChunkError(Path("x.md"), ("content_hash",))
    assert err.path == Path("x.md")
    assert err.missing == ("content_hash",)
    assert "content_hash" in str(err)
