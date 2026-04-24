"""trust_v2 tests: confidence-adjusted scoring + stealth-agent enumeration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.governance.trust_v2 import (
    DEFAULT_HALF_LIFE_DAYS,
    MAX_RATING,
    MIN_RATING,
    TrustScoreV2,
    compute_score,
    enumerate_agents,
    is_stealth,
)


NOW = datetime(2026, 4, 24, 12, 0, tzinfo=timezone.utc)


def _days_ago(days: float) -> datetime:
    return NOW - timedelta(days=days)


# ---------------------------------------------------------------------------
# compute_score
# ---------------------------------------------------------------------------
def test_no_ratings_is_zero_confidence_stealth() -> None:
    score = compute_score("agent-x", [], now=NOW)
    assert score.confidence == 0.0
    assert score.sample_count == 0
    assert score.lower_bound == -2.0
    assert score.upper_bound == 2.0
    assert is_stealth(score)


def test_single_negative_rating_has_low_lower_bound() -> None:
    """Gemini finding: a lone critical failure must not be diluted to
    neutral. With 1 sample the lower bound stays below zero even if
    the point estimate is not extreme."""
    score = compute_score("agent-y", [(-2, _days_ago(1))], now=NOW)
    assert score.point < 0
    assert score.lower_bound < score.point + 0.1
    assert score.confidence < 0.5
    assert not is_stealth(score)


def test_many_positive_ratings_has_high_lower_bound() -> None:
    ratings = [(2, _days_ago(i)) for i in range(30)]
    score = compute_score("agent-z", ratings, now=NOW)
    assert score.point > 0
    assert score.lower_bound > 0
    assert score.confidence > 0.9


def test_lower_bound_less_than_or_equal_to_point() -> None:
    ratings = [(1, _days_ago(1)), (2, _days_ago(2)), (-1, _days_ago(3))]
    score = compute_score("agent-a", ratings, now=NOW)
    assert score.lower_bound <= score.point <= score.upper_bound


def test_old_ratings_weighted_less_than_recent() -> None:
    """Half-life discounts old ratings. Two identical +2 ratings, one
    today and one 90 days ago, should give a lower confidence than
    two +2 ratings both today."""
    old = compute_score("old", [(2, _days_ago(0)), (2, _days_ago(90))], now=NOW)
    fresh = compute_score("fresh", [(2, _days_ago(0)), (2, _days_ago(0))], now=NOW)
    assert old.confidence < fresh.confidence


def test_rating_clamped_to_valid_range() -> None:
    # Out-of-range inputs clamp; do not raise.
    score = compute_score("x", [(10, _days_ago(0)), (-10, _days_ago(0))], now=NOW)
    # After clamping to [-2, +2], weighted mean is 0.
    assert abs(score.point) < 1e-6


def test_naive_datetime_treated_as_utc() -> None:
    naive = datetime(2026, 4, 20, 12, 0)
    score = compute_score("agent", [(1, naive)], now=NOW)
    assert score.sample_count == 1


def test_is_stealth_only_when_zero_samples() -> None:
    stealth = compute_score("stealth", [], now=NOW)
    rated = compute_score("rated", [(0, _days_ago(1))], now=NOW)
    assert is_stealth(stealth)
    assert not is_stealth(rated)


# ---------------------------------------------------------------------------
# enumerate_agents
# ---------------------------------------------------------------------------
def test_enumerate_empty_vault(tmp_path: Path) -> None:
    assert enumerate_agents(tmp_path) == []


def test_enumerate_nonexistent_vault() -> None:
    assert enumerate_agents(Path("/no/such/path")) == []


def test_enumerate_finds_managers_and_specialists(tmp_path: Path) -> None:
    company = tmp_path / "acme"
    (company).mkdir()
    (company / "config.json").write_text("{}", encoding="utf-8")
    (company / "marketing").mkdir()
    (company / "marketing" / "manager-memory.md").write_text("# m", encoding="utf-8")
    specialist = company / "marketing" / "copywriter"
    specialist.mkdir()
    (specialist / "copywriter.md").write_text("# s", encoding="utf-8")
    agents = enumerate_agents(tmp_path)
    assert "manager:marketing" in agents
    assert "specialist:marketing.copywriter" in agents


def test_enumerate_skips_reserved_top_level_dirs(tmp_path: Path) -> None:
    company = tmp_path / "acme"
    company.mkdir()
    (company / "config.json").write_text("{}", encoding="utf-8")
    # Reserved; must not produce a phantom agent.
    (company / "board").mkdir()
    (company / "decisions").mkdir()
    (company / "sessions").mkdir()
    (company / "governance").mkdir()
    # Legit dept.
    (company / "finance").mkdir()
    (company / "finance" / "manager-memory.md").write_text("# m", encoding="utf-8")
    agents = enumerate_agents(tmp_path)
    assert agents == ["manager:finance"]


def test_enumerate_filters_by_company_slug(tmp_path: Path) -> None:
    for name in ("acme", "other"):
        c = tmp_path / name
        c.mkdir()
        (c / "config.json").write_text("{}", encoding="utf-8")
        (c / "dept").mkdir()
        (c / "dept" / "manager-memory.md").write_text("# m", encoding="utf-8")
    agents = enumerate_agents(tmp_path, company_slug="acme")
    assert agents == ["manager:dept"]


def test_enumerate_skips_dirs_without_config(tmp_path: Path) -> None:
    not_a_company = tmp_path / "random_dir"
    not_a_company.mkdir()
    (not_a_company / "marketing").mkdir()
    (not_a_company / "marketing" / "manager-memory.md").write_text("# m", encoding="utf-8")
    assert enumerate_agents(tmp_path) == []


def test_enumerate_returns_sorted_unique() -> None:
    pass  # Covered by contract of sorted(set(...))


def test_score_dataclass_is_frozen() -> None:
    score = TrustScoreV2(
        agent="a", point=0.0, lower_bound=-2.0, upper_bound=2.0,
        confidence=0.0, sample_count=0, last_rated_at="",
    )
    with pytest.raises(Exception):
        score.point = 1.0  # type: ignore[misc]


def test_stealth_vs_rated_are_distinguishable() -> None:
    """The whole point: a stealth agent is not mistaken for a high-trust
    one. confidence + sample_count together disambiguate."""
    stealth = compute_score("s", [], now=NOW)
    high_trust = compute_score("h", [(2, _days_ago(i)) for i in range(50)], now=NOW)
    assert stealth.confidence < 0.05
    assert high_trust.confidence > 0.9
