"""Taste profile + fit_preference_vector primitive (Phase 6.3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.primitives.state import (
    AuthorityPriority,
    ProvenanceStatus,
    check_provenance,
)
from core.primitives.taste import (
    FitScore,
    TasteProfile,
    fit_preference_vector,
    load_profile,
    profile_to_claim,
    write_profile,
)

ISO = "2026-04-17T10:00:00+00:00"


def _profile(**overrides) -> TasteProfile:
    defaults = dict(
        last_fit_at=ISO,
        picks_used=10,
        confidence=0.8,
        axes={"coastal": 0.8, "corporate": -0.9, "spare": 0.6},
    )
    defaults.update(overrides)
    return TasteProfile(**defaults)


# ---------------------------------------------------------------------------
# fit_preference_vector
# ---------------------------------------------------------------------------
def test_no_shared_axes_returns_zero() -> None:
    profile = _profile()
    candidate = {"novelty": 0.5}
    fit = fit_preference_vector(profile, candidate)
    assert fit.score == 0.0
    assert fit.shared_axes == ()
    assert "no shared axes" in fit.reason


def test_perfect_alignment_scaled_by_confidence() -> None:
    profile = _profile(
        confidence=1.0, axes={"coastal": 1.0, "spare": 1.0}
    )
    candidate = {"coastal": 1.0, "spare": 1.0}
    fit = fit_preference_vector(profile, candidate)
    assert fit.score == pytest.approx(1.0)
    assert set(fit.shared_axes) == {"coastal", "spare"}


def test_perfect_anti_alignment_returns_minus_one() -> None:
    profile = _profile(confidence=1.0, axes={"coastal": 1.0, "spare": 1.0})
    candidate = {"coastal": -1.0, "spare": -1.0}
    fit = fit_preference_vector(profile, candidate)
    assert fit.score == pytest.approx(-1.0)


def test_confidence_scales_score() -> None:
    profile_hi = _profile(confidence=1.0)
    profile_lo = _profile(confidence=0.2)
    candidate = {"coastal": 0.9, "corporate": -0.8, "spare": 0.7}
    hi = fit_preference_vector(profile_hi, candidate).score
    lo = fit_preference_vector(profile_lo, candidate).score
    assert hi > lo > 0
    assert lo == pytest.approx(hi * 0.2, rel=1e-6)


def test_confidence_clamped_to_unit_interval() -> None:
    # Pathological profiles that snuck in a >1 or <0 confidence should be
    # clamped, not multiplied through raw.
    profile = _profile(confidence=5.0, axes={"coastal": 1.0})
    candidate = {"coastal": 1.0}
    assert fit_preference_vector(profile, candidate).score == pytest.approx(1.0)

    profile = _profile(confidence=-1.0, axes={"coastal": 1.0})
    assert fit_preference_vector(profile, candidate).score == 0.0


def test_per_axis_contribution_reported() -> None:
    profile = _profile(
        confidence=1.0,
        axes={"coastal": 0.8, "corporate": -0.9, "spare": 0.6},
    )
    candidate = {"coastal": 0.5, "corporate": 0.4}
    fit = fit_preference_vector(profile, candidate)
    assert fit.per_axis["coastal"] == pytest.approx(0.4)
    assert fit.per_axis["corporate"] == pytest.approx(-0.36)
    assert "spare" not in fit.per_axis


def test_fit_is_deterministic() -> None:
    profile = _profile()
    candidate = {"coastal": 0.5, "spare": 0.3}
    a = fit_preference_vector(profile, candidate)
    b = fit_preference_vector(profile, candidate)
    assert a == b


def test_fit_score_is_frozen() -> None:
    fs = FitScore(score=0.0, per_axis={}, shared_axes=(), reason="")
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        fs.score = 0.5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def test_load_profile_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_profile(tmp_path) is None


def test_write_then_load_roundtrip(tmp_path: Path) -> None:
    profile = _profile()
    path = write_profile(tmp_path, profile)
    assert path.exists()
    loaded = load_profile(tmp_path)
    assert loaded is not None
    assert loaded.picks_used == profile.picks_used
    assert loaded.confidence == pytest.approx(profile.confidence)
    assert loaded.axes == profile.axes
    assert loaded.last_fit_at == profile.last_fit_at


def test_load_profile_tolerates_malformed_yaml(tmp_path: Path) -> None:
    path = tmp_path / "taste" / "profile.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("::: not yaml :::\n  key: [unclosed", encoding="utf-8")
    assert load_profile(tmp_path) is None


def test_load_profile_defaults_bad_numeric_fields(tmp_path: Path) -> None:
    path = tmp_path / "taste" / "profile.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "last_fit_at: 2026-04-17T10:00:00+00:00\n"
        "picks_used: not-a-number\n"
        "confidence: also-bad\n"
        "axes:\n  coastal: 0.5\n",
        encoding="utf-8",
    )
    profile = load_profile(tmp_path)
    assert profile is not None
    assert profile.picks_used == 0
    assert profile.confidence == 0.0
    assert profile.axes["coastal"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Claim adapter
# ---------------------------------------------------------------------------
def test_profile_to_claim_produces_priority_7() -> None:
    claim = profile_to_claim(_profile())
    assert claim.priority is AuthorityPriority.TASTE
    assert claim.priority.value == 7
    assert claim.ref.startswith("priority_7_taste:")
    assert check_provenance(claim.provenance) is ProvenanceStatus.VALID


def test_profile_to_claim_rejects_missing_last_fit_at() -> None:
    bad = TasteProfile(last_fit_at="", picks_used=0, confidence=0.0)
    with pytest.raises(ValueError, match="last_fit_at"):
        profile_to_claim(bad)


def test_taste_claim_loses_to_every_higher_priority() -> None:
    """Priority 7 is the weakest non-assumption tier — it must lose to
    FOUNDER/DECISION/KB/BRAND/HANDSHAKE/MEMORY."""
    from core.primitives.state import Claim, resolve_conflict

    taste = profile_to_claim(_profile())
    prov = {
        "updated_at": ISO,
        "updated_by": "x",
        "source_path": "s",
        "ingested_at": ISO,
    }
    for pri in (
        AuthorityPriority.FOUNDER,
        AuthorityPriority.DECISION,
        AuthorityPriority.KB,
        AuthorityPriority.BRAND,
        AuthorityPriority.HANDSHAKE,
        AuthorityPriority.MEMORY,
    ):
        higher = Claim(priority=pri, content="x", ref=f"ref-{pri.value}", provenance=prov)
        resolved = resolve_conflict(taste, higher)
        assert resolved.winner is higher, f"taste should lose to {pri.name}"


def test_taste_claim_wins_over_assumption() -> None:
    from core.primitives.state import Claim, resolve_conflict

    taste = profile_to_claim(_profile())
    prov = {
        "updated_at": ISO,
        "updated_by": "x",
        "source_path": "s",
        "ingested_at": ISO,
    }
    assumption = Claim(
        priority=AuthorityPriority.ASSUMPTION,
        content="x", ref="a", provenance=prov,
    )
    resolved = resolve_conflict(taste, assumption)
    assert resolved.winner is taste
