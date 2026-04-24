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


class MalformedChunkError(ValueError):
    """Raised when a chunk's frontmatter is missing required fields.
    Callers treat this as a data-loss signal, not a skip: a corrupted
    chunk must surface to the Tension HUD rather than silently vanish
    from the KB (Gemini review finding)."""

    def __init__(self, path: Path, missing: tuple[str, ...]) -> None:
        self.path = path
        self.missing = missing
        super().__init__(
            f"chunk at {path!r} missing required frontmatter fields: {list(missing)}",
        )


_REQUIRED_FRONTMATTER = ("source_path", "content_hash")


def load_chunk(path: Path, *, strict: bool = False) -> Chunk | None:
    """Load one chunk.

    By default, returns None on any load failure so callers written
    against the historical API continue to work; set `strict=True` to
    raise `MalformedChunkError` on missing required fields. The
    `iter_chunks(strict=True)` path surfaces corrupted chunks instead
    of hiding them so the operator can re-ingest or delete, per
    Gemini's `silent data loss` finding.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        if strict:
            raise
        return None
    fm, body = _split_frontmatter(text)
    missing = tuple(k for k in _REQUIRED_FRONTMATTER if k not in fm)
    if missing:
        if strict:
            raise MalformedChunkError(path, missing)
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


def iter_chunks(company_dir: Path, *, strict: bool = False):
    """Yield every valid chunk in the company's KB chunks dir.

    With `strict=True`, malformed chunks raise `MalformedChunkError`
    on encounter; the caller aborts enumeration and handles the
    corruption. Default behaviour is permissive for legacy callers.
    """
    chunks_dir = company_dir / CHUNKS_SUBDIR
    if not chunks_dir.exists():
        return
    for p in sorted(chunks_dir.glob("*.md")):
        ch = load_chunk(p, strict=strict)
        if ch is not None:
            yield ch


def find_malformed_chunks(company_dir: Path) -> list[tuple[Path, tuple[str, ...]]]:
    """Walk the chunks dir and return every malformed chunk with the
    list of missing required fields. Intended for a startup check and
    the Tension HUD: callers surface these to the operator instead of
    silently skipping. Returns an empty list when everything is clean."""
    chunks_dir = company_dir / CHUNKS_SUBDIR
    problems: list[tuple[Path, tuple[str, ...]]] = []
    if not chunks_dir.exists():
        return problems
    for p in sorted(chunks_dir.glob("*.md")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            problems.append((p, ("<unreadable>",)))
            continue
        fm, _ = _split_frontmatter(text)
        missing = tuple(k for k in _REQUIRED_FRONTMATTER if k not in fm)
        if missing:
            problems.append((p, missing))
    return problems


def load_all(company_dir: Path) -> list[Chunk]:
    return list(iter_chunks(company_dir))
