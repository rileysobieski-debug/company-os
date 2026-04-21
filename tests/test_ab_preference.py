"""A/B preference learning + first-principles axis discovery (Phase 6.4)."""
from __future__ import annotations

import pytest

from core.primitives.ab import (
    AXIS_DISCOVERY_MIN_MAGNITUDE,
    AXIS_DISCOVERY_MIN_PICKS,
    CONFIDENCE_PICKS_FOR_FULL,
    ABOption,
    ABPair,
    ABPick,
    AxisHypothesis,
    discover_axis,
    update_profile_from_picks,
)
from core.primitives.taste import TasteProfile

ISO = "2026-04-17T10:00:00+00:00"


def _pair(pair_id: str, a_axes: dict, b_axes: dict) -> ABPair:
    return ABPair(
        id=pair_id,
        a=ABOption(id=f"{pair_id}-a", axes=a_axes),
        b=ABOption(id=f"{pair_id}-b", axes=b_axes),
        shown_at=ISO,
    )


def _pick(pair_id: str, a_axes: dict, b_axes: dict, chosen: str) -> ABPick:
    return ABPick(
        pair=_pair(pair_id, a_axes, b_axes),
        chosen=chosen,
        picked_at=ISO,
    )


def _empty_profile() -> TasteProfile:
    return TasteProfile(last_fit_at="", picks_used=0, confidence=0.0, axes={})


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_ab_pick_rejects_invalid_chosen() -> None:
    pair = _pair("p1", {"coastal": 1.0}, {"coastal": -1.0})
    with pytest.raises(ValueError):
        ABPick(pair=pair, chosen="neither", picked_at=ISO)


# ---------------------------------------------------------------------------
# update_profile_from_picks
# ---------------------------------------------------------------------------
def test_empty_batch_returns_identical_profile() -> None:
    prior = TasteProfile(
        last_fit_at=ISO, picks_used=5, confidence=0.5, axes={"coastal": 0.8}
    )
    result = update_profile_from_picks(prior, [])
    assert result is prior  # no-op optimization: same reference


def test_first_batch_seeds_profile_from_zero() -> None:
    picks = [
        _pick("p1", {"coastal": 1.0}, {"coastal": -1.0}, chosen="a"),
        _pick("p2", {"coastal": 1.0}, {"coastal": -1.0}, chosen="a"),
    ]
    result = update_profile_from_picks(_empty_profile(), picks, now=ISO)
    # Each pick: delta = chosen(1) - rejected(-1) = +2, clamped to +1
    # Batch avg = +1. With prior_n=0 → batch_weight=1 → value=+1 clamped.
    assert result.axes["coastal"] == pytest.approx(1.0)
    assert result.picks_used == 2
    assert result.last_fit_at == ISO


def test_confidence_reaches_one_at_threshold() -> None:
    picks = [
        _pick(f"p{i}", {"coastal": 0.5}, {"coastal": -0.5}, chosen="a")
        for i in range(CONFIDENCE_PICKS_FOR_FULL)
    ]
    result = update_profile_from_picks(_empty_profile(), picks)
    assert result.confidence == pytest.approx(1.0)


def test_confidence_partial_for_partial_batch() -> None:
    picks = [
        _pick("p1", {"coastal": 0.5}, {"coastal": -0.5}, chosen="a"),
    ]
    result = update_profile_from_picks(_empty_profile(), picks)
    assert result.confidence == pytest.approx(1.0 / CONFIDENCE_PICKS_FOR_FULL)


def test_prior_axis_untouched_when_batch_silent_on_it() -> None:
    prior = TasteProfile(
        last_fit_at=ISO, picks_used=10, confidence=0.5,
        axes={"coastal": 0.8, "spare": 0.6},
    )
    # Batch only touches 'corporate'.
    picks = [
        _pick("p1", {"corporate": -1.0}, {"corporate": 1.0}, chosen="a"),
    ]
    result = update_profile_from_picks(prior, picks)
    assert result.axes["coastal"] == pytest.approx(0.8)
    assert result.axes["spare"] == pytest.approx(0.6)
    assert "corporate" in result.axes
    # "corporate" picked the (-1) side, so delta = -1 - 1 = -2, clamped to -1,
    # batch_weight = 1/11, prior_weight = 10/11, prior=0 → value = -1 * 1/11
    assert result.axes["corporate"] == pytest.approx(-1.0 / 11)


def test_conflicting_picks_drag_toward_zero() -> None:
    # Equal and opposite picks on same axis → average delta = 0 → new
    # axis value = prior_weight * prior_value.
    prior = TasteProfile(
        last_fit_at=ISO, picks_used=2, confidence=0.2, axes={"x": 0.9}
    )
    picks = [
        _pick("p1", {"x": 1.0}, {"x": -1.0}, chosen="a"),
        _pick("p2", {"x": 1.0}, {"x": -1.0}, chosen="b"),
    ]
    # average delta across batch: (+2 + -2) / 2 = 0 (clamped also 0)
    # prior_weight = 2/4 = 0.5, batch_weight = 0.5
    # new = 0.9 * 0.5 + 0 * 0.5 = 0.45
    result = update_profile_from_picks(prior, picks)
    assert result.axes["x"] == pytest.approx(0.45)


def test_update_is_pure() -> None:
    prior = TasteProfile(
        last_fit_at=ISO, picks_used=3, confidence=0.3, axes={"a": 0.5}
    )
    picks = [_pick("p1", {"a": 1.0}, {"a": -1.0}, chosen="a")]
    _ = update_profile_from_picks(prior, picks)
    # prior not mutated
    assert prior.picks_used == 3
    assert prior.axes == {"a": 0.5}


def test_axis_value_always_clamped_to_unit_interval() -> None:
    # Prior outside [-1,1] (shouldn't happen but defensive), plus batch
    # delta also large. Output must still clamp.
    prior = TasteProfile(
        last_fit_at=ISO, picks_used=1, confidence=0.1, axes={"x": 5.0}
    )
    picks = [_pick("p", {"x": 5.0}, {"x": -5.0}, chosen="a")]
    result = update_profile_from_picks(prior, picks)
    assert -1.0 <= result.axes["x"] <= 1.0


# ---------------------------------------------------------------------------
# discover_axis (first-principles)
# ---------------------------------------------------------------------------
def test_discover_axis_returns_none_below_min_picks() -> None:
    picks = [
        _pick("p1", {"x": 1.0}, {"x": -1.0}, chosen="a")
        for _ in range(AXIS_DISCOVERY_MIN_PICKS - 1)
    ]
    assert discover_axis(picks) is None


def test_discover_axis_finds_dominant_dimension() -> None:
    # Founder consistently picks the "coastal+" side. Other axes vary
    # randomly across pairs — "coastal" should emerge as the explanation.
    picks = [
        _pick("p1", {"coastal": 1.0, "noise": 0.2}, {"coastal": -1.0, "noise": -0.3}, "a"),
        _pick("p2", {"coastal": 0.8, "noise": -0.5}, {"coastal": -0.9, "noise": 0.5}, "a"),
        _pick("p3", {"coastal": 0.9, "noise": 0.1}, {"coastal": -0.7, "noise": -0.1}, "a"),
        _pick("p4", {"coastal": 1.0, "noise": 0.4}, {"coastal": -1.0, "noise": -0.4}, "a"),
    ]
    hyp = discover_axis(picks)
    assert hyp is not None
    assert hyp.axis == "coastal"
    assert hyp.magnitude > 0
    assert hyp.picks_supporting == 4


def test_discover_axis_handles_negative_polarity() -> None:
    # Founder picks AGAINST the high-"corporate" side every time.
    picks = [
        _pick("p1", {"corporate": 1.0}, {"corporate": -1.0}, "b"),
        _pick("p2", {"corporate": 0.8}, {"corporate": -0.9}, "b"),
        _pick("p3", {"corporate": 1.0}, {"corporate": -1.0}, "b"),
    ]
    hyp = discover_axis(picks)
    assert hyp is not None
    assert hyp.axis == "corporate"
    assert hyp.magnitude < 0
    assert "negative" in hyp.reason


def test_discover_axis_none_when_no_signal() -> None:
    # Perfectly conflicting picks on the only axis.
    picks = [
        _pick("p1", {"x": 1.0}, {"x": -1.0}, "a"),
        _pick("p2", {"x": 1.0}, {"x": -1.0}, "b"),
        _pick("p3", {"x": 1.0}, {"x": -1.0}, "a"),
        _pick("p4", {"x": 1.0}, {"x": -1.0}, "b"),
    ]
    assert discover_axis(picks) is None


def test_discover_axis_alphabetical_tiebreak() -> None:
    # Two axes with identical absolute magnitude → alphabetically-first
    # wins. Both "aardvark" and "zebra" get +2 deltas per pick; pick 3 of
    # each so the magnitudes are equal.
    picks = [
        _pick("p1", {"aardvark": 1.0, "zebra": 1.0}, {"aardvark": -1.0, "zebra": -1.0}, "a"),
        _pick("p2", {"aardvark": 1.0, "zebra": 1.0}, {"aardvark": -1.0, "zebra": -1.0}, "a"),
        _pick("p3", {"aardvark": 1.0, "zebra": 1.0}, {"aardvark": -1.0, "zebra": -1.0}, "a"),
    ]
    hyp = discover_axis(picks)
    assert hyp is not None
    assert hyp.axis == "aardvark"


def test_discover_axis_ignores_subthreshold_magnitude() -> None:
    # Per-pick delta = 2 * tiny; we need it strictly below MIN_MAGNITUDE.
    tiny = AXIS_DISCOVERY_MIN_MAGNITUDE / 4
    picks = [
        _pick("p1", {"x": tiny}, {"x": -tiny}, "a"),
        _pick("p2", {"x": tiny}, {"x": -tiny}, "a"),
        _pick("p3", {"x": tiny}, {"x": -tiny}, "a"),
    ]
    assert discover_axis(picks) is None


def test_axis_hypothesis_is_frozen() -> None:
    import dataclasses
    hyp = AxisHypothesis(axis="x", magnitude=0.5, picks_supporting=3, reason="r")
    with pytest.raises(dataclasses.FrozenInstanceError):
        hyp.axis = "y"  # type: ignore[misc]
