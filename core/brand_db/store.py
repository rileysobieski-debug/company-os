"""
core/brand_db/store.py — Brand DB filesystem view
==================================================
Parses and indexes the two brand-DB content types:

  * Voice entries: `<company>/brand-db/voice/*.md` — markdown body with
    YAML frontmatter carrying `added_at`, `verdict`, plus optional tags
    and a description.

  * Image entries: `<company>/brand-db/images/<name>` with a sidecar YAML
    at `<company>/brand-db/images/<name>.yaml` carrying the same keys.
    The image file itself is opaque to the framework; the sidecar is
    where the taste signal lives.

Both entry types share a common `BrandEntry` supertype so callers can
treat them uniformly when mapping to Priority 4 claims.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Iterator

import yaml

VOICE_SUBDIR = "brand-db/voice"
IMAGES_SUBDIR = "brand-db/images"
SIDECAR_SUFFIX = ".yaml"

VALID_VERDICTS = frozenset({"gold", "acceptable", "reference", "anti-exemplar"})


@dataclass(frozen=True)
class BrandEntry:
    """Shared envelope for a brand-DB entry, voice or image.

    `kind` is one of "voice"|"image". `path` is the canonical file
    (the .md for voice, the image file for images — NOT the sidecar).
    `content` is the prose body for voice, the description/tags bundle
    for images. `ref` is the vault-relative path string used in claim refs.
    """

    kind: str
    path: Path
    ref: str
    added_at: str
    verdict: str
    tags: tuple[str, ...] = field(default_factory=tuple)
    description: str = ""
    content: str = ""


VoiceEntry = BrandEntry
ImageEntry = BrandEntry


# ---------------------------------------------------------------------------
# Frontmatter / YAML helpers
# ---------------------------------------------------------------------------
def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Parse a `---\\n...\\n---\\n` YAML frontmatter block.

    Unlike the KB store (key:value only), brand-DB frontmatter may carry
    a list of tags, so we parse it with `yaml.safe_load`.
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    raw = text[4:end]
    body = text[end + 5:]
    try:
        fm = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(fm, dict):
        return {}, text
    return fm, body


def _normalise_tags(val) -> tuple[str, ...]:
    if val is None:
        return ()
    if isinstance(val, str):
        return (val,) if val else ()
    try:
        return tuple(str(t) for t in val if str(t))
    except TypeError:
        return ()


def _relative_ref(company_dir: Path, file_path: Path) -> str:
    """Return a forward-slashed vault-relative path to use as a claim ref."""
    try:
        rel = file_path.resolve().relative_to(company_dir.resolve())
    except ValueError:
        rel = file_path
    return rel.as_posix() if hasattr(rel, "as_posix") else str(rel).replace("\\", "/")


def _normalise_timestamp(val) -> str:
    """YAML silently coerces ISO-8601 strings to `datetime`/`date`; round-trip
    those back to ISO strings so brand claims carry stable provenance."""
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return str(val).strip()


def _make_entry(
    *,
    kind: str,
    path: Path,
    ref: str,
    fm: dict,
    content: str,
) -> BrandEntry | None:
    added_at = _normalise_timestamp(fm.get("added_at"))
    verdict = str(fm.get("verdict", "")).strip()
    if not added_at or verdict not in VALID_VERDICTS:
        return None
    return BrandEntry(
        kind=kind,
        path=path,
        ref=ref,
        added_at=added_at,
        verdict=verdict,
        tags=_normalise_tags(fm.get("tags")),
        description=str(fm.get("description", "")),
        content=content,
    )


# ---------------------------------------------------------------------------
# Voice entries
# ---------------------------------------------------------------------------
def load_voice_entry(company_dir: Path, path: Path) -> VoiceEntry | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    fm, body = _split_frontmatter(text)
    if not fm:
        return None
    return _make_entry(
        kind="voice",
        path=path,
        ref=_relative_ref(company_dir, path),
        fm=fm,
        content=body.strip(),
    )


def iter_voice_entries(company_dir: Path) -> Iterator[VoiceEntry]:
    voice_dir = company_dir / VOICE_SUBDIR
    if not voice_dir.exists():
        return
    for p in sorted(voice_dir.glob("*.md")):
        entry = load_voice_entry(company_dir, p)
        if entry is not None:
            yield entry


# ---------------------------------------------------------------------------
# Image entries
# ---------------------------------------------------------------------------
def load_image_entry(company_dir: Path, image_path: Path) -> ImageEntry | None:
    """Load the brand-DB image entry for `image_path`. The sidecar is
    `<image_path>.yaml`; an image without a sidecar is not a valid brand
    entry and is skipped."""
    sidecar = image_path.with_suffix(image_path.suffix + SIDECAR_SUFFIX)
    if not sidecar.exists():
        return None
    try:
        fm = yaml.safe_load(sidecar.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(fm, dict):
        return None
    description = str(fm.get("description", ""))
    tags = _normalise_tags(fm.get("tags"))
    # For images the descriptive content is the description + tags —
    # that's the text a caller can diff against a draft or use as a
    # retrieval key.
    content_bits = [description] if description else []
    if tags:
        content_bits.append("tags: " + ", ".join(tags))
    return _make_entry(
        kind="image",
        path=image_path,
        ref=_relative_ref(company_dir, image_path),
        fm=fm,
        content="\n".join(content_bits),
    )


def iter_image_entries(company_dir: Path) -> Iterator[ImageEntry]:
    images_dir = company_dir / IMAGES_SUBDIR
    if not images_dir.exists():
        return
    for p in sorted(images_dir.iterdir()):
        if not p.is_file():
            continue
        if p.name.endswith(SIDECAR_SUFFIX):
            continue
        entry = load_image_entry(company_dir, p)
        if entry is not None:
            yield entry


# ---------------------------------------------------------------------------
# Bulk
# ---------------------------------------------------------------------------
def load_all_entries(company_dir: Path) -> list[BrandEntry]:
    """All voice + image entries, voice first, then images, both
    alphabetical by path. Stable order so downstream code can treat this
    as the canonical brand-DB view."""
    return [
        *iter_voice_entries(company_dir),
        *iter_image_entries(company_dir),
    ]
