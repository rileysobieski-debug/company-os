"""Brand DB — Priority 4 voice + aesthetic references (§1.5).

Voice entries live at `<company>/brand-db/voice/*.md` with a frontmatter
block (`added_at`, `verdict`, plus free-form tags). Image entries live at
`<company>/brand-db/images/<name>` accompanied by a sidecar YAML at
`<company>/brand-db/images/<name>.yaml` carrying the same keys.

Both are surfaced through `core.primitives.state.resolve_conflict()` via
`brand_entry_to_claim()` in `core.brand_db.claim`.
"""
from __future__ import annotations

from core.brand_db.claim import brand_entry_to_claim, entries_to_claims
from core.brand_db.store import (
    VALID_VERDICTS,
    BrandEntry,
    ImageEntry,
    VoiceEntry,
    iter_image_entries,
    iter_voice_entries,
    load_all_entries,
    load_image_entry,
    load_voice_entry,
)

__all__ = [
    "BrandEntry",
    "VALID_VERDICTS",
    "VoiceEntry",
    "ImageEntry",
    "brand_entry_to_claim",
    "entries_to_claims",
    "iter_image_entries",
    "iter_voice_entries",
    "load_all_entries",
    "load_image_entry",
    "load_voice_entry",
]
