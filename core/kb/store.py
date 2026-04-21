"""
Knowledge-base store — chunk index
===================================
Filesystem-backed view of the `<company>/knowledge-base/chunks/` dir.
Parses each chunk's frontmatter once, caches per-company, and exposes
an iterator.

The index is intentionally not a vector DB yet. sqlite-vec lands in a
later chunk; the stable contract is the returned `Chunk` record so the
retrieval layer can swap its backend without changing callers or the
`employee.kb-retriever` skill spec.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.kb.ingest import CHUNKS_SUBDIR


@dataclass(frozen=True)
class Chunk:
    path: Path
    source_path: str
    ingested_at: str
    source_asof: str
    stale_after: str
    content_hash: str
    chunk_index: int
    body: str


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 5:]
    fm: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        fm[k.strip()] = v.strip()
    return fm, body


def load_chunk(path: Path) -> Chunk | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, body = _split_frontmatter(text)
    if "source_path" not in fm or "content_hash" not in fm:
        return None
    try:
        chunk_index = int(fm.get("chunk_index", "0"))
    except ValueError:
        chunk_index = 0
    return Chunk(
        path=path,
        source_path=fm["source_path"],
        ingested_at=fm.get("ingested_at", ""),
        source_asof=fm.get("source_asof", ""),
        stale_after=fm.get("stale_after", ""),
        content_hash=fm["content_hash"],
        chunk_index=chunk_index,
        body=body.strip(),
    )


def iter_chunks(company_dir: Path):
    """Yield every valid chunk in the company's KB chunks dir."""
    chunks_dir = company_dir / CHUNKS_SUBDIR
    if not chunks_dir.exists():
        return
    for p in sorted(chunks_dir.glob("*.md")):
        ch = load_chunk(p)
        if ch is not None:
            yield ch


def load_all(company_dir: Path) -> list[Chunk]:
    return list(iter_chunks(company_dir))
