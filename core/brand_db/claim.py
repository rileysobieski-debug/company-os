"""
BrandEntry → Claim adapter
==========================
Lifts a brand-DB entry into a Priority 4 `Claim` (Phase 2 primitive) so
voice/aesthetic references flow through `resolve_conflict()` with the
same machinery as every other authority-tagged claim.

Provenance mapping:
  - `updated_at`  ← entry.added_at (brand-DB entries are append-only;
                    the author stamps `added_at` at submission time)
  - `updated_by`  ← "brand.curator"
  - `source_path` ← entry.ref (vault-relative forward-slashed path)
  - `ingested_at` ← entry.added_at

Ref format: "priority_4_brand:<ref>" — aligns with §1.5 citation style.
"""
from __future__ import annotations

from collections.abc import Iterable

from core.brand_db.store import BrandEntry
from core.primitives.state import AuthorityPriority, Claim


def brand_entry_to_claim(entry: BrandEntry) -> Claim:
    """Wrap one brand-DB entry as a Priority 4 Claim. Raises ValueError
    if the entry lacks `added_at` — without that stamp the resulting
    claim would fail `check_provenance()`."""
    if not entry.added_at:
        raise ValueError(
            f"brand entry {entry.ref!r} missing added_at — cannot produce a valid Claim"
        )
    provenance = {
        "updated_at": entry.added_at,
        "updated_by": "brand.curator",
        "source_path": entry.ref,
        "ingested_at": entry.added_at,
    }
    content = {
        "kind": entry.kind,
        "verdict": entry.verdict,
        "tags": list(entry.tags),
        "description": entry.description,
        "body": entry.content,
    }
    return Claim(
        priority=AuthorityPriority.BRAND,
        content=content,
        ref=f"priority_4_brand:{entry.ref}",
        provenance=provenance,
    )


def entries_to_claims(entries: Iterable[BrandEntry]) -> list[Claim]:
    return [brand_entry_to_claim(e) for e in entries]
