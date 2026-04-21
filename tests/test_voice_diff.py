"""voice.diff_from_brand pure skill (Phase 6.2)."""
from __future__ import annotations

from pathlib import Path

from core.brand_db.store import BrandEntry
from core.primitives.voice import VoiceDiff, diff_from_brand


def _make_voice(
    *,
    verdict: str,
    content: str = "",
    description: str = "",
    tags: tuple[str, ...] = (),
    name: str = "v",
) -> BrandEntry:
    return BrandEntry(
        kind="voice",
        path=Path(f"brand-db/voice/{name}.md"),
        ref=f"brand-db/voice/{name}.md",
        added_at="2026-04-17T10:00:00+00:00",
        verdict=verdict,
        tags=tags,
        description=description,
        content=content,
    )


def test_empty_entries_returns_zero_alignment() -> None:
    result = diff_from_brand("any draft", [])
    assert result.gold_alignment == 0.0
    assert result.entries_considered == 0
    assert "nothing to diff" in result.reason


def test_image_entries_ignored() -> None:
    image = BrandEntry(
        kind="image",
        path=Path("brand-db/images/x.jpg"),
        ref="brand-db/images/x.jpg",
        added_at="2026-04-17T10:00:00+00:00",
        verdict="gold",
        content="coastal lighthouse",
    )
    result = diff_from_brand("any draft", [image])
    assert result.entries_considered == 0


def test_perfect_gold_alignment_when_draft_matches_body() -> None:
    voice = _make_voice(
        verdict="gold",
        content="lighthouse keeper short spare lit inside",
    )
    result = diff_from_brand(
        "lighthouse keeper short spare lit inside", [voice]
    )
    assert result.gold_alignment == 1.0
    assert result.missing_gold_markers == ()


def test_partial_alignment_reports_missing_markers() -> None:
    voice = _make_voice(
        verdict="gold",
        content="lighthouse keeper short spare lit inside",
    )
    # Draft only hits "lighthouse" + "spare"
    result = diff_from_brand("we admire a spare lighthouse", [voice])
    assert 0.0 < result.gold_alignment < 1.0
    assert len(result.missing_gold_markers) > 0
    assert "keeper" in result.missing_gold_markers or "lit" in result.missing_gold_markers


def test_anti_exemplar_hits_flagged() -> None:
    gold = _make_voice(verdict="gold", content="spare honest local")
    anti = _make_voice(
        verdict="anti-exemplar",
        content="synergize leverage disrupt innovative bandwidth",
        name="bad",
    )
    draft = "we will leverage synergy and disrupt the market"
    result = diff_from_brand(draft, [gold, anti])
    assert len(result.anti_exemplar_hits) >= 1
    hit_set = set(result.anti_exemplar_hits)
    assert "leverage" in hit_set or "disrupt" in hit_set


def test_tags_contribute_as_markers() -> None:
    voice = _make_voice(
        verdict="gold",
        content="body text",
        tags=("coastal", "restrained"),
    )
    # Draft contains the tag 'coastal' but not 'restrained' or 'body'/'text'
    result = diff_from_brand("coastal breezes and quiet harbors", [voice])
    assert result.gold_alignment > 0
    # "coastal" single-token marker should be present.
    assert "coastal" not in result.missing_gold_markers


def test_determinism() -> None:
    voice = _make_voice(
        verdict="gold",
        content="lighthouse keeper short spare",
    )
    r1 = diff_from_brand("keeper spare", [voice])
    r2 = diff_from_brand("keeper spare", [voice])
    assert r1 == r2


def test_acceptable_and_reference_verdicts_do_not_affect_diff() -> None:
    # Only gold + anti-exemplar drive alignment/hits. 'acceptable' and
    # 'reference' entries are present in the corpus but neutral.
    gold = _make_voice(verdict="gold", content="spare", name="g")
    acceptable = _make_voice(
        verdict="acceptable", content="verbose padding filler", name="a"
    )
    result = diff_from_brand("verbose padding", [gold, acceptable])
    # "verbose" and "padding" should NOT register as anti-exemplar hits
    # (acceptable ≠ anti-exemplar).
    assert result.anti_exemplar_hits == ()
    # entries_considered counts ALL voice entries; gold alignment only uses gold.
    assert result.entries_considered == 2


def test_reason_mentions_percentages() -> None:
    gold = _make_voice(verdict="gold", content="alpha beta gamma")
    result = diff_from_brand("alpha beta gamma", [gold])
    assert "100%" in result.reason


def test_description_folded_into_gold_corpus() -> None:
    voice = _make_voice(
        verdict="gold",
        description="Our voice is like a lighthouse keeper — spare and lit.",
        content="body",
    )
    # Draft hits description-derived markers but not 'body'.
    result = diff_from_brand("we are a spare lighthouse keeper", [voice])
    assert result.gold_alignment > 0


def test_voice_diff_is_frozen() -> None:
    diff = VoiceDiff(gold_alignment=0.5)
    import dataclasses
    try:
        diff.gold_alignment = 0.7
    except dataclasses.FrozenInstanceError:
        assert True
        return
    assert False, "VoiceDiff should be frozen"
