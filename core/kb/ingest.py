"""
Knowledge-base ingest — source docs → canonical chunks
=======================================================
Walks `<company_dir>/knowledge-base/source/` (markdown / plain-text files),
splits each into paragraph-grouped chunks of approximately `target_size`
characters, and writes one `.md` file per chunk into
`<company_dir>/knowledge-base/chunks/`.

Each chunk file carries YAML frontmatter with the provenance fields
declared for Priority 3 KNOWLEDGE_BASE in §1.5 of the master plan:

  source_path    — relative to the company dir (e.g. "knowledge-base/source/x.md")
  ingested_at    — ISO-8601 UTC timestamp at time of ingest
  source_asof    — date from the source's own frontmatter if present,
                   else the source file's mtime (ISO date)
  stale_after    — human-readable retention (default "180d")
  content_hash   — sha256 of the chunk body (first 16 hex chars)
  chunk_index    — 0-based index within the source doc

Idempotency: re-running ingest on an unchanged corpus produces identical
outputs (same filenames, same bodies). A chunk whose `content_hash` already
exists in the chunks dir is skipped — no rewrite, no timestamp churn.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_STALE_AFTER = "180d"
DEFAULT_CHUNK_SIZE = 1000  # characters per chunk, approximate
SOURCE_SUBDIR = "knowledge-base/source"
CHUNKS_SUBDIR = "knowledge-base/chunks"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------
@dataclass
class IngestResult:
    sources_scanned: int = 0
    chunks_written: int = 0
    chunks_skipped: int = 0
    chunks_by_source: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _slugify(name: str) -> str:
    s = _SLUG_RE.sub("-", name.lower()).strip("-")
    return s or "untitled"


def _content_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_source_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter_dict, body). Frontmatter is YAML-lite: KEY: value
    lines only. No nested structures; that's fine for source metadata."""
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


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
def chunk_text(text: str, target_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """Split `text` into chunks of ~`target_size` characters, respecting
    paragraph boundaries. Short docs return a single chunk; very long
    paragraphs that exceed `target_size` on their own are kept whole
    (readability > strict size cap)."""
    if not text.strip():
        return []
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paras:
        p_len = len(para)
        if current and current_len + p_len > target_size:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = p_len
        else:
            current.append(para)
            current_len += p_len + 2  # +2 for paragraph separator
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# ---------------------------------------------------------------------------
# Frontmatter emission
# ---------------------------------------------------------------------------
def _render_chunk(
    *,
    body: str,
    source_path_rel: str,
    ingested_at: str,
    source_asof: str,
    stale_after: str,
    content_hash: str,
    chunk_index: int,
) -> str:
    # Phase 14 — consolidated-2026-04-18 §9/§10.1: every chunk carries a
    # hash-backed integrity seal so that downstream verifiers can detect
    # tampering (frontmatter edits, body edits, or both). `updated_at`
    # and `updated_by` are set to ingest values — specialists can't
    # update a chunk without a re-ingest pass that rewrites the hash.
    from core.primitives.integrity import compute_integrity_hash  # local import — avoid cycle

    provenance = {
        "source_path": source_path_rel,
        "ingested_at": ingested_at,
        "source_asof": source_asof,
        "stale_after": stale_after,
        "content_hash": content_hash,
        "chunk_index": str(chunk_index),
        "updated_at": ingested_at,
        "updated_by": "engine:kb.ingest",
    }
    digest = compute_integrity_hash(body, provenance)

    fm_lines = [
        "---",
        f"source_path: {source_path_rel}",
        f"ingested_at: {ingested_at}",
        f"source_asof: {source_asof}",
        f"stale_after: {stale_after}",
        f"content_hash: {content_hash}",
        f"chunk_index: {chunk_index}",
        f"updated_at: {ingested_at}",
        f"updated_by: engine:kb.ingest",
        f"integrity_hash: {digest}",
        "---",
        "",
    ]
    return "\n".join(fm_lines) + body.rstrip() + "\n"


# ---------------------------------------------------------------------------
# Per-doc ingest
# ---------------------------------------------------------------------------
def ingest_source_doc(
    source_path: Path,
    company_dir: Path,
    *,
    target_size: int = DEFAULT_CHUNK_SIZE,
) -> list[Path]:
    """Ingest one source doc. Returns the list of chunk paths written
    (or skipped-as-unchanged). Does NOT delete chunks whose source was
    removed — that's the job of a future garbage-collect pass."""
    if not source_path.exists():
        return []
    raw = source_path.read_text(encoding="utf-8")
    fm, body = _parse_source_frontmatter(raw)

    # source_asof: explicit frontmatter wins; else source file mtime.
    source_asof = fm.get("asof") or fm.get("source_asof")
    if not source_asof:
        mtime = datetime.fromtimestamp(source_path.stat().st_mtime, tz=timezone.utc)
        source_asof = mtime.date().isoformat()

    stale_after = fm.get("stale_after", DEFAULT_STALE_AFTER)
    chunks = chunk_text(body, target_size=target_size)
    if not chunks:
        return []

    chunks_dir = company_dir / CHUNKS_SUBDIR
    chunks_dir.mkdir(parents=True, exist_ok=True)

    source_rel = source_path.relative_to(company_dir).as_posix()
    slug = _slugify(source_path.stem)
    ingested_at = _now_iso()
    written: list[Path] = []
    for i, body_part in enumerate(chunks):
        c_hash = _content_hash(body_part)
        out = chunks_dir / f"{slug}--{i:03d}-{c_hash}.md"
        if out.exists():
            written.append(out)
            continue
        rendered = _render_chunk(
            body=body_part,
            source_path_rel=source_rel,
            ingested_at=ingested_at,
            source_asof=source_asof,
            stale_after=stale_after,
            content_hash=c_hash,
            chunk_index=i,
        )
        out.write_text(rendered, encoding="utf-8")
        written.append(out)
    return written


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------
def ingest_all(
    company_dir: Path,
    *,
    target_size: int = DEFAULT_CHUNK_SIZE,
) -> IngestResult:
    """Walk source/ and ingest every markdown/txt file. Returns a tally."""
    result = IngestResult()
    source_dir = company_dir / SOURCE_SUBDIR
    if not source_dir.exists():
        return result

    for doc in sorted(source_dir.rglob("*")):
        if not doc.is_file():
            continue
        if doc.suffix.lower() not in {".md", ".txt"}:
            continue
        result.sources_scanned += 1
        try:
            before = _count_chunks_for(doc, company_dir)
            written = ingest_source_doc(doc, company_dir, target_size=target_size)
            after = len(written)
            result.chunks_by_source[doc.name] = after
            # A file that existed before is a "skipped" from this call's POV;
            # a file that didn't is a "written".
            new_count = max(0, after - before)
            result.chunks_written += new_count
            result.chunks_skipped += (after - new_count)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"{doc.name}: {exc}")
    return result


def _count_chunks_for(source_path: Path, company_dir: Path) -> int:
    """How many chunks already exist on disk for this source, by slug match."""
    chunks_dir = company_dir / CHUNKS_SUBDIR
    if not chunks_dir.exists():
        return 0
    slug = _slugify(source_path.stem)
    return sum(1 for p in chunks_dir.glob(f"{slug}--*.md"))
