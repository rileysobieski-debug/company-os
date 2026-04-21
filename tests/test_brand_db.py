"""Brand DB store + Priority 4 claim adapter (Phase 6.1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.brand_db import (
    VALID_VERDICTS,
    BrandEntry,
    brand_entry_to_claim,
    entries_to_claims,
    iter_image_entries,
    iter_voice_entries,
    load_all_entries,
    load_image_entry,
    load_voice_entry,
)
from core.brand_db.store import IMAGES_SUBDIR, VOICE_SUBDIR
from core.primitives.state import (
    AuthorityPriority,
    ProvenanceStatus,
    check_provenance,
    resolve_conflict,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _write_voice(company_dir: Path, name: str, fm: str, body: str) -> Path:
    vdir = company_dir / VOICE_SUBDIR
    vdir.mkdir(parents=True, exist_ok=True)
    path = vdir / f"{name}.md"
    path.write_text(f"---\n{fm}\n---\n{body}", encoding="utf-8")
    return path


def _write_image(
    company_dir: Path, name: str, sidecar_yaml: str, image_bytes: bytes = b"png"
) -> Path:
    idir = company_dir / IMAGES_SUBDIR
    idir.mkdir(parents=True, exist_ok=True)
    img_path = idir / name
    img_path.write_bytes(image_bytes)
    sidecar = img_path.with_suffix(img_path.suffix + ".yaml")
    sidecar.write_text(sidecar_yaml, encoding="utf-8")
    return img_path


VALID_VOICE_FM = (
    "added_at: 2026-04-17T10:00:00+00:00\n"
    "verdict: gold\n"
    "tags:\n  - coastal\n  - restrained\n"
    'description: "Ironbound island voice — RFK 1968 cadence."'
)
VALID_VOICE_BODY = (
    "We write like a lighthouse keeper: short, spare, and lit from inside."
)


# ---------------------------------------------------------------------------
# Voice entries
# ---------------------------------------------------------------------------
def test_load_voice_entry_parses_valid_file(tmp_path: Path) -> None:
    path = _write_voice(tmp_path, "lighthouse", VALID_VOICE_FM, VALID_VOICE_BODY)
    entry = load_voice_entry(tmp_path, path)
    assert entry is not None
    assert entry.kind == "voice"
    assert entry.verdict == "gold"
    assert entry.added_at == "2026-04-17T10:00:00+00:00"
    assert "coastal" in entry.tags and "restrained" in entry.tags
    assert entry.description.startswith("Ironbound")
    assert VALID_VOICE_BODY in entry.content
    assert entry.ref.endswith("brand-db/voice/lighthouse.md")


def test_load_voice_entry_rejects_missing_frontmatter(tmp_path: Path) -> None:
    vdir = tmp_path / VOICE_SUBDIR
    vdir.mkdir(parents=True)
    path = vdir / "nofm.md"
    path.write_text("just a body", encoding="utf-8")
    assert load_voice_entry(tmp_path, path) is None


def test_load_voice_entry_rejects_invalid_verdict(tmp_path: Path) -> None:
    bad_fm = "added_at: 2026-04-17T10:00:00+00:00\nverdict: maybe"
    path = _write_voice(tmp_path, "bad", bad_fm, "body")
    assert load_voice_entry(tmp_path, path) is None


def test_load_voice_entry_rejects_missing_added_at(tmp_path: Path) -> None:
    bad_fm = "verdict: gold"
    path = _write_voice(tmp_path, "bad", bad_fm, "body")
    assert load_voice_entry(tmp_path, path) is None


def test_iter_voice_entries_sorted(tmp_path: Path) -> None:
    _write_voice(tmp_path, "b", VALID_VOICE_FM, "body b")
    _write_voice(tmp_path, "a", VALID_VOICE_FM, "body a")
    entries = list(iter_voice_entries(tmp_path))
    names = [e.path.name for e in entries]
    assert names == ["a.md", "b.md"]


def test_iter_voice_entries_empty_when_dir_missing(tmp_path: Path) -> None:
    assert list(iter_voice_entries(tmp_path)) == []


# ---------------------------------------------------------------------------
# Image entries
# ---------------------------------------------------------------------------
def test_load_image_entry_reads_sidecar(tmp_path: Path) -> None:
    sidecar = (
        "added_at: 2026-04-17T11:00:00+00:00\n"
        "verdict: reference\n"
        "tags:\n  - sailing\n  - americana\n"
        'description: "RFK campaign whistle-stop photo"'
    )
    img = _write_image(tmp_path, "rfk-1968.jpg", sidecar)
    entry = load_image_entry(tmp_path, img)
    assert entry is not None
    assert entry.kind == "image"
    assert entry.verdict == "reference"
    assert "sailing" in entry.tags
    assert entry.description.startswith("RFK")
    assert "tags: sailing, americana" in entry.content
    assert entry.ref.endswith("brand-db/images/rfk-1968.jpg")


def test_load_image_entry_without_sidecar_skipped(tmp_path: Path) -> None:
    idir = tmp_path / IMAGES_SUBDIR
    idir.mkdir(parents=True)
    img = idir / "orphan.jpg"
    img.write_bytes(b"png")
    assert load_image_entry(tmp_path, img) is None


def test_iter_image_entries_skips_sidecars_and_non_files(tmp_path: Path) -> None:
    sidecar = "added_at: 2026-04-17T11:00:00+00:00\nverdict: acceptable"
    _write_image(tmp_path, "one.jpg", sidecar)
    _write_image(tmp_path, "two.png", sidecar)
    # Add a subdirectory that shouldn't be picked up.
    (tmp_path / IMAGES_SUBDIR / "misc").mkdir()
    entries = list(iter_image_entries(tmp_path))
    names = sorted(e.path.name for e in entries)
    assert names == ["one.jpg", "two.png"]


def test_anti_exemplar_verdict_accepted(tmp_path: Path) -> None:
    fm = "added_at: 2026-04-17T10:00:00+00:00\nverdict: anti-exemplar"
    path = _write_voice(tmp_path, "bad-voice", fm, "too corporate")
    entry = load_voice_entry(tmp_path, path)
    assert entry is not None
    assert entry.verdict == "anti-exemplar"


def test_all_valid_verdicts_recognized() -> None:
    assert VALID_VERDICTS == {"gold", "acceptable", "reference", "anti-exemplar"}


# ---------------------------------------------------------------------------
# load_all_entries
# ---------------------------------------------------------------------------
def test_load_all_entries_voice_first(tmp_path: Path) -> None:
    _write_voice(tmp_path, "v1", VALID_VOICE_FM, "voice body")
    sidecar = "added_at: 2026-04-17T11:00:00+00:00\nverdict: gold"
    _write_image(tmp_path, "i1.jpg", sidecar)
    entries = load_all_entries(tmp_path)
    assert len(entries) == 2
    assert entries[0].kind == "voice"
    assert entries[1].kind == "image"


# ---------------------------------------------------------------------------
# Claim adapter
# ---------------------------------------------------------------------------
def test_brand_entry_to_claim_produces_priority_4(tmp_path: Path) -> None:
    path = _write_voice(tmp_path, "v", VALID_VOICE_FM, VALID_VOICE_BODY)
    entry = load_voice_entry(tmp_path, path)
    assert entry is not None
    claim = brand_entry_to_claim(entry)
    assert claim.priority is AuthorityPriority.BRAND
    assert claim.priority.value == 4
    assert claim.ref.startswith("priority_4_brand:")
    assert check_provenance(claim.provenance) is ProvenanceStatus.VALID


def test_brand_entry_to_claim_rejects_missing_added_at() -> None:
    bad = BrandEntry(
        kind="voice", path=Path("x"), ref="x", added_at="", verdict="gold"
    )
    with pytest.raises(ValueError, match="missing added_at"):
        brand_entry_to_claim(bad)


def test_brand_claim_loses_to_kb_and_wins_over_taste(tmp_path: Path) -> None:
    """Brand is Priority 4 — should lose to any Priority 1/2/3 and beat
    Priority 5/6/7/8. Spot-check vs KB (3) and TASTE (7)."""
    path = _write_voice(tmp_path, "v", VALID_VOICE_FM, VALID_VOICE_BODY)
    entry = load_voice_entry(tmp_path, path)
    brand = brand_entry_to_claim(entry)

    from core.primitives.state import Claim
    kb_prov = {
        "updated_at": "2026-04-17T00:00:00+00:00",
        "updated_by": "kb.ingest",
        "source_path": "knowledge-base/source/x.md",
        "ingested_at": "2026-04-17T00:00:00+00:00",
    }
    kb_claim = Claim(
        priority=AuthorityPriority.KB,
        content="kb fact",
        ref="priority_3_kb:x.md#c0",
        provenance=kb_prov,
    )
    taste_prov = {
        "updated_at": "2026-04-17T00:00:00+00:00",
        "updated_by": "taste.learn",
        "source_path": "taste/profile.yaml",
        "ingested_at": "2026-04-17T00:00:00+00:00",
    }
    taste_claim = Claim(
        priority=AuthorityPriority.TASTE,
        content={"fit": 0.8},
        ref="priority_7_taste:profile.yaml",
        provenance=taste_prov,
    )
    # Brand loses to KB
    assert resolve_conflict(brand, kb_claim).winner is kb_claim
    # Brand wins over Taste
    assert resolve_conflict(brand, taste_claim).winner is brand


def test_entries_to_claims_bulk(tmp_path: Path) -> None:
    _write_voice(tmp_path, "a", VALID_VOICE_FM, "a body")
    _write_voice(tmp_path, "b", VALID_VOICE_FM, "b body")
    entries = load_all_entries(tmp_path)
    claims = entries_to_claims(entries)
    assert len(claims) == 2
    assert all(c.priority is AuthorityPriority.BRAND for c in claims)
