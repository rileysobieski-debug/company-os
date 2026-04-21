"""Business interview — schema + validators + file writers (Phase 8.1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.onboarding.business_interview import (
    INTERVIEW_QUESTIONS,
    MAX_PRIORITY_STACK,
    MIN_PRIORITY_STACK,
    InterviewPhase,
    InterviewWriteResult,
    build_config_payload,
    render_context_md,
    render_domain_md,
    render_founder_profile_md,
    render_priorities_md,
    validate_answers,
    write_interview_files,
)


def _complete_answers() -> dict:
    return {
        "company_name": "Old Press Wine Company LLC",
        "industry": "wine / beverage",
        "geography": "Maine (Ironbound Island base); SW Virginia narrative-only",
        "team_size": "2",
        "revenue_status": "pre-revenue",
        "twelve_month_vision": "First vintage from coastal Maine released at small scale.",
        "five_year_vision": "Established brand with loyal buyer list and working capital.",
        "refusals": ["Will not take VC money", "Will not dilute the coastal identity"],
        "budget": "$500/mo operating",
        "timeline": "First label by Q3 2026",
        "regulatory": "TTB alternating-proprietor path; Maine liquor licensing",
        "hard_rules": ["No selling through distributors in Year 1"],
        "founder_role": "sole founder",
        "founder_background": "Wine industry veteran with background in direct-to-consumer.",
        "founder_bandwidth": "5–10 hours per week",
        "decision_style": "Present options; escalate only genuine deadlocks.",
        "settled_convictions": [
            "Coastal Maine is the operational base",
            "Quiet-abundance brand stance; no public founder",
        ],
        "premortem_cause": "Ran out of cash before first vintage; W-2 fell through.",
        "priority_stack": [
            "Secure W-2 income in Maine",
            "Finish TTB alternating-proprietor paperwork",
            "Draft brand and positioning doc",
            "Build small pre-order list",
        ],
    }


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
def test_seven_phases_covered() -> None:
    phases_seen = {q.phase for q in INTERVIEW_QUESTIONS}
    assert phases_seen == set(InterviewPhase)


def test_premortem_question_exists() -> None:
    premortem = [q for q in INTERVIEW_QUESTIONS if q.id == "premortem_cause"]
    assert len(premortem) == 1
    assert premortem[0].phase is InterviewPhase.PREMORTEM
    assert premortem[0].required is True


def test_priority_stack_question_is_list() -> None:
    q = [q for q in INTERVIEW_QUESTIONS if q.id == "priority_stack"][0]
    assert q.is_list is True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def test_validate_answers_happy_path() -> None:
    result = validate_answers(_complete_answers())
    assert result.ok is True
    assert result.issues == ()


def test_validate_flags_missing_required() -> None:
    answers = _complete_answers()
    del answers["company_name"]
    result = validate_answers(answers)
    assert result.ok is False
    assert any(i.question_id == "company_name" for i in result.issues)


def test_validate_flags_missing_premortem() -> None:
    answers = _complete_answers()
    answers["premortem_cause"] = ""
    result = validate_answers(answers)
    assert result.ok is False
    assert any(i.question_id == "premortem_cause" for i in result.issues)


def test_validate_optional_refusals_may_be_empty() -> None:
    answers = _complete_answers()
    answers["refusals"] = []
    result = validate_answers(answers)
    assert result.ok is True


def test_validate_priority_stack_min_and_max() -> None:
    answers = _complete_answers()
    answers["priority_stack"] = ["only one"]
    r1 = validate_answers(answers)
    assert not r1.ok
    assert any(
        i.question_id == "priority_stack"
        and f"at least {MIN_PRIORITY_STACK}" in i.message
        for i in r1.issues
    )

    answers["priority_stack"] = [f"p{i}" for i in range(MAX_PRIORITY_STACK + 1)]
    r2 = validate_answers(answers)
    assert not r2.ok
    assert any(
        i.question_id == "priority_stack"
        and f"at most {MAX_PRIORITY_STACK}" in i.message
        for i in r2.issues
    )


def test_validate_priority_stack_must_be_list() -> None:
    answers = _complete_answers()
    answers["priority_stack"] = "not a list"
    result = validate_answers(answers)
    assert not result.ok
    assert any("must be a list" in i.message for i in result.issues)


# ---------------------------------------------------------------------------
# Config payload
# ---------------------------------------------------------------------------
def test_build_config_payload_copies_core_fields() -> None:
    payload = build_config_payload(_complete_answers())
    assert payload["company_name"] == "Old Press Wine Company LLC"
    assert payload["industry"] == "wine / beverage"
    assert payload["revenue_status"] == "pre-revenue"
    assert len(payload["priorities"]) == 4
    assert "created_at" in payload
    assert payload["premortem"].startswith("Ran out of cash")


def test_build_config_payload_accepts_active_departments() -> None:
    payload = build_config_payload(
        _complete_answers(), active_departments=["finance", "operations", "marketing"],
    )
    assert payload["active_departments"] == ["finance", "operations", "marketing"]


def test_build_config_payload_default_company_id_slugified() -> None:
    payload = build_config_payload(_complete_answers())
    assert payload["company_id"].startswith("old-press")
    assert " " not in payload["company_id"]


def test_build_config_payload_respects_explicit_company_id() -> None:
    payload = build_config_payload(_complete_answers(), company_id="custom-slug")
    assert payload["company_id"] == "custom-slug"


def test_build_config_payload_hard_constraints_from_hard_rules() -> None:
    payload = build_config_payload(_complete_answers())
    assert "No selling through distributors in Year 1" in payload["hard_constraints"]


# ---------------------------------------------------------------------------
# Markdown renderers
# ---------------------------------------------------------------------------
def test_render_context_md_includes_vision_and_refusals() -> None:
    md = render_context_md(_complete_answers())
    assert "12 months" in md
    assert "5 years" in md
    assert "Will not take VC money" in md


def test_render_priorities_md_marks_top_3_active() -> None:
    md = render_priorities_md(_complete_answers())
    lines = [l for l in md.splitlines() if "[ACTIVE]" in l]
    assert len(lines) == 3


def test_render_founder_profile_md_contains_premortem_block() -> None:
    md = render_founder_profile_md(_complete_answers())
    assert "Pre-mortem" in md
    assert "Ran out of cash" in md
    assert "§0.5" in md  # load-bearing annotation


def test_render_founder_profile_md_lists_convictions() -> None:
    md = render_founder_profile_md(_complete_answers())
    assert "Coastal Maine is the operational base" in md
    assert "DO NOT RE-EXAMINE" in md


def test_render_domain_md_shows_regulatory_and_hard_rules() -> None:
    md = render_domain_md(_complete_answers())
    assert "TTB" in md
    assert "No selling through distributors" in md


# ---------------------------------------------------------------------------
# Composed writer
# ---------------------------------------------------------------------------
def test_write_interview_files_creates_six_files(tmp_path: Path) -> None:
    result = write_interview_files(
        tmp_path, _complete_answers(),
        active_departments=["finance", "marketing", "operations"],
    )
    assert isinstance(result, InterviewWriteResult)
    for attr in (
        "config_path", "context_path", "priorities_path",
        "founder_profile_path", "domain_path", "state_authority_path",
    ):
        assert getattr(result, attr).exists(), f"{attr} was not written"


def test_write_interview_files_config_roundtrips_json(tmp_path: Path) -> None:
    result = write_interview_files(
        tmp_path, _complete_answers(), active_departments=["marketing"],
    )
    payload = json.loads(result.config_path.read_text(encoding="utf-8"))
    assert payload["company_name"] == "Old Press Wine Company LLC"
    assert payload["active_departments"] == ["marketing"]


def test_write_interview_files_rejects_incomplete_answers(tmp_path: Path) -> None:
    answers = _complete_answers()
    del answers["premortem_cause"]
    with pytest.raises(ValueError, match="premortem_cause"):
        write_interview_files(tmp_path, answers)


def test_write_interview_files_writes_state_authority_doc(tmp_path: Path) -> None:
    result = write_interview_files(
        tmp_path, _complete_answers(), active_departments=["marketing"],
    )
    sa = result.state_authority_path.read_text(encoding="utf-8")
    assert "Priority" in sa
