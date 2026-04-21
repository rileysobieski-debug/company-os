"""Evaluator: rubric + verdict + autoresearch trigger (Phase 7.2)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.dispatch.evaluator import (
    AUTORESEARCH_MAJOR_FAILURES,
    CriterionResult,
    RubricCriterion,
    TriggerAction,
    Verdict,
    VerdictStatus,
    consider_autoresearch_trigger,
    evaluate_output,
    load_recent_verdicts,
    load_verdict,
    record_verdict,
)

ISO = "2026-04-17T10:00:00+00:00"


# ---------------------------------------------------------------------------
# evaluate_output — status logic
# ---------------------------------------------------------------------------
def test_pass_when_every_criterion_scores_above_min() -> None:
    def judge(c, _brief, _output):
        return CriterionResult(criterion_id=c.id, score=0.9, passed=True, comment="ok")

    rubric = [
        RubricCriterion(id="clarity", min_score=0.7, weight=1.0),
        RubricCriterion(id="relevance", min_score=0.7, weight=2.0),
    ]
    verdict = evaluate_output(
        brief="b", output="o", rubric=rubric,
        specialist_id="s", skill_id="k", session_id="sess",
        judge=judge, now=ISO,
    )
    assert verdict.status is VerdictStatus.PASS
    assert verdict.total_score == pytest.approx(0.9)


def test_fail_when_any_criterion_below_min() -> None:
    def judge(c, _brief, _output):
        score = 0.5 if c.id == "clarity" else 0.9
        return CriterionResult(
            criterion_id=c.id, score=score, passed=score >= c.min_score,
        )

    rubric = [
        RubricCriterion(id="clarity", min_score=0.7),
        RubricCriterion(id="relevance", min_score=0.7),
    ]
    verdict = evaluate_output(
        brief="b", output="o", rubric=rubric,
        specialist_id="s", skill_id="k", session_id="sess",
        judge=judge,
    )
    assert verdict.status is VerdictStatus.FAIL


def test_needs_founder_review_on_max_iterations_hit() -> None:
    def judge(c, *_):
        return CriterionResult(criterion_id=c.id, score=1.0, passed=True)

    rubric = [RubricCriterion(id="a", min_score=0.5)]
    verdict = evaluate_output(
        brief="b", output="o", rubric=rubric,
        specialist_id="s", skill_id="k", session_id="sess",
        max_iterations_hit=True,
        judge=judge,
    )
    assert verdict.status is VerdictStatus.NEEDS_FOUNDER_REVIEW


def test_fail_beats_needs_review() -> None:
    """Hard-gate rule: any criterion fail → FAIL even if max_iterations_hit."""
    def judge(c, *_):
        return CriterionResult(criterion_id=c.id, score=0.1, passed=False)

    rubric = [RubricCriterion(id="a", min_score=0.5)]
    verdict = evaluate_output(
        brief="b", output="o", rubric=rubric,
        specialist_id="s", skill_id="k", session_id="sess",
        max_iterations_hit=True, judge=judge,
    )
    assert verdict.status is VerdictStatus.FAIL


def test_weighted_total_score() -> None:
    def judge(c, *_):
        score = {"a": 1.0, "b": 0.0}[c.id]
        return CriterionResult(criterion_id=c.id, score=score, passed=score >= c.min_score)

    rubric = [
        RubricCriterion(id="a", min_score=0.0, weight=3.0),
        RubricCriterion(id="b", min_score=0.0, weight=1.0),
    ]
    verdict = evaluate_output(
        brief="b", output="o", rubric=rubric,
        specialist_id="s", skill_id="k", session_id="sess",
        judge=judge,
    )
    assert verdict.total_score == pytest.approx(0.75)


def test_empty_rubric_raises() -> None:
    with pytest.raises(ValueError, match="at least one criterion"):
        evaluate_output(
            brief="b", output="o", rubric=[],
            specialist_id="s", skill_id="k", session_id="sess",
        )


def test_default_judge_keyword_match() -> None:
    """When no LLM judge is supplied, the keyword-presence fallback runs."""
    rubric = [RubricCriterion(id="positioning", min_score=0.5)]
    v_pass = evaluate_output(
        brief="b", output="our positioning is clear",
        rubric=rubric,
        specialist_id="s", skill_id="k", session_id="sess",
    )
    v_fail = evaluate_output(
        brief="b", output="nothing to see here",
        rubric=rubric,
        specialist_id="s", skill_id="k", session_id="sess",
    )
    assert v_pass.status is VerdictStatus.PASS
    assert v_fail.status is VerdictStatus.FAIL


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _fail_verdict(
    *, specialist_id: str = "spec-1", skill_id: str = "skill-a",
    session_id: str = "s1", ts: str = ISO,
) -> Verdict:
    return Verdict(
        specialist_id=specialist_id, skill_id=skill_id,
        session_id=session_id, ts=ts,
        status=VerdictStatus.FAIL, total_score=0.2,
        criterion_results=(
            CriterionResult(criterion_id="x", score=0.2, passed=False),
        ),
    )


def _pass_verdict(**kw) -> Verdict:
    base = _fail_verdict(**kw)
    return Verdict(
        specialist_id=base.specialist_id, skill_id=base.skill_id,
        session_id=base.session_id, ts=base.ts,
        status=VerdictStatus.PASS, total_score=0.95,
        criterion_results=(
            CriterionResult(criterion_id="x", score=0.95, passed=True),
        ),
    )


def test_record_and_load_verdict_roundtrip(tmp_path: Path) -> None:
    v = _pass_verdict()
    path = record_verdict(tmp_path, v)
    assert path.exists()
    loaded = load_verdict(path)
    assert loaded == v


def test_verdict_path_includes_date(tmp_path: Path) -> None:
    path = record_verdict(tmp_path, _pass_verdict())
    assert "2026-04-17" in str(path)


def test_load_verdict_returns_none_on_malformed(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("not json", encoding="utf-8")
    assert load_verdict(p) is None


# ---------------------------------------------------------------------------
# Autoresearch trigger
# ---------------------------------------------------------------------------
def test_trigger_declines_when_few_failures() -> None:
    verdicts = [_pass_verdict(ts=f"2026-04-17T10:0{i}:00+00:00") for i in range(5)]
    verdicts.append(_fail_verdict(ts="2026-04-17T10:10:00+00:00"))
    decision = consider_autoresearch_trigger(
        specialist_id="spec-1",
        recent_verdicts=verdicts,
        monthly_budget_remaining=100.0,
        autoresearch_cost_estimate=5.0,
    )
    assert decision.action is TriggerAction.DECLINE


def test_trigger_approves_on_threshold_failures() -> None:
    verdicts = [
        _fail_verdict(ts=f"2026-04-17T10:0{i}:00+00:00", skill_id=f"k{i}")
        for i in range(AUTORESEARCH_MAJOR_FAILURES)
    ]
    decision = consider_autoresearch_trigger(
        specialist_id="spec-1",
        recent_verdicts=verdicts,
        monthly_budget_remaining=100.0,
        autoresearch_cost_estimate=5.0,
    )
    assert decision.action is TriggerAction.APPROVE
    assert decision.failures_in_window == AUTORESEARCH_MAJOR_FAILURES


def test_trigger_approves_on_skill_pattern() -> None:
    """Even below the 3-failure threshold in general — wait, pattern IS
    the 3-per-skill rule. Test a case where multiple skills fail but
    one skill dominates."""
    verdicts = [
        _fail_verdict(ts=f"2026-04-17T10:0{i}:00+00:00", skill_id="bad-skill")
        for i in range(3)
    ]
    decision = consider_autoresearch_trigger(
        specialist_id="spec-1",
        recent_verdicts=verdicts,
        monthly_budget_remaining=100.0,
        autoresearch_cost_estimate=5.0,
    )
    assert decision.action is TriggerAction.APPROVE
    assert decision.skill_pattern_count == 3


def test_trigger_defers_when_budget_exhausted() -> None:
    verdicts = [
        _fail_verdict(ts=f"2026-04-17T10:0{i}:00+00:00", skill_id=f"k{i}")
        for i in range(AUTORESEARCH_MAJOR_FAILURES)
    ]
    decision = consider_autoresearch_trigger(
        specialist_id="spec-1",
        recent_verdicts=verdicts,
        monthly_budget_remaining=2.0,
        autoresearch_cost_estimate=5.0,
    )
    assert decision.action is TriggerAction.DEFER
    assert "budget" in decision.reason.lower()


def test_trigger_filters_by_specialist() -> None:
    """Failures on OTHER specialists don't contribute to this one's trigger."""
    other = [
        _fail_verdict(ts=f"2026-04-17T10:0{i}:00+00:00", specialist_id="other")
        for i in range(5)
    ]
    decision = consider_autoresearch_trigger(
        specialist_id="spec-1",
        recent_verdicts=other,
        monthly_budget_remaining=100.0,
        autoresearch_cost_estimate=5.0,
    )
    assert decision.action is TriggerAction.DECLINE


def test_trigger_respects_lookback_window() -> None:
    """Old failures outside the 10-dispatch window shouldn't count."""
    # 20 verdicts, first 5 are fails on the same skill, rest pass.
    verdicts = [
        _fail_verdict(ts=f"2026-04-17T09:0{i}:00+00:00", skill_id="bad")
        for i in range(5)
    ]
    verdicts += [
        _pass_verdict(ts=f"2026-04-17T11:{i:02d}:00+00:00")
        for i in range(15)
    ]
    decision = consider_autoresearch_trigger(
        specialist_id="spec-1",
        recent_verdicts=verdicts,
        monthly_budget_remaining=100.0,
        autoresearch_cost_estimate=5.0,
    )
    # The 5 fails are outside the last-10 window → decline.
    assert decision.action is TriggerAction.DECLINE


# ---------------------------------------------------------------------------
# load_recent_verdicts
# ---------------------------------------------------------------------------
def test_load_recent_verdicts_from_disk(tmp_path: Path) -> None:
    v1 = _fail_verdict(ts="2026-04-17T09:00:00+00:00")
    v2 = _pass_verdict(ts="2026-04-17T10:00:00+00:00")
    record_verdict(tmp_path, v1)
    record_verdict(tmp_path, v2)
    loaded = load_recent_verdicts(tmp_path, "spec-1")
    assert [v.ts for v in loaded] == [v1.ts, v2.ts]


def test_load_recent_verdicts_filters_specialist(tmp_path: Path) -> None:
    v1 = _fail_verdict(specialist_id="spec-1")
    v2 = _fail_verdict(specialist_id="spec-2", ts="2026-04-17T11:00:00+00:00")
    record_verdict(tmp_path, v1)
    record_verdict(tmp_path, v2)
    loaded = load_recent_verdicts(tmp_path, "spec-2")
    assert len(loaded) == 1
    assert loaded[0].specialist_id == "spec-2"
