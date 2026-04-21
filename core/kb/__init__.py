"""
core/kb — per-company knowledge-base ingest + retrieval.
=========================================================
Phase 4 primitive. Source docs land in `<company>/knowledge-base/source/`;
ingest produces canonical chunks in `<company>/knowledge-base/chunks/`
with provenance frontmatter that flows into `resolve_conflict()` at
Priority 3 (KNOWLEDGE_BASE) per §1.5 of the master plan.

=== Entry points ===
  ingest_all(company_dir) -> IngestResult
  ingest_source_doc(source_path, company_dir) -> list[Path]
"""
from core.kb.claim import chunk_to_claim, matches_to_claims
from core.kb.ingest import (
    IngestResult,
    chunk_text,
    ingest_all,
    ingest_source_doc,
)
from core.kb.retrieve import Match, kb_query
from core.kb.store import Chunk, iter_chunks, load_all, load_chunk

__all__ = [
    "Chunk",
    "IngestResult",
    "Match",
    "chunk_text",
    "chunk_to_claim",
    "ingest_all",
    "ingest_source_doc",
    "iter_chunks",
    "kb_query",
    "load_all",
    "load_chunk",
    "matches_to_claims",
]
