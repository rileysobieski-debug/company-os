"""Adversary drift benchmark (Phase 12.3 — §0.5 prevention-of-drift)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.adversary import (
    CONSECUTIVE_FAILURES_TO_RESET,
    DRIFT_SAMPLE_SIZE,
    DRIFT_WINDOW_ACTIVATIONS,
    DRIFT_WINDOW_DAYS,
    MIN_MEDIAN,
    AdversaryRating,
    DriftWindow,
    ResetAction,
    append_rating,
    build_window,
    consider_reset_trigger,
    load_ratings,
    ratings_log_path,
    should_close_window,
)


# ---------------------------------------------------------------------------
# AdversaryRating construction
# ---------------------------------------------------------------------------
def test_rating_out_of_range_raises() -> None:
    with pytest.raises(ValueError, match=r"\[0, 5\]"):
        AdversaryRating(review_key="r", score=6)
    with pytest.raises(ValueError):
        AdversaryRating(review_key="r", score=-1)


def test_rating_accepts_endpoints() -> None:
    AdversaryRating(review_key="r", score=0)
    AdversaryRating(review_key="r", score=5)


# ---------------------------------------------------------------------------
# build_window
# ---------------------------------------------------------------------------
def _ratings(*scores: int) -> list[AdversaryRating]:
    return [AdversaryRating(review_key=f"r{i}", score=s) for i, s in enumerate(scores)]


def test_build_window_computes_median_from_ratings() -> None:
    w = build_window(
        "2026-04-01T00:00:00+00:00",
        "2026-04-30T00:00:00+00:00",
        _ratings(4, 3, 5),
        activations=3,
    )
    assert w.rating_median == 4.0
    assert w.passed  # 4.0 >= 3.0


def test_build_window_fails_when_median_below_threshold() -> None:
    w = build_window(
        "2026-04-01T00:00:00+00:00",
        "2026-04-30T00:00:00+00:00",
        _ratings(1, 2, 2),
        activations=3,
    )
    assert w.rating_median == 2.0
    assert not w.passed


def test_build_window_no_ratings_passes_by_default() -> None:
    """Zero-rating windows shouldn't trigger reset — no signal, hold."""
    w = build_window(
        "2026-04-01T00:00:00+00:00",
        "2026-04-30T00:00:00+00:00",
        [],
        activations=0,
    )
    assert w.rating_median is None
    assert w.passed


def test_build_window_honors_custom_threshold() -> None:
    # Median 3.0, but lifted threshold to 4.0 → should now fail.
    w = build_window(
        "2026-04-01T00:00:00+00:00",
        "2026-04-30T00:00:00+00:00",
        _ratings(3, 3, 3),
        activations=3,
        min_median=4.0,
    )
    assert not w.passed


# ---------------------------------------------------------------------------
# should_close_window
# ---------------------------------------------------------------------------
def test_closes_on_activations_threshold() -> None:
    assert should_close_window(
        "2026-04-18T12:00:00+00:00",
        activations=10,
        now=datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc),
    )


def test_closes_on_days_threshold() -> None:
    assert should_close_window(
        "2026-04-01T00:00:00+00:00",
        activations=3,
        now=datetime(2026, 5, 3, 0, 0, tzinfo=timezone.utc),  # 32 days
    )


def test_does_not_close_when_neither_met() -> None:
    assert not should_close_window(
        "2026-04-18T00:00:00+00:00",
        activations=3,
        now=datetime(2026, 4, 25, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# consider_reset_trigger
# ---------------------------------------------------------------------------
def _failing_window() -> DriftWindow:
    return build_window(
        "2026-04-01T00:00:00+00:00",
        "2026-04-30T00:00:00+00:00",
        _ratings(1, 1, 2),
        activations=3,
    )


def _passing_window() -> DriftWindow:
    return build_window(
        "2026-04-01T00:00:00+00:00",
        "2026-04-30T00:00:00+00:00",
        _ratings(4, 5, 4),
        activations=3,
    )


def test_single_failing_window_does_not_trigger_reset() -> None:
    decision = consider_reset_trigger([_failing_window()])
    assert decision.action is ResetAction.HOLD


def test_two_consecutive_failing_windows_trigger_reset() -> None:
    decision = consider_reset_trigger([_failing_window(), _failing_window()])
    assert decision.action is ResetAction.RESET
    assert decision.consecutive_failures == 2


def test_failing_then_passing_window_clears_to_hold() -> None:
    decision = consider_reset_trigger([_failing_window(), _passing_window()])
    assert decision.action is ResetAction.HOLD


def test_reset_only_looks_at_recent_tail() -> None:
    """Old failures don't count — only the last N windows."""
    decision = consider_reset_trigger([
        _failing_window(), _failing_window(),
        _passing_window(), _passing_window(),  # recent clear → HOLD
    ])
    assert decision.action is ResetAction.HOLD


def test_reset_trigger_respects_custom_threshold() -> None:
    # Require 3 consecutive fails; supplying 2 is not enough.
    decision = consider_reset_trigger(
        [_failing_window(), _failing_window()],
        consecutive_needed=3,
    )
    assert decision.action is ResetAction.HOLD


# ---------------------------------------------------------------------------
# JSONL ratings log
# ---------------------------------------------------------------------------
def test_append_creates_log_file(tmp_path: Path) -> None:
    path = append_rating(
        tmp_path,
        AdversaryRating(review_key="r1", score=3, created_at="2026-04-18T12:00:00+00:00"),
    )
    assert path.exists()
    assert path == ratings_log_path(tmp_path)


def test_append_roundtrip(tmp_path: Path) -> None:
    append_rating(tmp_path, AdversaryRating(
        review_key="r1", score=3, notes="stress-tested",
        created_at="2026-04-18T12:00:00+00:00",
    ))
    append_rating(tmp_path, AdversaryRating(
        review_key="r2", score=1, notes="performative",
        created_at="2026-04-19T12:00:00+00:00",
    ))
    loaded = load_ratings(tmp_path)
    assert len(loaded) == 2
    assert loaded[0].review_key == "r1"
    assert loaded[1].score == 1
    assert loaded[1].notes == "performative"


def test_load_empty_file_returns_empty(tmp_path: Path) -> None:
    assert load_ratings(tmp_path) == []


def test_load_skips_malformed_lines(tmp_path: Path) -> None:
    path = ratings_log_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"review_key": "r1", "score": 3}\n'
        'not json\n'
        '{"missing": "score"}\n'
        '{"review_key": "r2", "score": 4}\n',
        encoding="utf-8",
    )
    loaded = load_ratings(tmp_path)
    assert len(loaded) == 2
    assert [r.review_key for r in loaded] == ["r1", "r2"]


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------
def test_constants_match_plan_spec() -> None:
    assert DRIFT_WINDOW_DAYS == 30
    assert DRIFT_WINDOW_ACTIVATIONS == 10
    assert DRIFT_SAMPLE_SIZE == 3
    assert CONSECUTIVE_FAILURES_TO_RESET == 2
    assert MIN_MEDIAN == 3.0
