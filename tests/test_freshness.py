"""Assumption freshness clock (Phase 5.4 — §7.4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.primitives.freshness import (
    FRESH_DAYS,
    GRACE_DAYS,
    USES_THRESHOLD,
    Assumption,
    FreshnessStatus,
    demote,
    extend,
    is_citable_as_load_bearing,
    promote,
    record_use,
    tick,
)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make(created: datetime, **kw) -> Assumption:
    return Assumption(
        id="a1",
        content="Maine TTB permits take 90 days",
        created_at=_iso(created),
        **kw,
    )


def test_fresh_stays_fresh_under_thresholds() -> None:
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    a = _make(created, uses=2)
    result = tick(a, now=created + timedelta(days=5))
    assert result.status is FreshnessStatus.FRESH


def test_fresh_to_needs_review_on_uses_threshold() -> None:
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    a = _make(created, uses=USES_THRESHOLD)  # reaches threshold
    result = tick(a, now=created + timedelta(days=1))
    assert result.status is FreshnessStatus.NEEDS_REVIEW
    assert result.review_started_at is not None


def test_fresh_to_needs_review_on_age_threshold() -> None:
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    a = _make(created, uses=1)
    result = tick(a, now=created + timedelta(days=FRESH_DAYS + 1))
    assert result.status is FreshnessStatus.NEEDS_REVIEW


def test_needs_review_to_grace_after_grace_window() -> None:
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    review_started = created + timedelta(days=FRESH_DAYS + 1)
    a = _make(
        created,
        uses=USES_THRESHOLD,
        status=FreshnessStatus.NEEDS_REVIEW,
        review_started_at=_iso(review_started),
    )
    result = tick(a, now=review_started + timedelta(days=GRACE_DAYS + 1))
    assert result.status is FreshnessStatus.GRACE


def test_grace_to_demoted_at_grace_end() -> None:
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    review_started = created + timedelta(days=FRESH_DAYS + 1)
    a = _make(
        created,
        uses=USES_THRESHOLD,
        status=FreshnessStatus.GRACE,
        review_started_at=_iso(review_started),
    )
    result = tick(a, now=review_started + timedelta(days=GRACE_DAYS * 2 + 1))
    assert result.status is FreshnessStatus.DEMOTED


def test_demoted_is_terminal() -> None:
    a = Assumption(
        id="x",
        content="old fact",
        created_at=_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)),
        status=FreshnessStatus.DEMOTED,
    )
    # No matter how far into the future we tick, status stays DEMOTED.
    later = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert tick(a, now=later).status is FreshnessStatus.DEMOTED


def test_extend_resets_to_fresh() -> None:
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    a = _make(
        created,
        uses=USES_THRESHOLD,
        status=FreshnessStatus.NEEDS_REVIEW,
        review_started_at=_iso(created + timedelta(days=FRESH_DAYS + 1)),
    )
    new_now = datetime(2026, 5, 1, tzinfo=timezone.utc)
    extended = extend(a, now=new_now)
    assert extended.status is FreshnessStatus.FRESH
    assert extended.uses == 0
    assert extended.created_at == _iso(new_now)
    assert extended.review_started_at is None


def test_promote_marks_demoted_locally() -> None:
    a = _make(datetime(2026, 4, 1, tzinfo=timezone.utc))
    promoted = promote(a)
    # Caller moves content to Priority 1 store; local record goes cold.
    assert promoted.status is FreshnessStatus.DEMOTED


def test_demote_marks_demoted() -> None:
    a = _make(datetime(2026, 4, 1, tzinfo=timezone.utc))
    demoted = demote(a)
    assert demoted.status is FreshnessStatus.DEMOTED


def test_record_use_increments_count() -> None:
    a = _make(datetime(2026, 4, 1, tzinfo=timezone.utc), uses=2)
    assert record_use(a).uses == 3
    # immutability
    assert a.uses == 2


def test_is_citable_as_load_bearing() -> None:
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    assert is_citable_as_load_bearing(_make(created, status=FreshnessStatus.FRESH))
    assert is_citable_as_load_bearing(
        _make(created, status=FreshnessStatus.NEEDS_REVIEW)
    )
    assert is_citable_as_load_bearing(_make(created, status=FreshnessStatus.GRACE))
    assert not is_citable_as_load_bearing(
        _make(created, status=FreshnessStatus.DEMOTED)
    )


def test_tick_is_pure() -> None:
    """tick() must not mutate its input — the original assumption's
    status and uses count are preserved after a tick that transitions."""
    created = datetime(2026, 4, 1, tzinfo=timezone.utc)
    a = _make(created, uses=USES_THRESHOLD)
    tick(a, now=created + timedelta(days=1))
    assert a.status is FreshnessStatus.FRESH
    assert a.uses == USES_THRESHOLD
    assert a.review_started_at is None
