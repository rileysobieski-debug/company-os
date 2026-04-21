"""
Chunk → Claim adapter
=====================
Lifts a KB chunk (Phase 4 primitive) into a Priority 3 `Claim`
(Phase 2 primitive) so retrieved knowledge flows through
`core.primitives.state.resolve_conflict()` the same way any other
authority-tagged claim does.

Provenance mapping:
  - `updated_at`  ← chunk.ingested_at  (KB chunks are immutable per ingest)
  - `updated_by`  ← "kb.ingest"
  - `source_path` ← chunk.source_path  (relative to company dir)
  - `ingested_at` ← chunk.ingested_at

Ref format: "priority_3_kb:<source_path>#c<chunk_index>" — the naming
convention cited throughout §1.5 and §7.2 of the master plan.
"""
from __future__ import annotations

from collections.abc import Iterable

from core.kb.retrieve import Match
from core.kb.store import Chunk
from core.primitives.state import AuthorityPriority, Claim


def chunk_to_claim(chunk: Chunk) -> Claim:
    """Wrap one KB chunk as a Priority 3 Claim with the chunk's body as
    content. Raises ValueError if the chunk lacks an `ingested_at` —
    without that stamp the resulting claim would fail `check_provenance`."""
    if not chunk.ingested_at:
        raise ValueError(
            f"chunk {chunk.path} missing ingested_at — can't produce a valid Claim"
        )
    provenance = {
        "updated_at": chunk.ingested_at,
        "updated_by": "kb.ingest",
        "source_path": chunk.source_path,
        "ingested_at": chunk.ingested_at,
    }
    ref = f"priority_3_kb:{chunk.source_path}#c{chunk.chunk_index}"
    return Claim(
        priority=AuthorityPriority.KB,
        content=chunk.body,
        ref=ref,
        provenance=provenance,
    )


def matches_to_claims(matches: Iterable[Match]) -> list[Claim]:
    """Bulk-convert kb_query results to Claims, preserving order."""
    return [chunk_to_claim(m.chunk) for m in matches]
