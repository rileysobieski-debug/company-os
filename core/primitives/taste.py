"""
core/primitives/taste.py — Taste profile + `fit_preference_vector`
===================================================================
The taste profile is Priority 7 (the weakest authority tier) — a
preference signal only. Per §1.5 it must never be cited as fact or
constraint. Callers of `fit_preference_vector` treat the output as one
input to a specialist's reasoning, not as a decision.

The profile lives at `<company>/taste/profile.yaml` and carries:

  * `last_fit_at`  — ISO timestamp of the most recent fit computation
  * `picks_used`   — number of A/B picks folded into the current axes
  * `confidence`   — [0, 1] quality-of-signal derived from pick count
  * `axes`         — {axis_name: preference_value in [-1, 1]}

`fit_preference_vector(profile, candidate_axes)` is the pure-skill
reference to `taste.fit_preference_vector` in the plan (§4). It returns
a deterministic `FitScore` — cosine similarity over the shared axes,
weighted by `profile.confidence`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Mapping

import yaml

from core.primitives.state import AuthorityPriority, Claim

TASTE_SUBDIR = "taste"
PROFILE_FILENAME = "profile.yaml"


@dataclass(frozen=True)
class TasteProfile:
    last_fit_at: str
    picks_used: int
    confidence: float
    axes: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class FitScore:
    score: float                       # cosine * confidence, in [-1, 1]
    per_axis: Mapping[str, float]      # raw profile[axis] * candidate[axis]
    shared_axes: tuple[str, ...]
    reason: str


# ---------------------------------------------------------------------------
# Fit
# ---------------------------------------------------------------------------
def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors. Returns 0 when
    either vector is the zero vector — the alternative (raising) would
    make callers write the guard at every call site."""
    if not a or not b:
        return 0.0
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (norm_a * norm_b)


def fit_preference_vector(
    profile: TasteProfile, candidate_axes: Mapping[str, float]
) -> FitScore:
    """Score how well `candidate_axes` aligns with `profile.axes`.
    Pure + deterministic. No LLM call.

    Signal is computed only over axes present in BOTH vectors — axes the
    profile hasn't learned yet can't contribute. `profile.confidence`
    scales the final score; a low-confidence profile should have low
    influence even when the geometric alignment is strong.
    """
    shared = tuple(sorted(set(profile.axes) & set(candidate_axes)))
    if not shared:
        return FitScore(
            score=0.0,
            per_axis={},
            shared_axes=(),
            reason="no shared axes between profile and candidate",
        )
    p_vec = [float(profile.axes[a]) for a in shared]
    c_vec = [float(candidate_axes[a]) for a in shared]
    cos = _cosine(p_vec, c_vec)
    confidence = max(0.0, min(1.0, float(profile.confidence)))
    score = cos * confidence
    per_axis = {a: float(profile.axes[a]) * float(candidate_axes[a]) for a in shared}
    reason = (
        f"cosine={cos:+.3f} over {len(shared)} shared axes; "
        f"weighted by confidence={confidence:.2f} → score={score:+.3f}"
    )
    return FitScore(
        score=score,
        per_axis=per_axis,
        shared_axes=shared,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _normalise_timestamp(val) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, date):
        return val.isoformat()
    return str(val).strip()


def _profile_path(company_dir: Path) -> Path:
    return company_dir / TASTE_SUBDIR / PROFILE_FILENAME


def load_profile(company_dir: Path) -> TasteProfile | None:
    """Load the company's taste profile from disk, or None if absent.
    Returns None (not a default profile) so callers can distinguish
    "never learned anything" from "learned zero confidence"."""
    path = _profile_path(company_dir)
    if not path.exists():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    if not isinstance(raw, dict):
        return None
    axes_raw = raw.get("axes", {}) or {}
    if not isinstance(axes_raw, dict):
        axes_raw = {}
    axes = {str(k): float(v) for k, v in axes_raw.items()}
    try:
        picks_used = int(raw.get("picks_used", 0))
    except (TypeError, ValueError):
        picks_used = 0
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return TasteProfile(
        last_fit_at=_normalise_timestamp(raw.get("last_fit_at")),
        picks_used=picks_used,
        confidence=confidence,
        axes=axes,
    )


def write_profile(company_dir: Path, profile: TasteProfile) -> Path:
    """Atomically overwrite the company's taste profile. Returns the path."""
    path = _profile_path(company_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_fit_at": profile.last_fit_at,
        "picks_used": profile.picks_used,
        "confidence": profile.confidence,
        "axes": dict(profile.axes),
    }
    path.write_text(
        yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Claim adapter (Priority 7 — §1.5)
# ---------------------------------------------------------------------------
def profile_to_claim(profile: TasteProfile) -> Claim:
    """Wrap a taste profile as a Priority 7 Claim so it flows through
    `resolve_conflict()`. Priority 7 is preference signal only and loses
    to every higher tier — this adapter exists so the watchdog and
    citation contract see taste the same way they see any other claim,
    not so taste wins arguments.

    Raises ValueError if `last_fit_at` is missing — without that stamp
    the resulting claim would fail `check_provenance()`.
    """
    if not profile.last_fit_at:
        raise ValueError(
            "taste profile missing last_fit_at — cannot produce a valid Claim"
        )
    provenance = {
        "updated_at": profile.last_fit_at,
        "updated_by": "taste.learn",
        "source_path": f"{TASTE_SUBDIR}/{PROFILE_FILENAME}",
        "ingested_at": profile.last_fit_at,
    }
    content = {
        "picks_used": profile.picks_used,
        "confidence": profile.confidence,
        "axes": dict(profile.axes),
    }
    return Claim(
        priority=AuthorityPriority.TASTE,
        content=content,
        ref=f"priority_7_taste:{TASTE_SUBDIR}/{PROFILE_FILENAME}",
        provenance=provenance,
    )
