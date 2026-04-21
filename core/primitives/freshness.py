"""
core/primitives/freshness.py — Assumption freshness clock (§7.4)
================================================================
Every assumption carries `created_at`, `uses` (citation count), and a
lifecycle status that progresses:

    fresh → needs_review → grace → demoted

Transitions (§7.4):

  * fresh       — `uses < 5 AND age < 14 days`
  * needs_review — either threshold crossed; surfaces to founder
  * grace       — 7 days after `needs_review` began; last chance
  * demoted     — automatically at grace end; decisions citing it go stale

Pure + deterministic. `tick()` is a function of
(assumption, now) → new assumption — no globals, no I/O. Persistence is
the caller's responsibility (the GUI dashboard at /c/<slug>/assumptions
in a later phase).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum

USES_THRESHOLD = 5
FRESH_DAYS = 14
GRACE_DAYS = 7


class FreshnessStatus(Enum):
    FRESH = "fresh"
    NEEDS_REVIEW = "needs_review"
    GRACE = "grace"
    DEMOTED = "demoted"


@dataclass(frozen=True)
class Assumption:
    """Envelope for a tracked assumption. Timestamps are ISO-8601 UTC.

    `review_started_at` is set the first time status flips to
    `needs_review`. It anchors the grace countdown; a fresh transition
    back to `needs_review` after a founder `extend` resets it.
    """

    id: str
    content: str
    created_at: str  # ISO-8601 UTC
    uses: int = 0
    status: FreshnessStatus = FreshnessStatus.FRESH
    review_started_at: str | None = None


def _parse(ts: str) -> datetime:
    # Tolerate "...Z" suffix via fromisoformat (Python 3.11+).
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def _needs_review(assumption: Assumption, now: datetime) -> bool:
    age = now - _parse(assumption.created_at)
    return assumption.uses >= USES_THRESHOLD or age >= timedelta(days=FRESH_DAYS)


def tick(assumption: Assumption, now: datetime | None = None) -> Assumption:
    """Advance the assumption's status based on the current clock.
    Returns a new Assumption; the input is never mutated.

    Lifecycle transitions:
      fresh        → needs_review when threshold crossed
      needs_review → grace         when `FRESH_DAYS + GRACE_DAYS`
                                   has elapsed since creation AND
                                   review_started_at is ≥ GRACE_DAYS ago
      grace        → demoted       when grace window elapsed
      demoted      → demoted       (terminal)
    """
    now = now or datetime.now(timezone.utc)

    # Terminal state: nothing to do.
    if assumption.status is FreshnessStatus.DEMOTED:
        return assumption

    # Fresh → needs_review
    if assumption.status is FreshnessStatus.FRESH:
        if _needs_review(assumption, now):
            return replace(
                assumption,
                status=FreshnessStatus.NEEDS_REVIEW,
                review_started_at=now.isoformat(),
            )
        return assumption

    # needs_review → grace (once GRACE_DAYS elapsed since review started)
    if assumption.status is FreshnessStatus.NEEDS_REVIEW:
        if assumption.review_started_at is None:
            # Corrupt state — stamp it now rather than crash.
            return replace(assumption, review_started_at=now.isoformat())
        since_review = now - _parse(assumption.review_started_at)
        if since_review >= timedelta(days=GRACE_DAYS):
            return replace(assumption, status=FreshnessStatus.GRACE)
        return assumption

    # grace → demoted (after another GRACE_DAYS from grace entry;
    # conservatively we demote once 2*GRACE_DAYS has passed since review start).
    if assumption.status is FreshnessStatus.GRACE:
        if assumption.review_started_at is None:
            return replace(assumption, status=FreshnessStatus.DEMOTED)
        since_review = now - _parse(assumption.review_started_at)
        if since_review >= timedelta(days=GRACE_DAYS * 2):
            return replace(assumption, status=FreshnessStatus.DEMOTED)
        return assumption

    return assumption


# ---------------------------------------------------------------------------
# Founder actions
# ---------------------------------------------------------------------------
def promote(assumption: Assumption) -> Assumption:
    """Founder action: promote an assumption to a settled conviction.
    The caller is responsible for moving it to the Priority 1 store;
    we just mark it `demoted` locally so no downstream code treats it
    as still live in the assumption log."""
    return replace(assumption, status=FreshnessStatus.DEMOTED)


def demote(assumption: Assumption) -> Assumption:
    """Founder action: mark provisional. Downstream load-bearing citations
    are no longer valid; decisions citing it become stale."""
    return replace(assumption, status=FreshnessStatus.DEMOTED)


def extend(assumption: Assumption, now: datetime | None = None) -> Assumption:
    """Founder action: grant another `FRESH_DAYS` by resetting `created_at`.
    Status returns to `fresh`, uses resets to 0."""
    now = now or datetime.now(timezone.utc)
    return replace(
        assumption,
        created_at=now.isoformat(),
        uses=0,
        status=FreshnessStatus.FRESH,
        review_started_at=None,
    )


def record_use(assumption: Assumption) -> Assumption:
    """Increment the usage count. Does NOT run the lifecycle; call
    `tick()` afterwards (or let the nightly sweep do it)."""
    return replace(assumption, uses=assumption.uses + 1)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
def is_citable_as_load_bearing(assumption: Assumption) -> bool:
    """Per §7.4: once an assumption is demoted, agents can no longer
    cite it as load-bearing. The watchdog uses this to reject citations
    referencing demoted assumptions."""
    return assumption.status is not FreshnessStatus.DEMOTED
