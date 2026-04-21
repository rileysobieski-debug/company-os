"""
core/primitives/ab.py — Taste Inbox / A/B preference learning (§6, §4)
======================================================================
Pure math behind the Taste Inbox described in plan §13 Phase 6. An
``ABPair`` is a question ("which of these two do you prefer?"); an
``ABPick`` is the founder's answer plus the axis-tagged descriptors of
each option. ``update_profile_from_picks`` folds a batch of picks into
the running ``TasteProfile`` (§6.3); ``discover_axis`` runs the
"first-principles discovery" step — it finds, from scratch, which axis
is doing the predictive work, without any priors or taxonomy.

Axis tagging is an INPUT to this module, not an output. The plan routes
axis-tagging itself through a Haiku call (§9, "Taste discovery axis
scoring"). Keeping the math pure lets the axis-tagger swap between
Haiku, a deterministic heuristic, or any other labelling source without
changing the learning loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Mapping

from core.primitives.taste import TasteProfile

CONFIDENCE_PICKS_FOR_FULL = 20
AXIS_DISCOVERY_MIN_PICKS = 3
AXIS_DISCOVERY_MIN_MAGNITUDE = 0.15


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ABOption:
    id: str
    axes: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ABPair:
    id: str
    a: ABOption
    b: ABOption
    shown_at: str


@dataclass(frozen=True)
class ABPick:
    pair: ABPair
    chosen: str        # "a" or "b"
    picked_at: str

    def __post_init__(self) -> None:  # pragma: no cover - trivial guard
        if self.chosen not in ("a", "b"):
            raise ValueError(f"ABPick.chosen must be 'a' or 'b', got {self.chosen!r}")


@dataclass(frozen=True)
class AxisHypothesis:
    axis: str
    magnitude: float          # [-1, 1] — sign encodes polarity
    picks_supporting: int
    reason: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _chosen_option(pick: ABPick) -> ABOption:
    return pick.pair.a if pick.chosen == "a" else pick.pair.b


def _rejected_option(pick: ABPick) -> ABOption:
    return pick.pair.b if pick.chosen == "a" else pick.pair.a


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _now_iso(now: str | None) -> str:
    return now if now is not None else datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Profile update
# ---------------------------------------------------------------------------
def _per_pick_delta(pick: ABPick) -> dict[str, float]:
    """Per-axis signed preference delta for one pick: (chosen - rejected).
    Axes present in only one option contribute the raw value from the side
    that has them (i.e. rejected-side axis implicit-zero on the chosen side)."""
    chosen = _chosen_option(pick).axes
    rejected = _rejected_option(pick).axes
    axes = set(chosen) | set(rejected)
    return {
        a: _clamp(float(chosen.get(a, 0.0)) - float(rejected.get(a, 0.0)))
        for a in axes
    }


def _batch_average_delta(picks: list[ABPick]) -> dict[str, float]:
    """Simple average of per-pick deltas across the batch. Axes only
    appearing in a subset of picks are averaged over the subset they
    appear in (not over the batch total) — axes with no evidence in a
    pick shouldn't drag the signal toward zero."""
    counts: dict[str, int] = {}
    totals: dict[str, float] = {}
    for pick in picks:
        for axis, delta in _per_pick_delta(pick).items():
            counts[axis] = counts.get(axis, 0) + 1
            totals[axis] = totals.get(axis, 0.0) + delta
    return {a: totals[a] / counts[a] for a in totals}


def update_profile_from_picks(
    profile: TasteProfile,
    picks: Iterable[ABPick],
    now: str | None = None,
) -> TasteProfile:
    """Fold `picks` into `profile`, returning a new TasteProfile. Pure.

    Math (per axis):
        prior_weight = profile.picks_used / (profile.picks_used + n)
        batch_weight =              n     / (profile.picks_used + n)
        new_value    = prior * prior_weight + batch_delta * batch_weight

    Axes absent from the prior profile are seeded with just the batch
    delta. Axes in the prior but untouched by the batch are preserved.

    Confidence is `min(1.0, new_picks_used / CONFIDENCE_PICKS_FOR_FULL)`
    so a small batch against an empty profile doesn't claim confidence
    it hasn't earned.
    """
    picks_list = list(picks)
    n_new = len(picks_list)
    if n_new == 0:
        return profile

    batch = _batch_average_delta(picks_list)
    prior_n = max(0, profile.picks_used)
    total_n = prior_n + n_new

    prior_weight = prior_n / total_n if total_n else 0.0
    batch_weight = n_new / total_n if total_n else 1.0

    new_axes: dict[str, float] = {}
    # Preserve prior axes (weighted down when a batch has something to say).
    for axis, prior_val in profile.axes.items():
        delta = batch.get(axis, 0.0)
        # If the batch doesn't mention this axis, keep the prior untouched.
        if axis not in batch:
            new_axes[axis] = float(prior_val)
        else:
            new_axes[axis] = _clamp(
                float(prior_val) * prior_weight + delta * batch_weight
            )
    # Axes introduced by the batch alone.
    for axis, delta in batch.items():
        if axis in new_axes:
            continue
        # With no prior, the new value is the batch delta itself,
        # but scaled by batch_weight (which equals 1 when prior_n == 0).
        new_axes[axis] = _clamp(delta * batch_weight)

    confidence = min(1.0, total_n / CONFIDENCE_PICKS_FOR_FULL)

    return TasteProfile(
        last_fit_at=_now_iso(now),
        picks_used=total_n,
        confidence=confidence,
        axes=new_axes,
    )


# ---------------------------------------------------------------------------
# First-principles axis discovery
# ---------------------------------------------------------------------------
def discover_axis(picks: Iterable[ABPick]) -> AxisHypothesis | None:
    """Given a batch of picks, identify the single axis with the
    strongest signed correlation with the founder's choice. Returns
    None if the batch is too small or no axis shows meaningful signal.

    "First-principles" means the primitive doesn't start from a fixed
    set of axes — it takes whatever axes appear in the option tags and
    ranks them by evidence.

    Tiebreak on equal absolute magnitude: alphabetical axis name.
    """
    picks_list = list(picks)
    if len(picks_list) < AXIS_DISCOVERY_MIN_PICKS:
        return None

    per_axis_sum: dict[str, float] = {}
    per_axis_count: dict[str, int] = {}
    for pick in picks_list:
        for axis, delta in _per_pick_delta(pick).items():
            per_axis_sum[axis] = per_axis_sum.get(axis, 0.0) + delta
            per_axis_count[axis] = per_axis_count.get(axis, 0) + 1

    if not per_axis_sum:
        return None

    def _rank(axis: str) -> tuple[float, str]:
        mean = per_axis_sum[axis] / per_axis_count[axis]
        return (-abs(mean), axis)

    best = sorted(per_axis_sum, key=_rank)[0]
    best_mean = per_axis_sum[best] / per_axis_count[best]
    if abs(best_mean) < AXIS_DISCOVERY_MIN_MAGNITUDE:
        return None

    polarity = "positive" if best_mean > 0 else "negative"
    return AxisHypothesis(
        axis=best,
        magnitude=_clamp(best_mean),
        picks_supporting=per_axis_count[best],
        reason=(
            f"axis {best!r} had {polarity} mean delta {best_mean:+.3f} "
            f"across {per_axis_count[best]} picks"
        ),
    )
