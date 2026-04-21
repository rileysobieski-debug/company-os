"""Kill-switch retro data model + 3-question renderer (Phase 12.2 — §0.5)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.adversary import (
    RETROS_SUBDIR,
    KillSwitchRetro,
    iter_retros,
    load_retro,
    render_retro,
    retro_path,
    retros_since,
    write_retro,
)


def _sample() -> KillSwitchRetro:
    return KillSwitchRetro(
        specialist_id="copywriter",
        created_at="2026-04-18T12:00:00+00:00",
        expected="Distinct voice matching brand foundation.",
        saw="Generic luxury cliché. 'Handcrafted discerning palates.'",
        fix="Re-anchor to settled convictions block and ban the cliché list.",
        last_known_good_prompt_ref="copywriter.md@rev-2026-03-28",
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------
def test_roundtrip_through_dict() -> None:
    original = _sample()
    restored = KillSwitchRetro.from_dict(original.to_dict())
    assert restored == original


def test_defaults_fill_missing_optional_fields() -> None:
    r = KillSwitchRetro.from_dict({
        "specialist_id": "s",
        "created_at": "2026-04-18T12:00:00+00:00",
    })
    assert r.expected == ""
    assert r.saw == ""
    assert r.fix == ""
    assert r.last_known_good_prompt_ref == ""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def test_render_has_all_three_questions() -> None:
    md = render_retro(_sample())
    assert "## What did you expect?" in md
    assert "## What did you see?" in md
    assert "## What would fix it?" in md


def test_render_includes_specialist_in_heading() -> None:
    md = render_retro(_sample())
    assert md.startswith("# Kill-switch retro — copywriter")


def test_render_shows_prompt_restoration_ref_when_present() -> None:
    md = render_retro(_sample())
    assert "copywriter.md@rev-2026-03-28" in md
    assert "Prompt restored to" in md


def test_render_marks_missing_answers_as_unrecorded() -> None:
    empty = KillSwitchRetro(
        specialist_id="s",
        created_at="2026-04-18T12:00:00+00:00",
        expected="",
        saw="",
        fix="",
    )
    md = render_retro(empty)
    assert md.count("_(not recorded)_") == 3


# ---------------------------------------------------------------------------
# Disk persistence
# ---------------------------------------------------------------------------
def test_retro_path_encodes_date_and_specialist() -> None:
    p = retro_path(Path("/co"), _sample())
    assert p.name == "2026-04-18-copywriter.md"
    assert p.parent.as_posix().endswith(RETROS_SUBDIR)


def test_write_creates_markdown_and_json_sidecar(tmp_path: Path) -> None:
    path = write_retro(tmp_path, _sample())
    assert path.exists()
    assert path.with_suffix(".json").exists()


def test_load_retro_from_md_or_json_path(tmp_path: Path) -> None:
    path = write_retro(tmp_path, _sample())
    from_md = load_retro(path)
    from_json = load_retro(path.with_suffix(".json"))
    assert from_md == from_json == _sample()


def test_iter_retros_reads_all(tmp_path: Path) -> None:
    r1 = _sample()
    r2 = KillSwitchRetro(
        specialist_id="market-researcher",
        created_at="2026-04-19T12:00:00+00:00",
        expected="x", saw="y", fix="z",
    )
    write_retro(tmp_path, r1)
    write_retro(tmp_path, r2)
    loaded = iter_retros(tmp_path)
    assert {r.specialist_id for r in loaded} == {"copywriter", "market-researcher"}


def test_iter_empty_returns_empty() -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert iter_retros(Path(d)) == []


# ---------------------------------------------------------------------------
# retros_since filter
# ---------------------------------------------------------------------------
def test_retros_since_filters_by_specialist() -> None:
    r1 = _sample()
    r2 = KillSwitchRetro(
        specialist_id="market-researcher",
        created_at="2026-04-18T12:00:00+00:00",
        expected="x", saw="y", fix="z",
    )
    out = retros_since([r1, r2], specialist_id="copywriter")
    assert len(out) == 1
    assert out[0].specialist_id == "copywriter"


def test_retros_since_filters_by_timestamp() -> None:
    old = KillSwitchRetro(
        specialist_id="s", created_at="2026-04-01T12:00:00+00:00",
        expected="x", saw="y", fix="z",
    )
    new = KillSwitchRetro(
        specialist_id="s", created_at="2026-04-20T12:00:00+00:00",
        expected="x", saw="y", fix="z",
    )
    out = retros_since([old, new], since_iso="2026-04-15T00:00:00+00:00")
    assert len(out) == 1
    assert out[0].created_at == "2026-04-20T12:00:00+00:00"


def test_retros_since_no_filters_returns_all() -> None:
    r1 = _sample()
    r2 = KillSwitchRetro(
        specialist_id="x", created_at="2026-04-01T12:00:00+00:00",
        expected="", saw="", fix="",
    )
    out = retros_since([r1, r2])
    assert len(out) == 2
