"""
core/dispatch/evaluator.py — Rubric-based verdict + autoresearch trigger
=======================================================================
The evaluator is the LLM judge described in plan §9 (role row
"LLM judge — evaluator, watchdog" — Haiku) and the autoresearch trigger
authority described in §8.1 ("Trigger authority belongs solely to the
evaluator agent").

This module ships the pure primitives; the Haiku judge call itself is
passed in as an optional `judge` callable so tests can run deterministic
rubric-only scoring without an API key. Phase 11 wires the trigger
decision into the full autoresearch runner.

Verdict file layout:
    <company>/evaluations/<YYYY-MM-DD>/<session>-<specialist>-<ts>.json

Autoresearch trigger (§8.1 revised):
    - 3+ major failures in last 10 dispatches → approve
    - Pattern of repeated failure on same skill → approve
    - Cost envelope remaining for the month → may defer
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping

EVALUATIONS_SUBDIR = "evaluations"
AUTORESEARCH_LOOKBACK = 10          # §8.1 — "last 10 dispatches"
AUTORESEARCH_MAJOR_FAILURES = 3     # §8.1 — "3+ major failures"
AUTORESEARCH_SKILL_PATTERN_MIN = 3  # same skill failing N times → pattern


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------
class VerdictStatus(Enum):
    PASS = "pass"
    NEEDS_FOUNDER_REVIEW = "needs_founder_review"
    FAIL = "fail"


@dataclass(frozen=True)
class RubricCriterion:
    """One dimension the evaluator scores against.

    `min_score` is the pass threshold for this criterion in isolation;
    a criterion scoring below its `min_score` is counted as a failure
    regardless of the weighted total (§Phase 7 fail-any-hard-gate rule).
    `weight` contributes to the weighted total score.
    """

    id: str
    min_score: float  # [0, 1]
    weight: float = 1.0
    description: str = ""


@dataclass(frozen=True)
class CriterionResult:
    criterion_id: str
    score: float
    passed: bool
    comment: str = ""


@dataclass(frozen=True)
class Verdict:
    specialist_id: str
    skill_id: str
    session_id: str
    ts: str                          # ISO-8601 UTC
    status: VerdictStatus
    total_score: float               # weighted [0, 1]
    criterion_results: tuple[CriterionResult, ...] = field(default_factory=tuple)
    max_iterations_hit: bool = False
    notes: str = ""


class TriggerAction(Enum):
    APPROVE = "approve"
    DEFER = "defer"
    DECLINE = "decline"


@dataclass(frozen=True)
class TriggerDecision:
    action: TriggerAction
    reason: str
    failures_in_window: int = 0
    skill_pattern_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


ScoreFn = Callable[[RubricCriterion, str, str], CriterionResult]
"""LLM-judge signature: given (criterion, brief, output) return a CriterionResult."""


def _default_score_fn(
    criterion: RubricCriterion, brief: str, output: str
) -> CriterionResult:
    """Deterministic fallback when no LLM judge is supplied.

    The rubric just checks whether the criterion's `id` appears as a
    token in the output (case-insensitive). It's a stub — real deployments
    always pass a Haiku-backed `judge` callable — but the shape lets tests
    exercise status transitions and autoresearch logic without mocking
    an LLM client.
    """
    hit = criterion.id.lower() in output.lower()
    score = 1.0 if hit else 0.0
    return CriterionResult(
        criterion_id=criterion.id,
        score=score,
        passed=score >= criterion.min_score,
        comment="keyword present" if hit else "keyword absent",
    )


# ---------------------------------------------------------------------------
# Evaluate
# ---------------------------------------------------------------------------
def _status_for(results: list[CriterionResult], max_iterations_hit: bool) -> VerdictStatus:
    """Translate criterion results + partial-output flag into a status.

    Rules (Phase 7):
      - Any criterion fail → FAIL (hard gate).
      - max_iterations_hit=True → NEEDS_FOUNDER_REVIEW (§4.1 constraint 5).
      - Otherwise → PASS.
    """
    if any(not r.passed for r in results):
        return VerdictStatus.FAIL
    if max_iterations_hit:
        return VerdictStatus.NEEDS_FOUNDER_REVIEW
    return VerdictStatus.PASS


def evaluate_output(
    *,
    brief: str,
    output: str,
    rubric: Iterable[RubricCriterion],
    specialist_id: str,
    skill_id: str,
    session_id: str,
    max_iterations_hit: bool = False,
    judge: ScoreFn | None = None,
    now: str | None = None,
) -> Verdict:
    """Score `output` against `rubric` and return a Verdict.

    `judge` is the scoring callable. When None, the stub rubric scorer
    runs (keyword-presence per criterion id) — useful for tests and for
    deployments that don't have a judge wired in yet. Production callers
    pass a Haiku-backed scorer.
    """
    rubric_list = list(rubric)
    if not rubric_list:
        raise ValueError("evaluate_output: rubric must have at least one criterion")

    scorer = judge or _default_score_fn
    results: list[CriterionResult] = []
    weighted_sum = 0.0
    weight_total = 0.0
    for criterion in rubric_list:
        raw = scorer(criterion, brief, output)
        score = _clamp01(raw.score)
        passed = score >= criterion.min_score
        results.append(
            CriterionResult(
                criterion_id=criterion.id,
                score=score,
                passed=passed,
                comment=raw.comment,
            )
        )
        weighted_sum += score * criterion.weight
        weight_total += criterion.weight

    total_score = weighted_sum / weight_total if weight_total > 0 else 0.0
    status = _status_for(results, max_iterations_hit)

    return Verdict(
        specialist_id=specialist_id,
        skill_id=skill_id,
        session_id=session_id,
        ts=now or _now_iso(),
        status=status,
        total_score=_clamp01(total_score),
        criterion_results=tuple(results),
        max_iterations_hit=max_iterations_hit,
        notes="",
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _verdict_path(company_dir: Path, verdict: Verdict) -> Path:
    day = verdict.ts.split("T", 1)[0] if "T" in verdict.ts else verdict.ts[:10]
    filename_ts = verdict.ts.replace(":", "-").replace("+", "-plus-")
    return (
        company_dir
        / EVALUATIONS_SUBDIR
        / day
        / f"{verdict.session_id}-{verdict.specialist_id}-{filename_ts}.json"
    )


def record_verdict(company_dir: Path, verdict: Verdict) -> Path:
    """Persist a Verdict to disk. Returns the path.

    Idempotent per (session_id, specialist_id, ts) — same Verdict written
    twice produces the same bytes."""
    path = _verdict_path(company_dir, verdict)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(verdict)
    payload["status"] = verdict.status.value
    payload["criterion_results"] = [asdict(r) for r in verdict.criterion_results]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_verdict(path: Path) -> Verdict | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        status = VerdictStatus(raw["status"])
    except (KeyError, ValueError):
        return None
    crits = []
    for r in raw.get("criterion_results") or []:
        if not isinstance(r, dict):
            continue
        crits.append(CriterionResult(
            criterion_id=str(r.get("criterion_id", "")),
            score=float(r.get("score", 0.0)),
            passed=bool(r.get("passed", False)),
            comment=str(r.get("comment", "")),
        ))
    try:
        return Verdict(
            specialist_id=str(raw["specialist_id"]),
            skill_id=str(raw["skill_id"]),
            session_id=str(raw["session_id"]),
            ts=str(raw["ts"]),
            status=status,
            total_score=float(raw.get("total_score", 0.0)),
            criterion_results=tuple(crits),
            max_iterations_hit=bool(raw.get("max_iterations_hit", False)),
            notes=str(raw.get("notes", "")),
        )
    except KeyError:
        return None


# ---------------------------------------------------------------------------
# Autoresearch trigger (§8.1 revised)
# ---------------------------------------------------------------------------
def consider_autoresearch_trigger(
    *,
    specialist_id: str,
    recent_verdicts: Iterable[Verdict],
    monthly_budget_remaining: float,
    autoresearch_cost_estimate: float,
) -> TriggerDecision:
    """Pure: decide whether to approve/defer/decline an autoresearch run
    for `specialist_id` given the specialist's recent verdicts and the
    remaining monthly cost envelope.

    Rules (§8.1):
      - Look at the most recent AUTORESEARCH_LOOKBACK verdicts for the
        specialist.
      - 3+ FAIL verdicts → candidate for approval.
      - ≥ 3 FAILs on the same skill → candidate for approval (pattern).
      - If neither triggers, decline.
      - If either triggers BUT `monthly_budget_remaining <
        autoresearch_cost_estimate`, defer.
    """
    verdicts = [
        v for v in recent_verdicts
        if v.specialist_id == specialist_id
    ]
    # Keep only the last N (callers can pass any slice; we re-clamp).
    verdicts = list(verdicts)[-AUTORESEARCH_LOOKBACK:]
    fails = [v for v in verdicts if v.status is VerdictStatus.FAIL]
    failure_count = len(fails)

    skill_counter: Counter[str] = Counter(v.skill_id for v in fails)
    top_skill, top_n = (skill_counter.most_common(1) or [("", 0)])[0]

    approve = (
        failure_count >= AUTORESEARCH_MAJOR_FAILURES
        or top_n >= AUTORESEARCH_SKILL_PATTERN_MIN
    )
    if not approve:
        return TriggerDecision(
            action=TriggerAction.DECLINE,
            reason=(
                f"{failure_count} failures in last {len(verdicts)} verdicts; "
                f"top skill pattern {top_n} — below thresholds"
            ),
            failures_in_window=failure_count,
            skill_pattern_count=top_n,
        )

    if monthly_budget_remaining < autoresearch_cost_estimate:
        return TriggerDecision(
            action=TriggerAction.DEFER,
            reason=(
                f"would trigger (failures={failure_count}, "
                f"skill_pattern={top_n} on {top_skill!r}) but budget remaining "
                f"${monthly_budget_remaining:.2f} < estimate ${autoresearch_cost_estimate:.2f}"
            ),
            failures_in_window=failure_count,
            skill_pattern_count=top_n,
        )

    return TriggerDecision(
        action=TriggerAction.APPROVE,
        reason=(
            f"approve (failures={failure_count}, "
            f"skill_pattern={top_n} on {top_skill!r})"
        ),
        failures_in_window=failure_count,
        skill_pattern_count=top_n,
    )


def load_recent_verdicts(
    company_dir: Path, specialist_id: str, limit: int = AUTORESEARCH_LOOKBACK
) -> list[Verdict]:
    """Walk the evaluations/ dir and return the most recent `limit`
    verdicts for `specialist_id`, chronological order."""
    eval_dir = company_dir / EVALUATIONS_SUBDIR
    if not eval_dir.exists():
        return []
    hits: list[Verdict] = []
    for day_dir in sorted(eval_dir.iterdir()):
        if not day_dir.is_dir():
            continue
        for path in sorted(day_dir.glob(f"*-{specialist_id}-*.json")):
            v = load_verdict(path)
            if v is not None and v.specialist_id == specialist_id:
                hits.append(v)
    hits.sort(key=lambda v: v.ts)
    return hits[-limit:]
