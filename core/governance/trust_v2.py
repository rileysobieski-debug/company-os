"""Confidence-adjusted trust scoring (v6 Weeks 4-5 deliverable 4).

Sits alongside the Phase 1 `core.governance.trust` module rather than
replacing it; the plan marks Phase 1 trust as frozen during the
Weeks 2-3 Walls sprint. When callers migrate, they flip imports from
`trust` to `trust_v2` and gain two properties that close the reviewer-
flagged dilution and stealth-agent gaps.

Design:

    1. Scores expose (lower_bound, point, upper_bound, confidence,
       sample_count). Decision-making uses the LOWER bound, not the
       point estimate. A new agent with one critical failure has a
       low lower bound despite a middling point estimate; the gate
       that consumes this value sees the risk.

    2. Explicit agent enumeration via `enumerate_agents(vault_dir)`.
       The Phase 1 trust engine only knew about agents that already
       had at least one rating. The v2 enumerator walks the
       department roster files directly and returns every agent known
       to the company, rated or not. Unrated agents surface as
       `confidence=0.0` and are distinguishable from high-trust
       agents that happen to have zero negative ratings.

    3. Wilson-score lower bound over the empirical mean. Maps the
       (-1, +1) rating to a (0, 1) positive-outcome rate, computes a
       95% Wilson lower bound on the proportion, maps back. This is
       the standard defensive statistic for small-sample binary
       reputation scoring (Reddit, Stack Overflow used variants of
       this for years).

The Phase 1 half-life weighting is preserved; the v2 layer adds the
confidence interval on top. Tests exercise both the dilution case
(one -2 rating pins lower_bound low) and the enumeration case
(unrated agents surface with confidence=0).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


MAX_RATING: int = 2
MIN_RATING: int = -2
DEFAULT_HALF_LIFE_DAYS: float = 30.0
WILSON_Z: float = 1.96  # 95% confidence


@dataclass(frozen=True)
class TrustScoreV2:
    """Confidence-adjusted trust score with explicit uncertainty.

    The `point` estimate is the weighted mean rating; the `lower_bound`
    is the defensive value gates should consult. `confidence` is the
    sample-count-derived confidence on [0.0, 1.0]; 0 means no ratings
    at all (stealth-agent case) and grows asymptotically toward 1 as
    samples accumulate."""
    agent: str
    point: float
    lower_bound: float
    upper_bound: float
    confidence: float
    sample_count: int
    last_rated_at: str  # ISO-8601 UTC, empty if never rated


@dataclass(frozen=True)
class _WeightedSample:
    rating: int      # clamped to [MIN_RATING, MAX_RATING]
    weight: float    # positive; 0.0 denotes unweighted zero
    rated_at: datetime


def _half_life_weight(sample_at: datetime, now: datetime, half_life_days: float) -> float:
    age_days = (now - sample_at).total_seconds() / 86400.0
    if age_days < 0:
        age_days = 0.0
    # Avoid overflow for absurdly old samples.
    exponent = -age_days / max(half_life_days, 1e-9)
    return math.exp(exponent * math.log(2))


def _rating_to_positive_rate(rating: int) -> float:
    """Map a clamped (-2, +2) rating into a (0, 1) positive-outcome
    rate. A -2 is 0, +2 is 1, 0 is 0.5; linear in between. This is
    the mapping Wilson-score expects."""
    clamped = max(MIN_RATING, min(MAX_RATING, rating))
    return (clamped - MIN_RATING) / (MAX_RATING - MIN_RATING)


def _positive_rate_to_rating(rate: float) -> float:
    """Inverse mapping. Returns a float in [-2.0, 2.0]."""
    clamped = max(0.0, min(1.0, rate))
    return MIN_RATING + clamped * (MAX_RATING - MIN_RATING)


def _wilson_interval(positive_rate: float, effective_n: float) -> tuple[float, float]:
    """Wilson-score 95% confidence interval for a proportion.

    `effective_n` is the sum of sample weights rather than a raw
    count; the half-life weighting treats old ratings as partially
    discounted samples, so the Wilson formula consumes the weighted
    total for its denominator. When effective_n <= 0 the interval is
    the full (0, 1) range."""
    if effective_n <= 0:
        return (0.0, 1.0)
    z = WILSON_Z
    z2 = z * z
    p = max(0.0, min(1.0, positive_rate))
    denominator = 1.0 + z2 / effective_n
    centre = (p + z2 / (2.0 * effective_n)) / denominator
    margin = (z / denominator) * math.sqrt(
        (p * (1.0 - p) / effective_n) + (z2 / (4.0 * effective_n * effective_n)),
    )
    low = max(0.0, centre - margin)
    high = min(1.0, centre + margin)
    return low, high


def _confidence_from_effective_n(effective_n: float) -> float:
    """Sample-count-derived confidence on [0, 1]. Zero samples yields
    0.0 (no information). Grows asymptotically toward 1.0; 20 full-
    weight samples give ~0.95. The curve is intentionally aggressive:
    callers should not treat a brand-new agent as high-trust merely
    because no one has rated them badly yet."""
    if effective_n <= 0:
        return 0.0
    return 1.0 - math.exp(-effective_n / 5.0)


def compute_score(
    agent: str,
    ratings: Iterable[tuple[int, datetime]],
    *,
    now: datetime | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> TrustScoreV2:
    """Confidence-adjusted trust for one agent.

    `ratings` is an iterable of (rating, rated_at_datetime) tuples.
    An empty iterable produces a zero-confidence score suitable for
    surfacing a stealth agent to the Tension HUD."""
    resolved_now = now or datetime.now(timezone.utc)
    samples: list[_WeightedSample] = []
    for raw_rating, rated_at in ratings:
        if rated_at.tzinfo is None:
            rated_at = rated_at.replace(tzinfo=timezone.utc)
        clamped = max(MIN_RATING, min(MAX_RATING, int(raw_rating)))
        w = _half_life_weight(rated_at, resolved_now, half_life_days)
        samples.append(_WeightedSample(rating=clamped, weight=w, rated_at=rated_at))

    effective_n = sum(s.weight for s in samples)
    if effective_n <= 0 or not samples:
        return TrustScoreV2(
            agent=agent,
            point=0.0,
            lower_bound=-2.0,  # most conservative
            upper_bound=2.0,
            confidence=0.0,
            sample_count=len(samples),
            last_rated_at="",
        )

    weighted_sum = sum(s.rating * s.weight for s in samples)
    point = weighted_sum / effective_n

    # Convert weighted mean rating to positive-outcome rate, run Wilson,
    # then convert the interval bounds back to rating-space.
    point_rate = _rating_to_positive_rate(round(point)) if False else (
        (point - MIN_RATING) / (MAX_RATING - MIN_RATING)
    )
    low_rate, high_rate = _wilson_interval(point_rate, effective_n)
    lower_bound = _positive_rate_to_rating(low_rate)
    upper_bound = _positive_rate_to_rating(high_rate)

    last_rated_at = max(s.rated_at for s in samples).isoformat()
    confidence = _confidence_from_effective_n(effective_n)

    return TrustScoreV2(
        agent=agent,
        point=point,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        confidence=confidence,
        sample_count=len(samples),
        last_rated_at=last_rated_at,
    )


# ---------------------------------------------------------------------------
# Stealth-agent enumeration
# ---------------------------------------------------------------------------
def enumerate_agents(vault_dir: Path, company_slug: str | None = None) -> list[str]:
    """Walk roster files and return every agent known to the company,
    whether or not it has any ratings. Closes the Gemini `stealth
    agent` finding: the trust engine can no longer silently miss an
    agent that performed work but was never rated.

    Lookup rules:

        - Company root is `vault_dir / company_slug` when `company_slug`
          is provided; otherwise walk every direct subdir of
          `vault_dir` that contains a `config.json`.
        - Every `<company>/<dept>/manager-memory.md` implies an agent
          `manager:<dept>`.
        - Every `<company>/<dept>/<specialist>/<specialist>.md` implies
          an agent `specialist:<dept>.<specialist>`.
        - Returns a stable-sorted list of unique agent ids.

    Returns an empty list if the vault dir does not exist or the
    company dir has no department subdirs.
    """
    root = Path(vault_dir)
    if not root.exists():
        return []
    companies: list[Path] = []
    if company_slug:
        candidate = root / company_slug
        if candidate.is_dir():
            companies.append(candidate)
    else:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if (child / "config.json").exists():
                companies.append(child)

    agents: set[str] = set()
    for company in companies:
        for dept_dir in company.iterdir():
            if not dept_dir.is_dir():
                continue
            dept = dept_dir.name
            if dept.startswith(".") or dept in {"board", "decisions", "sessions", "governance", "demo-artifacts", "knowledge-base", "taste", "scenarios", "brand"}:
                continue
            manager_file = dept_dir / "manager-memory.md"
            if manager_file.exists():
                agents.add(f"manager:{dept}")
            for specialist_dir in dept_dir.iterdir():
                if not specialist_dir.is_dir():
                    continue
                specialist_name = specialist_dir.name
                if specialist_name.startswith("."):
                    continue
                agents.add(f"specialist:{dept}.{specialist_name}")
    return sorted(agents)


def is_stealth(score: TrustScoreV2) -> bool:
    """True when the agent exists in the roster but has zero ratings.
    The dispatcher should treat these differently from high-trust
    agents; the Tension HUD should surface them."""
    return score.confidence == 0.0 and score.sample_count == 0


__all__ = [
    "DEFAULT_HALF_LIFE_DAYS",
    "MAX_RATING",
    "MIN_RATING",
    "TrustScoreV2",
    "compute_score",
    "enumerate_agents",
    "is_stealth",
]
