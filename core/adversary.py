"""
core/adversary.py — Phase 12 — §0.5 Adversary + kill-switch primitives
=======================================================================
Plan §0.5 ships the explicit adversarial loop — a Path C agent whose
only job is to stress-test the founder's direction. This module is the
data layer: review records, retro records, and the drift-quality
benchmark used to detect adversary decay.

The actual adversary agent prompt (LLM seat, Haiku-first per §9) lives
downstream; this module focuses on the structured artifacts the
agent produces and consumes.

Artifacts on disk:
  * `<company>/decisions/adversary-reviews/<date>-<milestone-slug>.md`
  * `<company>/decisions/retros/<date>-<specialist>.md`
  * `<company>/decisions/adversary-ratings.jsonl`  (benchmark log)

Public surface (Chunk 12.1):
  * `ActivationReason` enum (milestone, manual, pattern)
  * `AdversaryReview(milestone, thesis, activation_reason, created_at,
                     founder_override, objections, premortem_quote,
                     citations)`
  * `render_review(review) → markdown`
  * `write_review(company_dir, review) → Path`
  * `load_review(path) → AdversaryReview`
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from statistics import median, pstdev
from typing import Iterable, Sequence


_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")
ADVERSARY_REVIEWS_SUBDIR = "decisions/adversary-reviews"
RETROS_SUBDIR = "decisions/retros"
RATINGS_FILENAME = "decisions/adversary-ratings.jsonl"


def _slug(text: str) -> str:
    s = _SLUG_RE.sub("-", text).strip("-").lower()
    return s or "anon"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Activation reason
# ---------------------------------------------------------------------------
class ActivationReason(str, Enum):
    """Why the adversary convened (§0.5 triggers)."""

    MILESTONE = "milestone"      # founder-declared checkpoint
    MANUAL = "manual"            # `companyos adversary --on "<thesis>"`
    PATTERN = "pattern"          # 3+ deliverables in 30d on same assumption


# ---------------------------------------------------------------------------
# Adversary review
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AdversaryReview:
    milestone: str                 # e.g. "commit-inaugural-varietal"
    thesis: str                    # the founder claim being stress-tested
    activation_reason: ActivationReason
    created_at: str                # ISO-8601 UTC
    objections: tuple[str, ...] = ()
    premortem_quote: str = ""      # injected per §0.5 pre-mortem rule
    citations: tuple[str, ...] = ()
    founder_override: str = ""     # recorded only after founder overrides
    notes: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["activation_reason"] = self.activation_reason.value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "AdversaryReview":
        return cls(
            milestone=str(data["milestone"]),
            thesis=str(data["thesis"]),
            activation_reason=ActivationReason(str(data["activation_reason"])),
            created_at=str(data["created_at"]),
            objections=tuple(data.get("objections", ())),
            premortem_quote=str(data.get("premortem_quote", "")),
            citations=tuple(data.get("citations", ())),
            founder_override=str(data.get("founder_override", "")),
            notes=str(data.get("notes", "")),
        )


def render_review(review: AdversaryReview) -> str:
    lines = [
        f"# Adversary review — {review.milestone}",
        "",
        f"**Activation:** {review.activation_reason.value}",
        f"**Created:** {review.created_at}",
        "",
        "## Thesis being stress-tested",
        "",
        f"> {review.thesis.strip()}",
        "",
    ]
    if review.premortem_quote.strip():
        lines.extend([
            "## Pre-mortem context (§0.5 load-bearing)",
            "",
            f"> {review.premortem_quote.strip()}",
            "",
        ])
    if review.objections:
        lines.append("## Objections")
        lines.append("")
        for i, obj in enumerate(review.objections, start=1):
            lines.append(f"{i}. {obj.strip()}")
        lines.append("")
    else:
        lines.extend(["## Objections", "", "_(no objections recorded)_", ""])
    if review.citations:
        lines.append("## Citations")
        lines.append("")
        lines.extend(f"- {c}" for c in review.citations)
        lines.append("")
    if review.founder_override.strip():
        lines.extend([
            "## Founder override",
            "",
            "_(Logged per §0.5: override carries adversary objection attached.)_",
            "",
            review.founder_override.strip(),
            "",
        ])
    if review.notes.strip():
        lines.extend(["## Notes", "", review.notes.strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def review_path(company_dir: Path, review: AdversaryReview) -> Path:
    date = review.created_at[:10]  # YYYY-MM-DD
    filename = f"{date}-{_slug(review.milestone)}.md"
    return company_dir / ADVERSARY_REVIEWS_SUBDIR / filename


def write_review(company_dir: Path, review: AdversaryReview) -> Path:
    path = review_path(company_dir, review)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_review(review), encoding="utf-8")
    # Also store a JSON sidecar so loaders don't need to reparse markdown.
    sidecar = path.with_suffix(".json")
    sidecar.write_text(
        json.dumps(review.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def load_review(path: Path) -> AdversaryReview:
    """Load an AdversaryReview from its `.json` sidecar. If `path`
    ends in `.md`, the sidecar `.json` is read instead."""
    if path.suffix == ".md":
        path = path.with_suffix(".json")
    return AdversaryReview.from_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )


def iter_reviews(company_dir: Path) -> list[AdversaryReview]:
    root = company_dir / ADVERSARY_REVIEWS_SUBDIR
    if not root.exists():
        return []
    out: list[AdversaryReview] = []
    for p in sorted(root.glob("*.json")):
        try:
            out.append(load_review(p))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return out


# ---------------------------------------------------------------------------
# Kill-switch retro (Chunk 12.2)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KillSwitchRetro:
    """Three-question retro recorded when the founder invokes /kill on a
    specialist (§0.5 Component 3). The retro is later surfaced as an
    input signal in the next adversary activation.

    Fields map to the three forced questions:
      * `expected` — "what did you expect?"
      * `saw`      — "what did you see?"
      * `fix`      — "what would fix it?"
    """

    specialist_id: str
    created_at: str  # ISO-8601 UTC
    expected: str
    saw: str
    fix: str
    last_known_good_prompt_ref: str = ""  # path/rev of prompt body that's being restored
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "KillSwitchRetro":
        return cls(
            specialist_id=str(data["specialist_id"]),
            created_at=str(data["created_at"]),
            expected=str(data.get("expected", "")),
            saw=str(data.get("saw", "")),
            fix=str(data.get("fix", "")),
            last_known_good_prompt_ref=str(
                data.get("last_known_good_prompt_ref", "")
            ),
            notes=str(data.get("notes", "")),
        )


def render_retro(retro: KillSwitchRetro) -> str:
    lines = [
        f"# Kill-switch retro — {retro.specialist_id}",
        "",
        f"**Specialist:** `{retro.specialist_id}`",
        f"**Created:** {retro.created_at}",
        "",
        "## What did you expect?",
        "",
        retro.expected.strip() or "_(not recorded)_",
        "",
        "## What did you see?",
        "",
        retro.saw.strip() or "_(not recorded)_",
        "",
        "## What would fix it?",
        "",
        retro.fix.strip() or "_(not recorded)_",
        "",
    ]
    if retro.last_known_good_prompt_ref:
        lines.extend([
            "## Prompt restored to",
            "",
            f"`{retro.last_known_good_prompt_ref}`",
            "",
        ])
    if retro.notes.strip():
        lines.extend(["## Notes", "", retro.notes.strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def retro_path(company_dir: Path, retro: KillSwitchRetro) -> Path:
    date = retro.created_at[:10]
    filename = f"{date}-{_slug(retro.specialist_id)}.md"
    return company_dir / RETROS_SUBDIR / filename


def write_retro(company_dir: Path, retro: KillSwitchRetro) -> Path:
    path = retro_path(company_dir, retro)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_retro(retro), encoding="utf-8")
    sidecar = path.with_suffix(".json")
    sidecar.write_text(
        json.dumps(retro.to_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def load_retro(path: Path) -> KillSwitchRetro:
    if path.suffix == ".md":
        path = path.with_suffix(".json")
    return KillSwitchRetro.from_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )


def iter_retros(company_dir: Path) -> list[KillSwitchRetro]:
    root = company_dir / RETROS_SUBDIR
    if not root.exists():
        return []
    out: list[KillSwitchRetro] = []
    for p in sorted(root.glob("*.json")):
        try:
            out.append(load_retro(p))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return out


def retros_since(
    retros: Iterable[KillSwitchRetro],
    *,
    specialist_id: str | None = None,
    since_iso: str | None = None,
) -> list[KillSwitchRetro]:
    """Filter a retro collection — used by the adversary to pull in
    recent kill-switch signals when convening."""
    cutoff: datetime | None = None
    if since_iso:
        cutoff = datetime.fromisoformat(since_iso)
    out: list[KillSwitchRetro] = []
    for r in retros:
        if specialist_id and r.specialist_id != specialist_id:
            continue
        if cutoff is not None:
            try:
                ts = datetime.fromisoformat(r.created_at)
            except ValueError:
                continue
            if ts < cutoff:
                continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# Drift benchmark (Chunk 12.3 — §0.5 prevention-of-drift defense #2)
# ---------------------------------------------------------------------------
# Plan §0.5: "Every 30 days (or every 10 adversary activations, whichever
# first), the founder rates the last 3 adversary outputs on a simple
# rubric: 'did this stress-test my thinking, or did it feel performative?'
# If median score drops below threshold for two consecutive periods, the
# adversary's memory is reset and its prompt body reverts to the vertical-
# pack default."
#
# Scale: 0-5 integer score. `MIN_MEDIAN` is the pass threshold.
DRIFT_WINDOW_DAYS = 30
DRIFT_WINDOW_ACTIVATIONS = 10
DRIFT_SAMPLE_SIZE = 3
MIN_MEDIAN = 3.0
CONSECUTIVE_FAILURES_TO_RESET = 2


@dataclass(frozen=True)
class AdversaryRating:
    """One founder-rating of an adversary review.

    `review_key` is typically the review file's basename (slug-date). It
    is NOT validated here — the caller is responsible for using whatever
    stable key lets downstream joins work.
    """

    review_key: str
    score: int  # 0..5 (lower = performative, higher = stress-tested)
    notes: str = ""
    created_at: str = ""

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 5:
            raise ValueError(f"score must be in [0, 5], got {self.score}")


@dataclass(frozen=True)
class DriftWindow:
    """A completed drift-benchmark period.

    A window closes when EITHER 30 days elapsed from `started_at` OR
    `DRIFT_WINDOW_ACTIVATIONS` reviews have been rated within it,
    whichever comes first (§0.5).
    """

    started_at: str
    ended_at: str
    activations: int
    rating_median: float | None  # None when no ratings collected
    passed: bool
    ratings: tuple[AdversaryRating, ...] = ()


class ResetAction(str, Enum):
    HOLD = "hold"    # no reset needed
    RESET = "reset"  # two consecutive sub-threshold windows → reset memory+prompt


@dataclass(frozen=True)
class ResetDecision:
    action: ResetAction
    reason: str
    consecutive_failures: int = 0


def build_window(
    started_at: str,
    ended_at: str,
    ratings: Sequence[AdversaryRating],
    activations: int,
    *,
    min_median: float = MIN_MEDIAN,
) -> DriftWindow:
    """Assemble a DriftWindow from a rating set.

    `passed` is True iff there is at least 1 rating AND its median is
    at or above `min_median`. Zero-rating windows pass by default
    (no signal → no alarm).
    """
    scored = [r.score for r in ratings]
    if scored:
        med = float(median(scored))
        passed = med >= min_median
    else:
        med = None
        passed = True  # no signal → hold
    return DriftWindow(
        started_at=started_at,
        ended_at=ended_at,
        activations=activations,
        rating_median=med,
        passed=passed,
        ratings=tuple(ratings),
    )


# ---------------------------------------------------------------------------
# Phase 14 §10.8 — Rating noise-resistance
# ---------------------------------------------------------------------------
# Grok-isolated attack surface (consolidated-2026-04-18 §6): gradual
# rating poisoning. A specialist whose outputs get subtly worse over time
# can walk ratings down from 5 → 4 → 3 → 3 without ever triggering a
# sub-threshold window, so `consider_reset_trigger()` never fires.
#
# Defense: track the moving-average trend AND per-window variance. A
# monotone downward trend across recent windows is suspicious regardless
# of whether any individual window failed. High variance is the opposite
# failure mode (founder-rating noise) — it tells us the signal is weak
# and we should not auto-reset on a single borderline window.

RATING_TREND_MIN_DELTA = 0.5  # median drop across lookback → suspicious
RATING_TREND_LOOKBACK = 5     # windows to look across


@dataclass(frozen=True)
class RatingTrendFlag:
    suspicious: bool
    reason: str
    trend_slope: float = 0.0      # negative = downward (bad)
    stdev: float = 0.0             # higher = noisier signal
    lookback_used: int = 0


def detect_rating_trend(
    windows: Sequence[DriftWindow],
    *,
    lookback: int = RATING_TREND_LOOKBACK,
    min_delta: float = RATING_TREND_MIN_DELTA,
) -> RatingTrendFlag:
    """Inspect the last `lookback` windows for a suspicious downward
    median trend. Returns `suspicious=True` iff the oldest and newest
    windows in the lookback both have numeric medians AND (oldest_median
    - newest_median) >= min_delta AND the median is weakly monotone
    non-increasing (at most one uptick allowed). Also computes the
    concatenated rating stdev across the lookback for signal-strength
    reasoning.

    Use alongside `consider_reset_trigger` — this primitive does NOT
    trigger a reset on its own. It surfaces the suspicion so the
    founder can investigate. Auto-action on a gaming signal would
    itself be gameable."""
    medianed = [w for w in windows if w.rating_median is not None]
    if len(medianed) < 2:
        return RatingTrendFlag(
            suspicious=False,
            reason="insufficient-windows",
            lookback_used=len(medianed),
        )
    tail = medianed[-lookback:]
    n = len(tail)
    if n < 2:
        return RatingTrendFlag(
            suspicious=False,
            reason="insufficient-lookback",
            lookback_used=n,
        )

    medians = [w.rating_median for w in tail]
    # Count upticks — allow 1, reject more.
    upticks = sum(1 for i in range(1, n) if medians[i] > medians[i - 1])
    # OLS-ish slope: (last - first) / (n - 1). Not a full regression,
    # but adequate at lookback=5. Negative = downward.
    slope = (medians[-1] - medians[0]) / (n - 1)
    drop = medians[0] - medians[-1]

    # Concatenated stdev across all ratings in the lookback.
    all_scores = [r.score for w in tail for r in w.ratings]
    stdev_val = pstdev(all_scores) if len(all_scores) >= 2 else 0.0

    if drop >= min_delta and upticks <= 1:
        return RatingTrendFlag(
            suspicious=True,
            reason=(
                f"median dropped {drop:.2f} over last {n} windows "
                f"(slope={slope:.3f}/window, upticks={upticks})"
            ),
            trend_slope=slope,
            stdev=stdev_val,
            lookback_used=n,
        )
    return RatingTrendFlag(
        suspicious=False,
        reason=(
            f"no sustained downward trend (drop={drop:.2f} < "
            f"{min_delta}, upticks={upticks})"
        ),
        trend_slope=slope,
        stdev=stdev_val,
        lookback_used=n,
    )


def consider_reset_trigger(
    windows: Sequence[DriftWindow],
    *,
    consecutive_needed: int = CONSECUTIVE_FAILURES_TO_RESET,
) -> ResetDecision:
    """Return RESET iff the last `consecutive_needed` windows all failed.

    `windows` MUST be chronological (oldest → newest). Only the tail is
    inspected; older history is irrelevant once a pass window clears.
    """
    if len(windows) < consecutive_needed:
        return ResetDecision(
            action=ResetAction.HOLD,
            reason=(
                f"only {len(windows)} completed windows; need "
                f"{consecutive_needed} consecutive failures"
            ),
            consecutive_failures=sum(1 for w in windows if not w.passed),
        )
    tail = windows[-consecutive_needed:]
    failures = sum(1 for w in tail if not w.passed)
    if failures >= consecutive_needed:
        return ResetDecision(
            action=ResetAction.RESET,
            reason=(
                f"{failures} consecutive sub-threshold windows — "
                "adversary memory + prompt reset per §0.5"
            ),
            consecutive_failures=failures,
        )
    return ResetDecision(
        action=ResetAction.HOLD,
        reason=(
            f"last {consecutive_needed} windows had {failures} failures — "
            "below threshold"
        ),
        consecutive_failures=failures,
    )


def should_close_window(
    started_at: str,
    *,
    activations: int,
    now: datetime | None = None,
    window_days: int = DRIFT_WINDOW_DAYS,
    window_activations: int = DRIFT_WINDOW_ACTIVATIONS,
) -> bool:
    """True when either threshold (time or activations) is met."""
    if activations >= window_activations:
        return True
    ref = now if now is not None else datetime.now(tz=timezone.utc)
    start = datetime.fromisoformat(started_at)
    elapsed_days = (ref - start).days
    return elapsed_days >= window_days


# ---------------------------------------------------------------------------
# Ratings log (JSONL — append-only)
# ---------------------------------------------------------------------------
def ratings_log_path(company_dir: Path) -> Path:
    return company_dir / RATINGS_FILENAME


def append_rating(company_dir: Path, rating: AdversaryRating) -> Path:
    path = ratings_log_path(company_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "review_key": rating.review_key,
        "score": rating.score,
        "notes": rating.notes,
        "created_at": rating.created_at or _now_iso(),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, sort_keys=True))
        fh.write("\n")
    return path


def load_ratings(company_dir: Path) -> list[AdversaryRating]:
    path = ratings_log_path(company_dir)
    if not path.exists():
        return []
    out: list[AdversaryRating] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        try:
            out.append(AdversaryRating(
                review_key=str(data["review_key"]),
                score=int(data["score"]),
                notes=str(data.get("notes", "")),
                created_at=str(data.get("created_at", "")),
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return out
